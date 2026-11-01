"""Parse Grok native JSON and documented headless refusal diagnostics."""

from __future__ import annotations

import re
from typing import Any

from clink.native_contracts import ParsedNativeResponse
from clink.parsers.base import ParserError, bounded_counts, cli_failure, load_json, model_identifier

RESULT_CHARACTERS = 20000
MAX_EVENTS = 16384
_SESSION = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_PERMISSION_REFUSAL = re.compile(
    r"permission required: [^\r\n]+ was not run, as a headless run cannot ask for approval\Z"
)
_STOP_REASONS = {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def validate_session_id(value: str | None) -> str | None:
    """Accept only the UUID identity consumed by Grok's native session flags."""

    if value is not None and (not isinstance(value, str) or not _SESSION.fullmatch(value)):
        raise ParserError("Grok native session identity is invalid")
    return value


class GrokNativeParser:
    """Keep native execution outcomes separate from advisory answer validity."""

    def parse(self, stdout: str, stderr: str = "") -> ParsedNativeResponse:
        denials = min(MAX_EVENTS, sum(bool(_PERMISSION_REFUSAL.fullmatch(line)) for line in stderr.splitlines()))
        if not stdout.strip():
            if denials:
                return ParsedNativeResponse(
                    "permission_needed", metadata={"permission_denial_count": denials, "truncated": False}
                )
            raise cli_failure(stderr, message="Grok native output was empty")
        payload = load_json(stdout)
        if not isinstance(payload, dict):
            raise ParserError("Grok native JSON must be an object")
        if any(name in payload for name in ("event", "status")) or ("type" in payload and payload["type"] != "error"):
            raise ParserError("Grok native output used an unsupported JSON dialect")
        if "is_error" in payload and not isinstance(payload["is_error"], bool):
            raise ParserError("Grok native error flag must be boolean")
        if "errors" in payload and payload["errors"] is not None and not isinstance(payload["errors"], list):
            raise ParserError("Grok native errors must be an array")
        session_id = validate_session_id(payload.get("sessionId"))
        error = (
            payload.get("type") == "error"
            or payload.get("error") is not None
            or bool(payload.get("errors"))
            or payload.get("is_error") is True
        )
        reason = payload.get("stopReason")
        if reason is not None and (not isinstance(reason, str) or reason not in _STOP_REASONS):
            raise ParserError("Grok native terminal stop reason is invalid")
        if reason is None and not error:
            raise ParserError("Grok native output omitted its terminal stop reason")
        if not error and session_id is None:
            raise ParserError("Grok native terminal result omitted its session identity")
        content = payload.get("text", "") if error else payload.get("text")
        if not isinstance(content, str):
            raise ParserError("Grok native result must be textual")
        state = "permission_needed" if denials else "completed" if reason == "end_turn" and not error else "failed"
        metadata: dict[str, Any] = {
            "permission_denial_count": denials,
            "truncated": len(content) > RESULT_CHARACTERS,
        }
        if reason is not None:
            metadata["terminal_stop_reason"] = reason
        metadata.update(bounded_counts(payload, ("num_turns",)))
        if usage := bounded_counts(payload.get("usage"), _USAGE_FIELDS):
            metadata["usage"] = usage
        model_usage = payload.get("modelUsage")
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            model, stats = next(iter(model_usage.items()))
            actual_model = model_identifier(model)
            if actual_model is not None and actual_model != "unknown" and isinstance(stats, dict):
                metadata["actual_model"] = actual_model
        if state == "failed":
            metadata["error_code"] = cli_failure(payload, stderr, code="native_error").code
        return ParsedNativeResponse(state, content[:RESULT_CHARACTERS], session_id, metadata)
