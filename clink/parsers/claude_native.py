"""Parse native Claude NDJSON without retaining raw events or tool arguments."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from clink.parsers.base import ParserError, bounded_counts, bounded_number, cli_failure, load_json, model_identifier

RESULT_CHARACTERS = 20000
MAX_EVENTS = 16384
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SUBTYPE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def validate_session_id(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or not _SESSION.fullmatch(value)):
        raise ParserError("Claude native session identity is invalid")
    return value


@dataclass(frozen=True)
class ParsedNativeResponse:
    """Private session identity is deliberately separate from public metadata."""

    state: str
    content: str = ""
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ClaudeNativeParser:
    """Use terminal status; recoverable tool errors do not invalidate success."""

    def parse(self, stdout: str, stderr: str = "") -> ParsedNativeResponse:
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines or len(lines) > MAX_EVENTS:
            raise ParserError("Claude native output was empty or exceeded the event limit")
        terminal: dict[str, Any] | None = None
        session_id = None
        permission_denials = 0
        tool_errors = 0
        fatal = False
        for line in lines:
            event = load_json(line)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not event["type"]:
                raise ParserError("Claude native events must be JSON objects with a type")
            if terminal is not None:
                raise ParserError("Claude native output continued after its terminal result")
            observed_session = event.get("session_id")
            if observed_session is not None:
                validate_session_id(observed_session)
                if session_id is not None and session_id != observed_session:
                    raise ParserError("Claude native output changed its session identity")
                session_id = observed_session
            subtype = event.get("subtype")
            if subtype is not None and (not isinstance(subtype, str) or not _SUBTYPE.fullmatch(subtype)):
                raise ParserError("Claude native event subtype is invalid")
            if event["type"] == "system" and subtype == "permission_denied":
                permission_denials += 1
            elif event["type"] == "error":
                fatal = True
            elif event["type"] == "result":
                terminal = event
            message = event.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if isinstance(blocks, list):
                tool_errors += sum(
                    1
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") is True
                )
        if terminal is None:
            raise cli_failure(stderr, message="Claude native output had no terminal result")
        if not isinstance(terminal.get("is_error"), bool):
            raise ParserError("Claude native terminal error flag is invalid")
        terminal_status = terminal.get("status", "success")
        if not isinstance(terminal_status, str):
            raise ParserError("Claude native terminal status is invalid")
        denials = terminal.get("permission_denials", [])
        if not isinstance(denials, list):
            raise ParserError("Claude native permission denials must be an array")
        # A denial can appear both in the stream and in the terminal summary.
        permission_denials = max(permission_denials, len(denials))
        metadata: dict[str, Any] = {
            "tool_error_count": tool_errors,
            "permission_denial_count": permission_denials,
            "truncated": False,
        }
        for name in ("duration_ms", "duration_api_ms", "num_turns"):
            number = bounded_number(terminal.get(name))
            if number is not None:
                metadata[name] = number
        if usage := bounded_counts(terminal.get("usage"), _USAGE_FIELDS):
            metadata["usage"] = usage
        model_usage = terminal.get("modelUsage")
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            actual_model = model_identifier(next(iter(model_usage)))
            if actual_model is not None and isinstance(model_usage[actual_model], dict):
                metadata["actual_model"] = actual_model
        subtype = terminal.get("subtype")
        if isinstance(subtype, str):
            metadata["terminal_subtype"] = subtype
        success = (
            subtype == "success"
            and terminal["is_error"] is False
            and terminal_status in {"success", "completed"}
            and terminal.get("error") is None
            and not terminal.get("errors")
            and not fatal
        )
        if success and session_id is None:
            raise ParserError("Claude native successful result omitted its session identity")
        state = "permission_needed" if permission_denials else "completed" if success else "failed"
        content = terminal.get("result") if subtype == "success" else terminal.get("result", "")
        if not isinstance(content, str):
            raise ParserError("Claude native result must be textual")
        metadata["truncated"] = len(content) > RESULT_CHARACTERS
        if state == "failed":
            metadata["error_code"] = cli_failure(terminal, stderr, code="native_error").code
        return ParsedNativeResponse(state, content[:RESULT_CHARACTERS], session_id, metadata)
