"""Server-owned native jobs with atomic deduplication and bounded volatile retention."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from clink.contracts import BackendError
from clink.native_contracts import FrozenNativeContext, NativeRunOutcome, NativeRuntime

logger = logging.getLogger("clink.tasks")
MAX_TASKS = 64
MAX_ATTEMPTS = 16
MAX_RETAINED_BYTES = 8 * 1024 * 1024
RESULT_CHARACTERS = 20000
RESULT_METADATA_BYTES = 8192
# A JSON control character needs six bytes even when UTF-8 itself needs only one.
_COMPLETION_RESERVATION_BYTES = RESULT_CHARACTERS * 6 + RESULT_METADATA_BYTES + 4096
TERMINAL_TTL_SECONDS = 3 * 60 * 60
DEADLINE_SECONDS = 1800
PROMPT_BYTES = 1024 * 1024
_ACTIVE = frozenset({"queued", "running"})
_TERMINAL = frozenset({"completed", "permission_needed", "failed", "cancelled", "uncertain"})
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ERROR_CODES = frozenset(
    {
        "authentication_required",
        "cancelled",
        "cli_missing",
        "invalid_config",
        "input_limit",
        "missing_environment",
        "policy_unverified",
        "recursion_blocked",
        "native_unavailable",
        "native_error",
        "native_session_mismatch",
        "nonzero_exit",
        "output_limit",
        "parse_error",
        "path_not_allowed",
        "permission_needed",
        "queue_full",
        "queue_timeout",
        "rate_limited",
        "session_unavailable",
        "timeout",
        "unsupported_capability",
        "unsupported_platform",
        "verification_required",
    }
)
# Admission exceptions may contain private diagnostics; publish only code-indexed text.
_PREPARE_DIAGNOSTICS = {
    "path_not_allowed": (
        "Native project_dir and context paths must be absolute, accessible, and within the configured allowed_roots.",
        "Supply project_dir and context paths within an allowed_roots entry; inspect listmodels for the selected "
        "client's allowed_roots. To authorize another workspace, update allowed_roots in the operator-owned CLI "
        "manifest using `clink allow /absolute/workspace --cli NAME --yes` (replace NAME with the selected CLI; "
        "include `--project /bound/project` for a bound server), then restart the server.",
    )
}
# Native output is untrusted. Public metadata must not become a second raw-stream channel.
_METADATA_KEYS = frozenset(
    {
        "actual_model",
        "duration_seconds",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
        "exit_status",
        "stdout_bytes",
        "stderr_bytes",
        "queue_seconds",
        "verification_status",
        "configuration_sources",
        "missing_environment_names",
        "environment_names",
        "permission_denial_count",
        "tool_error_count",
        "truncated",
        "read_scope",
        "repository_instructions",
        "cleanup_scope",
    }
)


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


@dataclass
class _Attempt:
    attempt_id: str
    state: str
    admitted_at: float
    started_at: float | None = None
    terminal_at: float | None = None
    possible_partial_effects: bool = False
    cancel_requested: bool = False
    error_code: str | None = None


@dataclass
class _TaskRecord:
    task_handle: str
    context: FrozenNativeContext
    attempts: list[_Attempt] = field(default_factory=list)
    session_id: str | None = None
    last_terminal_at: float | None = None
    latest_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class _KeyEntry:
    fingerprint: str
    task_handle: str
    attempt_id: str


class NativeTaskManager:
    """Own each native input until cleanup; no prompt bodies or PAL history enter records."""

    def __init__(
        self,
        runtime: NativeRuntime,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_tasks: int = MAX_TASKS,
        max_attempts: int = MAX_ATTEMPTS,
        max_retained_bytes: int = MAX_RETAINED_BYTES,
        terminal_ttl: float = TERMINAL_TTL_SECONDS,
    ) -> None:
        self.runtime = runtime
        self._clock = clock
        self.max_tasks = min(max_tasks, MAX_TASKS)
        self.max_attempts = min(max_attempts, MAX_ATTEMPTS)
        self.max_retained_bytes = min(max_retained_bytes, MAX_RETAINED_BYTES)
        self.terminal_ttl = min(terminal_ttl, TERMINAL_TTL_SECONDS)
        if min(self.max_tasks, self.max_attempts, self.max_retained_bytes, self.terminal_ttl) <= 0:
            raise ValueError("Native task limits must be positive.")
        self._records: dict[str, _TaskRecord] = {}
        self._keys: dict[str, _KeyEntry] = {}
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._leases: dict[str, Path] = {}
        self._session_owners: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @staticmethod
    def _validate(args: dict[str, Any], action: str) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise BackendError("invalid_config", "Native task arguments must be an object.")
        fields = {"action", "task_handle"}
        if action in {"start", "followup"}:
            fields |= {
                "cli_name",
                "role",
                "model",
                "project_dir",
                "prompt",
                "files",
                "absolute_file_paths",
                "images",
                "request_key",
                "timeout_seconds",
            }
        if set(args) - fields or (args.get("action") is not None and args["action"] != action):
            raise BackendError("invalid_config", "Unsupported arguments for this native task action.")
        value = dict(args)
        value.pop("action", None)
        if action != "start":
            handle = value.get("task_handle")
            if not isinstance(handle, str) or not re.fullmatch(r"nt_[a-f0-9]{32}", handle):
                raise BackendError("invalid_config", "A valid native task handle is required.")
        elif "task_handle" in value:
            raise BackendError("invalid_config", "Start does not accept an existing task handle.")
        if action not in {"start", "followup"}:
            return value
        if "absolute_file_paths" in value:
            if "files" in value:
                raise BackendError("invalid_config", "Supply native context paths through one field only.")
            value["files"] = value.pop("absolute_file_paths")
        timeout = value.get("timeout_seconds", DEADLINE_SECONDS)
        try:
            valid_timeout = (
                not isinstance(timeout, bool)
                and isinstance(timeout, (int, float))
                and math.isfinite(timeout)
                and 1 <= timeout <= DEADLINE_SECONDS
            )
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise BackendError("invalid_config", "Native timeout_seconds must be a finite number from 1 to 1800.")
        value["timeout_seconds"] = float(timeout)
        key = value.get("request_key")
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise BackendError("invalid_config", "A request_key of 1 to 128 ASCII identifier characters is required.")
        prompt = value.get("prompt")
        try:
            valid_prompt = (
                isinstance(prompt, str) and bool(prompt.strip()) and len(prompt.encode("utf-8")) <= PROMPT_BYTES
            )
        except UnicodeEncodeError:
            valid_prompt = False
        if not valid_prompt:
            raise BackendError("invalid_config", "A nonempty native task prompt within the 1 MiB limit is required.")
        for name in ("cli_name", "role"):
            selection = value.get(name)
            if selection is not None and (not isinstance(selection, str) or not _NAME.fullmatch(selection)):
                raise BackendError("invalid_config", "Native CLI and role names must be bounded identifiers.")
        if action == "start" and not value.get("cli_name"):
            raise BackendError("invalid_config", "Start requires explicit cli_name selection.")
        model = value.get("model")
        if model is not None and (
            not isinstance(model, str)
            or not model
            or model != model.strip()
            or len(model) > 256
            or model.startswith("-")
            or any(ord(char) < 32 or ord(char) == 127 for char in model)
        ):
            raise BackendError("invalid_config", "The native model selection is invalid.")
        project = value.get("project_dir")
        if action == "start" or project is not None:
            if not isinstance(project, str) or len(project) > 4096 or not Path(project).is_absolute():
                raise BackendError("path_not_allowed", "An absolute native project directory is required.")
            # Retry identity uses the submitted spelling. A completed job may have moved the
            # directory or retargeted a symlink; redelivery must still find that original input.
            # The runtime canonicalizes and approves only genuinely new submissions below.
        for name in ("files", "images"):
            paths = value.get(name, ())
            if (
                not isinstance(paths, (list, tuple))
                or len(paths) > 256
                or any(not isinstance(path, str) or not path or len(path) > 4096 for path in paths)
            ):
                raise BackendError("invalid_config", "Native context paths must be bounded lists of path strings.")
            value[name] = tuple(paths)
        return value

    def _expire(self) -> None:
        now = self._clock()
        expired = {
            handle
            for handle, record in self._records.items()
            if record.attempts[-1].state not in _ACTIVE
            and record.last_terminal_at is not None
            and now - record.last_terminal_at >= self.terminal_ttl
        }
        for handle in expired:
            del self._records[handle]
        if expired:
            self._keys = {key: entry for key, entry in self._keys.items() if entry.task_handle not in expired}
            self._session_owners = {
                identity: handle for identity, handle in self._session_owners.items() if handle not in expired
            }

    @staticmethod
    def _fingerprint(args: dict[str, Any], action: str) -> str:
        inputs = {key: value for key, value in args.items() if key != "request_key"}
        return hashlib.sha256(
            json.dumps({"action": action, **inputs}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _record_size(record: _TaskRecord) -> int:
        return _encoded_size(asdict(record))

    @property
    def retained_bytes(self) -> int:
        """Count retained context, index, attempt metadata, and worst-case active results."""
        return (
            sum(self._record_size(record) for record in self._records.values())
            + _encoded_size({key: asdict(value) for key, value in self._keys.items()})
            + _encoded_size(list(self._session_owners.items()))
            + _encoded_size({handle: str(project) for handle, project in self._leases.items()})
            + len(self._leases) * _COMPLETION_RESERVATION_BYTES
        )

    def _check_capacity(self, candidate: _TaskRecord, key: str, entry: _KeyEntry) -> None:
        previous = self._records.get(candidate.task_handle)
        extra = self._record_size(candidate) - (self._record_size(previous) if previous is not None else 0)
        # Include index punctuation and reserve the largest permitted completion before launching.
        extra += (
            _encoded_size({key: asdict(entry)})
            + _encoded_size({candidate.task_handle: str(candidate.context.target.working_directory)})
            + _COMPLETION_RESERVATION_BYTES
        )
        if self.retained_bytes + extra > self.max_retained_bytes:
            raise BackendError("task_capacity", "Native task retention capacity is full; no task was submitted.")

    def _deduplicate(self, key: str, fingerprint: str) -> dict[str, Any] | None:
        entry = self._keys.get(key)
        if entry is None:
            return None
        if entry.fingerprint != fingerprint:
            raise BackendError("request_key_conflict", "This request key was already used for different native input.")
        record = self._records[entry.task_handle]
        attempt = next(item for item in record.attempts if item.attempt_id == entry.attempt_id)
        return self._snapshot(record, attempt, deduplicated=True)

    def _reserve_project(self, handle: str, project: Path) -> None:
        for active in self._leases.values():
            if project == active or project in active.parents or active in project.parents:
                raise BackendError("project_busy", "An overlapping native project already has active work.")
        self._leases[handle] = project

    def _admission_time(self, admitted_at: float | None) -> float:
        value = self._clock() if admitted_at is None else admitted_at
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BackendError("invalid_config", "Native admission time must be a finite monotonic timestamp.")
        return value

    def _prepare(self, *args: Any, **kwargs: Any) -> FrozenNativeContext:
        try:
            return self.runtime.prepare(*args, **kwargs)
        except BackendError as exc:
            code = exc.code if exc.code in _ERROR_CODES else "native_unavailable"
            message, remedy = _PREPARE_DIAGNOSTICS.get(code, ("Native task admission could not be established.", ""))
            selected_cli = args[0] if args else kwargs.get("cli_name")
            if code == "path_not_allowed" and isinstance(selected_cli, str) and _NAME.fullmatch(selected_cli):
                remedy = remedy.replace("--cli NAME", f"--cli {selected_cli}").replace(
                    "(replace NAME with the selected CLI; include", "(include"
                )
            missing = exc.metadata.get("missing_environment_names")
            metadata = {}
            if isinstance(missing, list):
                metadata["missing_environment_names"] = [
                    name
                    for name in missing[:64]
                    if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name)
                ]
            raise BackendError(code, message, remedy, metadata=metadata) from None
        except Exception:
            raise BackendError("native_unavailable", "Native task admission could not be established.") from None

    async def start(
        self, args: dict[str, Any], admitted_at: float | None = None, request_id: str | None = None
    ) -> dict[str, Any]:
        """Reserve the retry identity and project before scheduling server-owned execution."""
        values = self._validate(args, "start")
        fingerprint = self._fingerprint(values, "start")
        submitted = self._admission_time(admitted_at)
        async with self._lock:
            self._expire()
            duplicate = self._deduplicate(values["request_key"], fingerprint)
            if duplicate is not None:
                return duplicate
            if self._closed:
                raise BackendError("cancelled", "The native task manager is shutting down.")
            if len(self._records) >= self.max_tasks:
                raise BackendError("task_capacity", "Native task capacity is full; no task was submitted.")
            context = self._prepare(
                values["cli_name"],
                values.get("role"),
                values.get("model"),
                values["project_dir"],
                values["files"],
                values["images"],
                absolute_deadline=submitted + values["timeout_seconds"],
            )
            handle = "nt_" + uuid.uuid4().hex
            attempt = _Attempt("na_" + uuid.uuid4().hex, "queued", submitted)
            # Context attachments belong only to this input, never to future native follow-ups.
            record = _TaskRecord(handle, replace(context, files=(), images=(), deadline=None), [attempt])
            entry = _KeyEntry(fingerprint, handle, attempt.attempt_id)
            self._check_capacity(record, values["request_key"], entry)
            self._reserve_project(handle, context.target.working_directory)
            self._records[handle] = record
            self._keys[values["request_key"]] = entry
            self._jobs[handle] = asyncio.create_task(
                self._execute(handle, attempt, context, values["prompt"], None), name=f"native-{attempt.attempt_id}"
            )
            return self._snapshot(record, attempt)

    async def followup(
        self, args: dict[str, Any], admitted_at: float | None = None, request_id: str | None = None
    ) -> dict[str, Any]:
        """Submit only new input to the recorded native session and frozen selection."""
        values = self._validate(args, "followup")
        fingerprint = self._fingerprint(values, "followup")
        submitted = self._admission_time(admitted_at)
        async with self._lock:
            self._expire()
            duplicate = self._deduplicate(values["request_key"], fingerprint)
            if duplicate is not None:
                return duplicate
            if self._closed:
                raise BackendError("cancelled", "The native task manager is shutting down.")
            record = self._records.get(values["task_handle"])
            if record is None:
                return self._unavailable(values["task_handle"])
            if record.attempts[-1].state in _ACTIVE:
                raise BackendError("task_busy", "This native task already has active input.")
            if record.session_id is None:
                raise BackendError(
                    "session_unavailable", "The recorded native session is unavailable; no task was submitted."
                )
            if len(record.attempts) >= self.max_attempts:
                raise BackendError(
                    "task_capacity", "This native task reached its attempt limit; no input was submitted."
                )
            target = record.context.target
            context = self._prepare(
                values.get("cli_name") or target.cli_name,
                values.get("role") or target.role.name,
                values.get("model", target.requested_model),
                values.get("project_dir") or str(target.working_directory),
                values["files"],
                values["images"],
                absolute_deadline=submitted + values["timeout_seconds"],
            )
            identity = lambda item: (  # noqa: E731 -- keep the complete binding visible at the comparison.
                item.target.cli_name,
                item.target.role.name,
                item.target.effective_model,
                item.target.working_directory,
                item.profile_id,
            )
            if identity(context) != identity(record.context):
                raise BackendError("session_conflict", "Follow-up input conflicts with the recorded native selection.")
            context = replace(record.context, files=context.files, images=context.images, deadline=context.deadline)
            attempt = _Attempt("na_" + uuid.uuid4().hex, "queued", submitted)
            candidate = replace(record, attempts=[*record.attempts, attempt], latest_result=None)
            entry = _KeyEntry(fingerprint, record.task_handle, attempt.attempt_id)
            self._check_capacity(candidate, values["request_key"], entry)
            self._reserve_project(record.task_handle, target.working_directory)
            self._records[record.task_handle] = candidate
            self._keys[values["request_key"]] = entry
            self._jobs[record.task_handle] = asyncio.create_task(
                self._execute(record.task_handle, attempt, context, values["prompt"], record.session_id),
                name=f"native-{attempt.attempt_id}",
            )
            return self._snapshot(candidate, attempt)

    @staticmethod
    def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        if not isinstance(metadata, dict):
            return safe
        for key in sorted(_METADATA_KEYS):
            if key not in metadata:
                continue
            value = metadata[key]
            # Scalars and short lists/maps only; arbitrary parser structures cannot bypass the result cap.
            if isinstance(value, str):
                value = value[:512]
            elif value is None or isinstance(value, bool):
                pass
            elif isinstance(value, (int, float)):
                try:
                    finite = math.isfinite(value) and abs(value) <= 10**15
                except (OverflowError, TypeError):
                    finite = False
                if not finite:
                    continue
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                value = [item[:128] for item in value[:32]]
            else:
                continue
            try:
                fits = _encoded_size({**safe, key: value}) <= RESULT_METADATA_BYTES
            except (UnicodeEncodeError, ValueError):
                fits = False
            if fits:
                safe[key] = value
        return safe

    async def _execute(
        self,
        handle: str,
        attempt: _Attempt,
        context: FrozenNativeContext,
        prompt: str,
        session_id: str | None,
    ) -> None:
        outcome: NativeRunOutcome | None = None
        state = "uncertain"
        error_code = None

        def started() -> None:
            attempt.started_at = self._clock()
            attempt.possible_partial_effects = True
            attempt.state = "running"

        try:
            outcome = await self.runtime.run(
                context, prompt, session_id, attempt_id=attempt.attempt_id, started=started
            )
            if not isinstance(outcome, NativeRunOutcome):
                outcome = None
                raise BackendError("parse_error", "The native runtime returned no recognized terminal outcome.")
            state = outcome.state if outcome.state in _TERMINAL else "uncertain"
            native_error = outcome.metadata.get("error_code") if isinstance(outcome.metadata, dict) else None
            error_code = native_error if isinstance(native_error, str) and native_error in _ERROR_CODES else None
            attempt.possible_partial_effects = attempt.possible_partial_effects or outcome.partial_effects
        except asyncio.CancelledError:
            state = "cancelled"
        except BackendError as exc:
            error_code = exc.code if exc.code in _ERROR_CODES else "native_failed"
            if error_code == "permission_needed":
                state = "permission_needed"
            else:
                state = "uncertain" if attempt.started_at is not None else "failed"
        except Exception:
            error_code = "native_failed"
            state = "uncertain" if attempt.started_at is not None else "failed"
        finally:
            # The runtime contract guarantees process cleanup is already complete at this point.
            prompt = ""
            async with self._lock:
                record = self._records[handle]
                if outcome is not None:
                    native_id = outcome.session_id
                    if native_id is not None and (
                        not isinstance(native_id, str)
                        or not native_id
                        or len(native_id) > 512
                        or any(ord(char) < 32 or ord(char) == 127 for char in native_id)
                    ):
                        native_id = None
                        state, error_code = "uncertain", "session_unavailable"
                    if native_id is not None:
                        owner = self._session_owners.get((record.context.target.cli_name, native_id))
                        if owner is not None and owner != handle:
                            state, error_code = "uncertain", "session_conflict"
                        elif record.session_id is not None and record.session_id != native_id:
                            state, error_code = "uncertain", "session_conflict"
                        else:
                            record.session_id = native_id
                            self._session_owners[(record.context.target.cli_name, native_id)] = handle
                    raw_content = outcome.content if isinstance(outcome.content, str) else ""
                    # Invalid Unicode is treated as untrusted output rather than failing finalization.
                    content = raw_content[:RESULT_CHARACTERS].encode("utf-8", errors="replace").decode("utf-8")
                    record.latest_result = {
                        "content": content,
                        "truncated": len(raw_content) > RESULT_CHARACTERS
                        or (isinstance(outcome.metadata, dict) and outcome.metadata.get("truncated") is True),
                        "agent_reported_claims": "unverified",
                        "metadata": self._public_metadata(outcome.metadata),
                    }
                attempt.state = state
                attempt.error_code = error_code
                attempt.terminal_at = self._clock()
                record.last_terminal_at = attempt.terminal_at
                self._leases.pop(handle, None)
                self._jobs.pop(handle, None)
                logger.info(
                    "native_attempt task_handle=%s attempt_id=%s state=%s possible_partial_effects=%s",
                    handle,
                    attempt.attempt_id,
                    state,
                    attempt.possible_partial_effects,
                )

    @staticmethod
    def _unavailable(handle: str) -> dict[str, Any]:
        return {
            "task_handle": handle,
            "state": "unavailable",
            "execution_outcome": "unknown",
            "result_unavailable": True,
            "execution_does_not_verify_task_fulfillment": True,
            "possible_partial_effects": True,
        }

    @staticmethod
    def _snapshot(record: _TaskRecord, attempt: _Attempt, *, deduplicated: bool = False) -> dict[str, Any]:
        latest = record.attempts[-1] is attempt
        result = record.latest_result if latest else None
        response: dict[str, Any] = {
            "task_handle": record.task_handle,
            "attempt_id": attempt.attempt_id,
            "state": attempt.state,
            "execution_outcome": "unknown" if attempt.state == "uncertain" else attempt.state,
            "admitted_at": attempt.admitted_at,
            "started_at": attempt.started_at,
            "terminal_at": attempt.terminal_at,
            "possible_partial_effects": attempt.possible_partial_effects,
            "cancellation_requested": attempt.cancel_requested,
            "execution_does_not_verify_task_fulfillment": True,
            "result_unavailable": result is None,
            "deduplicated": deduplicated,
        }
        if result is not None:
            # Return detached data so callers cannot mutate retained result metadata.
            response["result"] = json.loads(json.dumps(result))
        if attempt.error_code is not None:
            response["error"] = {"code": attempt.error_code, "message": "Native execution did not establish success."}
        if latest:
            response["followup_available"] = record.session_id is not None and attempt.state not in _ACTIVE
        return response

    async def status(self, args: dict[str, Any] | str) -> dict[str, Any]:
        values = self._validate({"task_handle": args} if isinstance(args, str) else args, "status")
        async with self._lock:
            self._expire()
            record = self._records.get(values["task_handle"])
            if record is None:
                return self._unavailable(values["task_handle"])
            return self._snapshot(record, record.attempts[-1])

    async def _cancel_job(self, handle: str, attempt: _Attempt, job: asyncio.Task[None]) -> None:
        await asyncio.gather(job, return_exceptions=True)
        async with self._lock:
            # A task cancelled before its coroutine first executes has no finally block.
            if attempt.state in _ACTIVE:
                attempt.state = "cancelled"
                attempt.terminal_at = self._clock()
                self._records[handle].last_terminal_at = attempt.terminal_at
                self._leases.pop(handle, None)
                self._jobs.pop(handle, None)

    @staticmethod
    async def _finish_cleanup(cleanup: asyncio.Task[Any]) -> None:
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
        if interrupted:
            raise asyncio.CancelledError

    async def cancel(self, args: dict[str, Any] | str) -> dict[str, Any]:
        values = self._validate({"task_handle": args} if isinstance(args, str) else args, "cancel")
        async with self._lock:
            self._expire()
            record = self._records.get(values["task_handle"])
            if record is None:
                return self._unavailable(values["task_handle"])
            attempt = record.attempts[-1]
            job = self._jobs.get(record.task_handle)
            if job is None or attempt.state not in _ACTIVE:
                return self._snapshot(record, attempt)
            if not attempt.cancel_requested:
                attempt.cancel_requested = True
                job.cancel()
            cleanup = asyncio.create_task(self._cancel_job(record.task_handle, attempt, job))
        await self._finish_cleanup(cleanup)
        async with self._lock:
            # Another explicit follow-up may already own the task. Return the cancelled input only.
            return self._snapshot(self._records.get(record.task_handle, record), attempt)

    async def shutdown(self) -> None:
        """Close admission and await every process cleanup before releasing native ownership."""
        async with self._lock:
            self._closed = True
            pending = []
            for handle, job in tuple(self._jobs.items()):
                attempt = self._records[handle].attempts[-1]
                if not attempt.cancel_requested:
                    attempt.cancel_requested = True
                    job.cancel()
                pending.append(asyncio.create_task(self._cancel_job(handle, attempt, job)))
        if pending:

            async def finish() -> None:
                await asyncio.gather(*pending)

            await self._finish_cleanup(asyncio.create_task(finish()))


_TASK_MANAGER: NativeTaskManager | None = None


def get_task_manager() -> NativeTaskManager:
    """Lazily share the server-owned manager without creating another process pool."""
    global _TASK_MANAGER
    if _TASK_MANAGER is None:
        from clink.native import get_native_runtime

        _TASK_MANAGER = NativeTaskManager(get_native_runtime())
    return _TASK_MANAGER


async def shutdown_task_manager() -> None:
    """Drain only an existing native manager; shutdown must never instantiate a backend."""
    if _TASK_MANAGER is not None:
        await _TASK_MANAGER.shutdown()
