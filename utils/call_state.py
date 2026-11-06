"""Atomic per-continuation MCP calls, canonical replay, and workflow transitions.

Lock ownership spans state read, external execution, and terminal publication.
Only JSON-ready bounded outcomes are retained; native CLI session IDs are never
used as continuations. Server dispatch is the sole normal transaction owner.
"""

import asyncio
import contextvars
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel

from utils.conversation_memory import (
    CONVERSATION_TIMEOUT_SECONDS,
    MAX_CONVERSATION_TURNS,
    MAX_HISTORY_BYTES,
    ConversationTurn,
    ThreadContext,
    _is_valid_uuid,
    build_conversation_history,
    build_replay_turns,
    get_storage,
    has_invalidated_consensus,
    successful_turn_count,
    trim_history,
)
from utils.storage_backend import StorageCapacityError

MAX_TERMINAL_BYTES = 256 * 1024
MAX_WORKFLOW_BYTES = 1024 * 1024
# Reserve all possible growth before external work, including JSON envelope overhead.
CALL_RESERVATION_BYTES = MAX_TERMINAL_BYTES + MAX_WORKFLOW_BYTES + MAX_HISTORY_BYTES + 64 * 1024
_current_call: contextvars.ContextVar[Optional["ConversationCall"]] = contextvars.ContextVar("clink_call", default=None)


class StateError(RuntimeError):
    """An explicit continuation admission or transition failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def get_current_call() -> Optional["ConversationCall"]:
    return _current_call.get()


def _json_ready(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def canonical_input(arguments: dict[str, Any]) -> str:
    """Digest validated original inputs, independent of key order and transport IDs."""
    clean = {key: value for key, value in arguments.items() if not key.startswith("_") and key != "continuation_id"}
    try:
        return json.dumps(
            _json_ready(clean), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise StateError("input_limit", "Conversation input must contain finite JSON values.") from exc


def transition_address(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Respect existing step, planner branch, and explicit revision coordinates."""
    if "step_number" not in arguments:
        return None
    address = {
        "tool": tool_name,
        "step_number": arguments.get("step_number"),
        "branch_id": arguments.get("branch_id"),
        "branch_from_step": arguments.get("branch_from_step"),
        "is_step_revision": arguments.get("is_step_revision", False),
        "is_branch_point": arguments.get("is_branch_point", False),
        "revises_step_number": arguments.get("revises_step_number"),
    }
    return json.dumps(address, sort_keys=True, separators=(",", ":"))


