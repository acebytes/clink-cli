"""Parse Antigravity native stream JSON without retaining tool payloads."""

from __future__ import annotations

import re
from typing import Any

from clink.native_contracts import ParsedNativeResponse
from clink.parsers.base import ParserError, bounded_counts, bounded_number, cli_failure, load_json, model_identifier

RESULT_CHARACTERS = 20000
MAX_EVENTS = 16384
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_STATUSES = {"SUCCESS", "ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING", "RUNNING", "UNKNOWN"}
_FINISHED_REASONS = {"", "print_timeout", "interrupted", "error"}
_USAGE_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")


def validate_session_id(value: str | None) -> str | None:
    """Keep private conversation identities bounded and safe for native arguments."""

    if value is not None and (not isinstance(value, str) or not _SESSION.fullmatch(value)):
        raise ParserError("Antigravity native session identity is invalid")
    return value


class AgyNativeParser:
    """Require the installed init/step_update/result dialect and terminal outcome."""

    def parse(self, stdout: str, stderr: str = "") -> ParsedNativeResponse:
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines or len(lines) > MAX_EVENTS:
            raise ParserError("Antigravity native output was empty or exceeded the event limit")
        terminal: dict[str, Any] | None = None
        session_id = None
        initialized = False
        failed_steps: set[int] = set()
        reported_model = None
        for line in lines:
            event = load_json(line)
            if (
                not isinstance(event, dict)
                or not isinstance(event.get("event"), str)
                or event["event"] not in {"init", "step_update", "result"}
            ):
                raise ParserError("Antigravity native output used an unsupported stream dialect")
            if any(name in event for name in ("type", "status")):
                raise ParserError("Antigravity native output mixed JSON dialects")
            if terminal is not None:
                raise ParserError("Antigravity native output continued after its terminal result")
            kind = event["event"]
            body = event.get(kind)
            if not isinstance(body, dict):
                raise ParserError("Antigravity native events must contain typed payload objects")
            observed = event.get("conversation_id") if kind == "init" else body.get("conversation_id")
            identity_container = event if kind == "init" else body
            if "conversation_id" in identity_container and observed is None:
                raise ParserError("Antigravity native conversation identity is invalid")
            if observed is not None:
                validate_session_id(observed)
                if session_id is not None and session_id != observed:
                    raise ParserError("Antigravity native output changed its conversation identity")
                session_id = observed
            if kind == "init":
                if initialized or observed is None:
                    raise ParserError("Antigravity native initialization is invalid")
                initialized = True
                reported_model = model_identifier(body.get("model"))
            elif kind == "step_update":
                if observed is None:
                    raise ParserError("Antigravity native step omitted its conversation identity")
                self._step_error(body, failed_steps)
            else:
                terminal = body
        if terminal is None:
            raise cli_failure(stderr, message="Antigravity native output omitted its terminal result")
        status = terminal.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise ParserError("Antigravity native terminal status is invalid")
        finished_reason = terminal.get("finished_reason", "")
        if not isinstance(finished_reason, str) or finished_reason not in _FINISHED_REASONS:
            raise ParserError("Antigravity native terminal finish reason is invalid")
        error = terminal.get("error", "")
        if not isinstance(error, str):
            raise ParserError("Antigravity native terminal error must be textual")
        content = terminal.get("response")
        if not isinstance(content, str):
            raise ParserError("Antigravity native response must be textual")
        denials = terminal.get("denied_actions", [])
        if not isinstance(denials, list) or len(denials) > MAX_EVENTS:
            raise ParserError("Antigravity native denied actions must be a bounded array")
        for denial in denials:
            if (
                not isinstance(denial, dict)
                or not isinstance(denial.get("display_name"), str)
                or ("action" in denial and not isinstance(denial["action"], str))
            ):
                raise ParserError("Antigravity native denied action is invalid")
        if "usage" in terminal and not isinstance(terminal["usage"], dict):
            raise ParserError("Antigravity native usage must be an object")
        success = status == "SUCCESS" and not finished_reason and not error
        if success and terminal.get("conversation_id") is None:
            raise ParserError("Antigravity native successful result omitted its conversation identity")
        state = "permission_needed" if denials else "completed" if success else "failed"
        metadata: dict[str, Any] = {
            "tool_error_count": len(failed_steps),
            "permission_denial_count": len(denials),
            "terminal_status": status,
            "truncated": len(content) > RESULT_CHARACTERS,
        }
        if finished_reason:
            metadata["finished_reason"] = finished_reason
        # The optional init model echoes an override, not the default model selected.
        if reported_model is not None:
            metadata["native_reported_model"] = reported_model
        duration = bounded_number(terminal.get("duration_seconds"))
        if duration is not None:
            metadata["duration_seconds"] = duration
        metadata.update(bounded_counts(terminal, ("num_turns",)))
        if usage := bounded_counts(terminal.get("usage"), _USAGE_FIELDS):
            metadata["usage"] = usage
        if state == "failed":
            metadata["error_code"] = cli_failure(terminal, stderr, code="native_error").code
        return ParsedNativeResponse(state, content[:RESULT_CHARACTERS], session_id, metadata)

    @staticmethod
    def _step_error(step: dict[str, Any], failed_steps: set[int]) -> None:
        """Count reported step errors once while excluding their raw arguments/output."""

        index = step.get("step_index")
        # Installed StepUpdatePayload omits the empty step_type string on the wire.
        step_type = step.get("step_type", "")
        step_state = step.get("state")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or not isinstance(step_state, str)
            or step_state not in {"ACTIVE", "DONE", "ERROR"}
            or not isinstance(step_type, str)
            or (step_type != "" and not _SYMBOL.fullmatch(step_type))
        ):
            raise ParserError("Antigravity native step identity or state is invalid")
        if "text_delta" in step and not isinstance(step["text_delta"], str):
            raise ParserError("Antigravity native step text must be textual")
        tool = step.get("tool_info")
        if tool is None:
            return
        if not isinstance(tool, dict):
            raise ParserError("Antigravity native tool information must be an object")
        error = tool.get("error")
        if error is not None:
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("type"), str)
                or not isinstance(error.get("message"), str)
            ):
                raise ParserError("Antigravity native tool error is invalid")
            if step_state in {"DONE", "ERROR"}:
                failed_steps.add(index)
