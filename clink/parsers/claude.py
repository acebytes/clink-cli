"""Parser for Claude CLI JSON output."""

from __future__ import annotations

from typing import Any

from .base import (
    BaseParser,
    ParsedCLIResponse,
    ParserError,
    bounded_counts,
    bounded_number,
    cli_failure,
    load_json,
    model_identifier,
)

_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class ClaudeJSONParser(BaseParser):
    """Require a successful terminal result from Claude's JSON envelope."""

    name = "claude_json"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        if not stdout.strip():
            raise cli_failure(stderr, message="Claude CLI returned empty stdout")

        loaded = load_json(stdout)
        if isinstance(loaded, dict):
            events = [loaded]
        elif isinstance(loaded, list) and loaded and all(isinstance(event, dict) for event in loaded):
            events = loaded
        else:
            raise ParserError("Claude CLI JSON must be an object or a nonempty array of objects")

        for index, event in enumerate(events):
            event_type = event.get("type")
            subtype = event.get("subtype")
            if (
                not isinstance(event_type, str)
                or not event_type
                or (subtype is not None and not isinstance(subtype, str))
            ):
                raise ParserError("Claude CLI events must contain valid type fields")
            if "status" in event and not isinstance(event["status"], str):
                raise ParserError("Claude CLI event status must be textual")
            if (
                event_type == "error"
                or event.get("status") in ("error", "failed", "cancelled", "aborted", "interrupted")
                or event.get("is_error", False) is not False
                or event.get("error") is not None
                or event.get("errors")
                or (isinstance(subtype, str) and subtype.lower().startswith("error"))
            ):
                raise cli_failure(event, event.get("result"), stderr, message="Claude CLI reported a failed result")
            if event_type == "result" and index != len(events) - 1:
                raise ParserError("Claude CLI emitted events after its terminal result")
            message = event.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    subtype = block.get("subtype")
                    if block.get("type") == "error" or (
                        block.get("type") == "tool_result"
                        and (
                            block.get("is_error", False) is not False
                            or block.get("error") is not None
                            or (isinstance(subtype, str) and subtype.lower().startswith("error"))
                        )
                    ):
                        raise cli_failure(block, stderr, message="Claude CLI tool execution failed")

        payload = events[-1]
        if payload["type"] != "result" or payload.get("subtype") != "success":
            raise cli_failure(payload, stderr, message="Claude CLI did not include a successful terminal result")
        if payload.get("status", "success") not in ("completed", "success"):
            raise ParserError("Claude CLI terminal status did not indicate success")

        result = payload.get("result")
        if not isinstance(result, str):
            raise ParserError("Claude CLI result must be textual")
        if not result.strip():
            raise cli_failure(stderr, message="Claude CLI result was empty", code="empty_response")

        metadata: dict[str, Any] = {}
        for field in ("duration_ms", "duration_api_ms", "num_turns"):
            number = bounded_number(payload.get(field))
            if number is not None:
                metadata[field] = number
        usage = bounded_counts(payload.get("usage"), _USAGE_FIELDS)
        if usage:
            metadata["usage"] = usage

        model_usage = payload.get("modelUsage")
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            actual_model = model_identifier(next(iter(model_usage)))
            if actual_model and isinstance(model_usage[actual_model], dict):
                metadata["actual_model"] = actual_model

        return ParsedCLIResponse(content=result.strip(), metadata=metadata)