class ConversationCall:
    """A reserved continuation transaction exposed to server and tool bridges."""

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        deadline: float | None = None,
        admitted_at: float | None = None,
        request_id: str | None = None,
    ):
        self.tool_name = tool_name
        self.arguments = _json_ready(arguments)
        self.admitted_at = time.monotonic() if admitted_at is None else admitted_at
        self.deadline = self.admitted_at + 1800 if deadline is None else deadline
        self.request_id = request_id or str(uuid.uuid4())
        self.continuation_id = arguments.get("continuation_id") or str(uuid.uuid4())
        self._existing = bool(arguments.get("continuation_id"))
        self._key = f"thread:{self.continuation_id}"
        self._address = transition_address(tool_name, self.arguments)
        self._digest = hashlib.sha256(canonical_input(self.arguments).encode("utf-8")).hexdigest()
        self._storage = get_storage()
        self._serialization = None
        self._lock_entered = False
        self._token = None
        self._reserved = False
        self._committed = False
        self._staged_turns: list[ConversationTurn] = []
        self.cached_result: Any = None
        self.context: ThreadContext
        self.workflow_state: dict[str, Any] = {}
        self._replay_metadata: dict[str, Any] = {}

    async def __aenter__(self) -> "ConversationCall":
        if get_current_call() is not None:
            raise RuntimeError("Nested conversation transactions are not supported")
        if not _is_valid_uuid(self.continuation_id):
            raise StateError(
                "continuation_unavailable", "Continuation is invalid or unavailable; start a new conversation."
            )
        self._serialization = self._storage.serialize(self._key, self.deadline)
        try:
            await self._serialization.__aenter__()
            self._lock_entered = True
            data = self._storage.get(self._key)
            if self._existing and data is None:
                raise StateError(
                    "continuation_unavailable", "Continuation is unknown or expired; start a new conversation."
                )
            now = datetime.now(timezone.utc).isoformat()
            self.context = (
                ThreadContext.model_validate_json(data)
                if data is not None
                else ThreadContext(
                    thread_id=self.continuation_id,
                    created_at=now,
                    last_updated_at=now,
                    tool_name=self.tool_name,
                    turns=[],
                    initial_context={},
                )
            )
            self.workflow_state = _json_ready(self.context.workflow_states.get(self.tool_name, {}))
            previous = self.context.transitions.get(self._address) if self._address is not None else None
            if (
                self.tool_name != "consensus"
                and (self.workflow_state or previous is not None)
                and has_invalidated_consensus(self.context)
            ):
                raise StateError(
                    "consensus_invalidated",
                    "Saved workflow state or cached results may depend on invalidated consensus; "
                    "start a new conversation without continuation_id.",
                )
            if previous is not None:
                if previous["digest"] != self._digest:
                    raise StateError("workflow_conflict", "This workflow transition already has different input.")
                self.cached_result = previous.get("result")
            elif successful_turn_count(self.context) + 2 > MAX_CONVERSATION_TURNS:
                raise StateError(
                    "conversation_capacity",
                    "Conversation cannot reserve another user/assistant pair; start a new conversation.",
                )
            # Cache validation can invalidate and replace a completed outcome, so it also needs reservation.
            self._storage.reserve(
                self._key, CONVERSATION_TIMEOUT_SECONDS, self.context.model_dump_json(), CALL_RESERVATION_BYTES
            )
            self._reserved = True
            self._token = _current_call.set(self)
            return self
        except BaseException as exc:
            if self._lock_entered:
                await self._serialization.__aexit__(None, None, None)
                self._lock_entered = False
            self._serialization = None
            if isinstance(exc, StorageCapacityError):
                raise StateError("conversation_capacity", str(exc)) from exc
            if isinstance(exc, asyncio.TimeoutError):
                raise StateError("timeout", "Request deadline expired while waiting for conversation state.") from exc
            raise

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        try:
            if not self._committed and self.cached_result is None and self._reserved:
                code = "cancelled" if isinstance(exc, asyncio.CancelledError) else "interrupted"
                if isinstance(exc, StateError):
                    code = exc.code
                terminal = [{"type": "text", "text": json.dumps({"status": "error", "error_code": code})}]
                try:
                    self.commit(terminal, success=False)
                except StateError:
                    # Malformed/oversized transient tool state must not erase the
                    # terminal failed address and permit an implicit re-launch.
                    self.workflow_state = self.context.workflow_states.get(self.tool_name, {})
                    self.commit(terminal, success=False)
        finally:
            if self._reserved:
                self._storage.release_reservation(self._key)
                self._reserved = False
            if self._token is not None:
                _current_call.reset(self._token)
            if self._serialization is not None:
                await self._serialization.__aexit__(exc_type, exc, traceback)
                self._serialization = None
        return False

    def history(self) -> str:
        return build_conversation_history(self.context, metadata=self._replay_metadata)[0]

    def history_turns(self) -> tuple[str, ...]:
        """Return whole replay messages independently from required request content."""
        return build_replay_turns(self.context, metadata=self._replay_metadata)

    @property
    def replay_metadata(self) -> dict[str, Any]:
        """Expose history pruning to the caller as well as the delegated prompt."""
        self.history_turns()
        return dict(self._replay_metadata)

    def set_workflow_state(self, state: dict[str, Any]) -> None:
        ready = _json_ready(state)
        try:
            size = len(json.dumps(ready, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise StateError("input_limit", "Workflow state must contain finite JSON values.") from exc
        if size > MAX_WORKFLOW_BYTES:
            raise StateError("conversation_capacity", "Workflow state exceeds the admitted conversation limit.")
        self.workflow_state = ready

    def stage_turn(self, turn: ConversationTurn) -> None:
        """Support old helper callers without publishing before terminal commit."""
        self._staged_turns.append(turn)

    def commit(
        self,
        result: Any,
        *,
        success: bool = True,
        retain_failure_context: bool = False,
        user_content: str | None = None,
        assistant_content: str | None = None,
        files: list[str] | None = None,
        images: list[str] | None = None,
        backend_identity: dict[str, Any] | None = None,
        workflow_state: dict[str, Any] | None = None,
    ) -> None:
        """Atomically publish outcome, owned tool state, attempt, and successful turns."""
        if self._committed:
            raise RuntimeError("Conversation call was already committed")
        ready = _json_ready(result)
        try:
            size = len(json.dumps(ready, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise StateError("input_limit", "Terminal result must contain finite JSON values.") from exc
        if size > MAX_TERMINAL_BYTES:
            raise StateError("conversation_capacity", "Terminal result exceeds the admitted conversation limit.")
        if workflow_state is not None:
            self.set_workflow_state(workflow_state)
        else:
            self.set_workflow_state(self.workflow_state)
        updated = self.context.model_copy(deep=True)
        updated.workflow_states[self.tool_name] = self.workflow_state
        if (
            self.tool_name == "consensus"
            and updated.consensus_started_at_turn is None
            and not self.context.workflow_states.get("consensus")
            and isinstance(self.workflow_state.get("original_proposal"), str)
            and not self.workflow_state.get("invalidated", False)
        ):
            # Absolute message ordinals survive oldest-first history pruning.
            updated.consensus_started_at_turn = self.context.omitted_turns + len(self.context.turns)
        now = datetime.now(timezone.utc).isoformat()
        attempt = {
            "request_id": self.request_id,
            "tool": self.tool_name,
            "success": success,
            "status": "success" if success else "partial" if retain_failure_context else "error",
            "timestamp": now,
        }
        if self._address is None and not success:
            # Non-workflow requests are explicit new calls, but their terminal
            # failures still belong to canonical server-owned attempt state.
            attempt["outcome"] = ready
        if backend_identity:
            attempt["backend"] = _json_ready(backend_identity)
        updated.attempts.append(attempt)
        # Keep bounded attempt metadata independently from successful turn count.
        updated.attempts = updated.attempts[-MAX_CONVERSATION_TURNS:]
        if self._address is not None:
            updated.transitions[self._address] = {"digest": self._digest, "result": ready, "success": success}
        if (success or retain_failure_context) and self.cached_result is None:
            status = "success" if success else "partial"
            turns = [turn.model_copy(update={"attempt_status": status}) for turn in self._staged_turns]
            for role, content in (("user", user_content), ("assistant", assistant_content)):
                if content is not None and not any(turn.role == role and turn.content == content for turn in turns):
                    turns.append(
                        ConversationTurn(
                            role=role,
                            content=content,
                            timestamp=now,
                            files=files,
                            images=images,
                            tool_name=self.tool_name,
                            model_metadata=backend_identity,
                            attempt_status=status,
                        )
                    )
            prior_count = successful_turn_count(updated)
            if len(turns) > 2 or (success and prior_count + len(turns) > MAX_CONVERSATION_TURNS):
                raise StateError(
                    "conversation_capacity", "Conversation exceeded its reserved user/assistant turn slots."
                )
            updated.turns.extend(turns)
            updated.successful_turns = prior_count + (len(turns) if success else 0)
            trim_history(updated)
        updated.last_updated_at = now
        try:
            self._storage.commit_reserved(self._key, CONVERSATION_TIMEOUT_SECONDS, updated.model_dump_json())
        except StorageCapacityError as exc:
            raise StateError("conversation_capacity", str(exc)) from exc
        self.context = updated
        self._reserved = False
        self._committed = True


def conversation_call(
    tool_name: str,
    validated_arguments: dict[str, Any],
    *,
    deadline: float | None = None,
    admitted_at: float | None = None,
    request_id: str | None = None,
) -> ConversationCall:
    """Create the server-owned transaction before replay or delegated execution."""
    return ConversationCall(
        tool_name, validated_arguments, deadline=deadline, admitted_at=admitted_at, request_id=request_id
    )
