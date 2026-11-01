"""One-shot native task attempts through the backend's shared process limits."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clink.backend import CLIBackend, _directory, _model, get_backend
from clink.contracts import AdapterPolicy, BackendError, BackendTarget, ClientSnapshot, CLIRequest, RoleSnapshot
from clink.native_contracts import FrozenNativeContext, NativeRunOutcome
from clink.native_policy import NATIVE_ENVIRONMENT_KEYS, native_arguments, native_recipe_digest
from clink.parsers.base import ParserError
from clink.parsers.claude_native import ClaudeNativeParser, validate_session_id
from clink.policy import executable_digest

PROMPT_BYTES = 1024 * 1024
# This profile has its own genuine native evidence. Advisory receipts cannot admit it.
_VERIFIED_NATIVE_POLICIES: dict[str, AdapterPolicy] = {
    "claude": AdapterPolicy(
        adapter="claude",
        version="2.1.304 (Claude Code)",
        verified=True,
        verification_status="verified_native_lifecycle",
        file_references=True,
        repository_instructions="native user/project instructions retained",
        read_scope="native permissions; approved roots constrain supplied context only",
        evidence="clink/resources/native-claude-2.1.304-admission.json",
        evidence_sha256="ed3cd9bf385ac533b8f24c06cc14a431c0dbac0100f1eb23f24953eab6ca4de2",
        executable_sha256="b9ac5374839f14abf3f1f45610637c332b5daee5ce0d1634726cef87aab78e6a",
        recipe_sha256="de885baaadfdf5810321e32992c7e961f3a360dda90b903ab98ffc243564885e",
    )
}


class NativeRuntime:
    """Freeze native configuration without granting capabilities beyond native policy."""

    def __init__(self, backend: CLIBackend | None = None):
        self.backend = backend or get_backend()
        self.pool = self.backend.pool

    @staticmethod
    def _policy(adapter: str) -> AdapterPolicy:
        return _VERIFIED_NATIVE_POLICIES.get(adapter, AdapterPolicy(adapter))

    def describe(self) -> list[dict[str, Any]]:
        """Report native profiles without authentication, inference, or executable probes."""
        descriptions = []
        for name in self.backend.registry.list_clients():
            client = self.backend.registry.get_client(name)
            profile = client.native_task
            if profile is None:
                continue
            policy = self._policy(client.adapter)
            installed = bool(client.executable and shutil.which(client.executable[0]))
            descriptions.append(
                {
                    "cli_name": client.name,
                    "adapter": client.adapter,
                    "enabled": profile.enabled,
                    "installed": installed,
                    "available": profile.enabled and installed and policy.verified,
                    "verification_status": policy.verification_status,
                    "version": policy.version,
                    "version_origin": "admission_receipt",
                    "launch_validation": "exact executable, recipe and receipt rechecked for each attempt",
                    "authentication": "unknown",
                    "roles": sorted(profile.roles),
                    "default_model": profile.default_model,
                    "default_model_origin": "native_profile" if profile.default_model else "native_cli_configuration",
                    "environment_names": list(profile.inherit_env),
                    "required_environment_names": list(profile.required_env),
                    "permission_handling": "native policy; unattended prompts denied and reported",
                    "read_scope": "native policy; approved roots only constrain server-supplied context",
                }
            )
        return descriptions

    def _check_depth(self) -> None:
        depth = os.environ.get("CLINK_DELEGATION_DEPTH", "0")
        if not re.fullmatch(r"[0-9]+", depth):
            raise BackendError("invalid_config", "CLINK_DELEGATION_DEPTH must be a nonnegative integer.")
        if self.backend._nested or depth.strip("0"):
            raise BackendError("recursion_blocked", "A known nested clink server cannot submit native tasks.")

    @staticmethod
    def _check_environment_names(context: FrozenNativeContext) -> None:
        missing = [name for name in context.required_environment_names if not os.environ.get(name)]
        if missing:
            raise BackendError(
                "missing_environment",
                "Required native environment names are unavailable in the server process.",
                metadata={"missing_environment_names": missing},
            )

    def prepare(
        self,
        cli_name: str,
        role: str | None,
        model: str | None,
        project_dir: str,
        files: tuple[str, ...] = (),
        images: tuple[str, ...] = (),
        absolute_deadline: float | None = None,
    ) -> FrozenNativeContext:
        """Validate and freeze names/context before reserving a task, without CLI calls."""
        self._check_depth()
        if absolute_deadline is not None and (
            isinstance(absolute_deadline, bool)
            or not isinstance(absolute_deadline, (int, float))
            or not math.isfinite(absolute_deadline)
        ):
            raise BackendError("invalid_config", "A native deadline must be a finite monotonic timestamp.")
        if not isinstance(cli_name, str) or not cli_name:
            raise BackendError("invalid_config", "A native task requires an explicit configured CLI name.")
        try:
            client = self.backend.registry.get_client(cli_name)
        except KeyError as exc:
            raise BackendError("invalid_config", "The requested native CLI client is not configured.") from exc
        profile = client.native_task
        if profile is None or not profile.enabled:
            raise BackendError("unsupported_capability", "This client has no enabled native task profile.")
        if client.adapter != "claude":
            raise BackendError("unsupported_capability", "Only the Claude native task adapter is implemented.")
        selected_role = profile.roles.get(role or "default")
        if selected_role is None:
            raise BackendError("invalid_config", "The requested role is not configured in the native task profile.")
        policy = self._policy(client.adapter)
        if not policy.verified:
            raise BackendError(
                "policy_unverified", "The native task profile has no admitted version-specific evidence."
            )
        command = tuple(client.executable)
        found = shutil.which(command[0]) if command else None
        if not found:
            raise BackendError("cli_missing", "The configured native CLI executable is unavailable.")
        executable = (str(Path(found).resolve()), *command[1:])
        roots = tuple(Path(root).resolve() for root in client.allowed_roots)
        cwd = _directory(project_dir, roots)
        requested_model = _model(model)
        role_model = _model(selected_role.model)
        effective_model = requested_model or role_model or _model(profile.default_model)
        target = BackendTarget(
            client=ClientSnapshot(
                name=client.name,
                adapter=client.adapter,
                executable=executable,
                working_dir=Path(client.working_dir),
                allowed_roots=roots,
                config_args=tuple(client.config_args),
                env=tuple(sorted(client.env.items())),
                timeout_seconds=client.timeout_seconds,
                parser="claude_native",
                default_model=profile.default_model,
            ),
            role=RoleSnapshot(selected_role.name, selected_role.prompt_text, model=selected_role.model),
            requested_model=requested_model,
            effective_model=effective_model,
            working_directory=cwd,
            policy=policy,
            default_origin=(
                "request" if requested_model else "role" if role_model else "client" if effective_model else "native"
            ),
        )
        validated_files = self.validate_context(target, files, images)
        identity = {
            "client": client.name,
            "adapter": client.adapter,
            "executable": executable,
            "project": str(cwd),
            "roots": [str(root) for root in roots],
            "model": effective_model,
            "role": selected_role.name,
            "role_sha256": hashlib.sha256(selected_role.prompt_text.encode()).hexdigest(),
            "environment_names": profile.inherit_env,
            "required_environment_names": profile.required_env,
            "configured_environment_names": sorted(client.env),
            "configured_environment_sha256": hashlib.sha256(
                json.dumps(sorted(client.env.items()), separators=(",", ":")).encode()
            ).hexdigest(),
            "config_args": client.config_args,
            "recipe": native_recipe_digest(client.adapter),
        }
        profile_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        context = FrozenNativeContext(
            target=target,
            profile_id=profile_id,
            files=validated_files,
            deadline=absolute_deadline,
            environment_names=tuple(profile.inherit_env),
            required_environment_names=tuple(profile.required_env),
        )
        self._check_environment_names(context)
        return context

    def validate_context(
        self, target: BackendTarget, files: tuple[str, ...], images: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        if images:
            raise BackendError("unsupported_capability", "Native image input has not been verified for this adapter.")
        validated, _ = self.backend._context(CLIRequest(prompt="", target=target, files=files))
        return validated

    @staticmethod
    def _compose(context: FrozenNativeContext, prompt: str) -> bytes:
        if not isinstance(prompt, str) or not prompt.strip():
            raise BackendError("invalid_config", "A native task prompt must be nonempty text.")
        sections = [
            "Execute the assigned scoped task using your configured native capabilities and permissions. "
            "Preserve project/user instructions. Report changes, checks, unfinished work, and possible partial effects. "
            "Do not claim independent verification for checks you did not perform.",
            "<subordinate_role_expertise_json>\n"
            + json.dumps(context.target.role.prompt_text, ensure_ascii=False)
            + "\n</subordinate_role_expertise_json>",
            "<declared_context_paths_json>\n"
            + json.dumps({"files": context.files}, ensure_ascii=False)
            + "\n</declared_context_paths_json>",
            "<assigned_task_json>\n" + json.dumps(prompt, ensure_ascii=False) + "\n</assigned_task_json>",
        ]
        content = "\n\n".join(sections).encode("utf-8")
        if len(content) > PROMPT_BYTES:
            raise BackendError("input_limit", "The assembled native prompt exceeds 1 MiB UTF-8.")
        return content

    @staticmethod
    def _command(context: FrozenNativeContext, session_id: str | None) -> tuple[str, ...]:
        validate_session_id(session_id)
        target = context.target
        arguments = native_arguments(target.client.adapter)
        if not arguments:
            raise BackendError("policy_unverified", "This adapter has no native launch recipe.")
        command = [*target.client.executable, *arguments, *target.client.config_args]
        if target.effective_model is not None:
            command.extend(("--model", target.effective_model))
        if session_id is not None:
            command.extend(("--resume", session_id))
        return tuple(command)

    @staticmethod
    def _environment(context: FrozenNativeContext) -> dict[str, str]:
        names = frozenset((*NATIVE_ENVIRONMENT_KEYS, *context.environment_names))
        result = {name: value for name, value in os.environ.items() if name in names}
        additions = dict(context.target.client.env)
        if any(name not in NATIVE_ENVIRONMENT_KEYS for name in additions):
            raise BackendError("invalid_config", "Native static environment additions must use baseline names.")
        result.update(additions)
        result["CLINK_DELEGATION_DEPTH"] = "1"
        return result

    def _validate_admission(self, context: FrozenNativeContext) -> None:
        target = context.target
        policy = self._policy(target.client.adapter)
        if not policy.verified or policy != target.policy:
            raise BackendError("policy_unverified", "The native admission snapshot is unavailable or changed.")
        if not policy.version or not policy.executable_sha256 or not policy.recipe_sha256:
            raise BackendError("policy_unverified", "Native admission must bind an exact binary/version and recipe.")
        if native_recipe_digest(target.client.adapter) != policy.recipe_sha256:
            raise BackendError("policy_unverified", "The native launch recipe changed after verification.")
        if not policy.evidence or not policy.evidence_sha256:
            raise BackendError("policy_unverified", "Native admission requires a pinned verification receipt.")
        project = Path(__file__).resolve().parent.parent
        path = (project / policy.evidence).resolve()
        evidence_roots = (project / "clink" / "resources", project / "docs" / "verification")
        if not any(root in path.parents for root in evidence_roots):
            raise BackendError("policy_unverified", "Native evidence is outside the verification directory.")
        try:
            with path.open("rb") as stream:
                evidence = stream.read(PROMPT_BYTES + 1)
        except OSError as exc:
            raise BackendError("policy_unverified", "Native admission evidence is unavailable.") from exc
        if len(evidence) > PROMPT_BYTES or hashlib.sha256(evidence).hexdigest() != policy.evidence_sha256:
            raise BackendError("policy_unverified", "Native admission evidence changed after verification.")

    async def run(
        self,
        context: FrozenNativeContext,
        prompt: str,
        session_id: str | None = None,
        *,
        attempt_id: str,
        started: Callable[[], None] | None = None,
    ) -> NativeRunOutcome:
        """Submit exactly once; process cleanup completes before cancellation propagates."""
        started_at = time.monotonic()
        configured_deadline = started_at + context.target.client.timeout_seconds
        deadline = min(context.deadline, configured_deadline) if context.deadline is not None else configured_deadline
        self._check_depth()
        self._check_environment_names(context)
        self._validate_admission(context)
        content = self._compose(context, prompt)
        command = self._command(context, session_id)
        try:
            observed = await asyncio.wait_for(
                asyncio.to_thread(executable_digest, command[0]), max(0.0, deadline - time.monotonic())
            )
        except asyncio.TimeoutError as exc:
            raise BackendError(
                "timeout", "The native attempt deadline expired during executable verification."
            ) from exc
        except OSError as exc:
            raise BackendError("cli_missing", "The admitted native executable is unavailable.") from exc
        if observed != context.target.policy.executable_sha256:
            raise BackendError("policy_unverified", "The installed native binary differs from its admission evidence.")
        environment: dict[str, str] = {}

        def prelaunch() -> None:
            self._check_depth()
            self._check_environment_names(context)
            self._validate_admission(context)
            if executable_digest(command[0]) != context.target.policy.executable_sha256:
                raise BackendError("policy_unverified", "The admitted native executable changed before launch.")
            cwd = _directory(context.target.working_directory, context.target.client.allowed_roots)
            if cwd != context.target.working_directory:
                raise BackendError("path_not_allowed", "The native project changed before launch.")
            self.validate_context(context.target, context.files, context.images)
            environment.update(self._environment(context))

        output = await self.pool.execute(
            command,
            content=content,
            cwd=str(context.target.working_directory),
            env=environment,
            deadline=deadline,
            request_id=attempt_id,
            metadata={"client": context.target.cli_name, "role": context.target.role.name, "tool": "clink"},
            prelaunch=prelaunch,
            on_started=started,
        )
        metadata: dict[str, Any] = {
            "exit_status": output.returncode,
            "stdout_bytes": output.stdout_bytes,
            "stderr_bytes": output.stderr_bytes,
            "queue_seconds": output.queue_seconds,
            "claims_verification": "agent_reported_unverified",
            "goal_achieved": "unverified",
            "configuration_sources": ["native_user", "native_project", "operator_environment_names"],
            "verification_status": context.target.policy.verification_status,
        }
        try:
            parsed = ClaudeNativeParser().parse(output.stdout, output.stderr)
        except ParserError as exc:
            state = (
                "failed"
                if output.returncode != 0 or exc.code in {"authentication_required", "rate_limited"}
                else "uncertain"
            )
            return NativeRunOutcome(state, metadata={**metadata, "error_code": exc.code})
        if session_id is not None and parsed.session_id is not None and session_id != parsed.session_id:
            return NativeRunOutcome("uncertain", metadata={**metadata, "error_code": "native_session_mismatch"})
        state = parsed.state
        if output.returncode != 0 and state == "completed":
            state = "failed"
            metadata["error_code"] = "nonzero_exit"
        return NativeRunOutcome(
            state,
            content=parsed.content,
            metadata={**metadata, **parsed.metadata},
            session_id=parsed.session_id,
            partial_effects=True,
        )


_RUNTIME: NativeRuntime | None = None


def get_native_runtime() -> NativeRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = NativeRuntime()
    return _RUNTIME
