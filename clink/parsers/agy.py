"""Parse Antigravity's single-document JSON result without admitting execution."""

from __future__ import annotations

from typing import Any

from .base import BaseParser, ParsedCLIResponse, ParserError, bounded_counts, bounded_number, cli_failure, load_json

_USAGE_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")


class AgyJSONParser(BaseParser):
    """Require an explicit SUCCESS envelope and nonempty response text."""

    name = "agy_json"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        if not stdout.strip():
            raise cli_failure(stderr, message="Antigravity CLI returned empty stdout")

        payload = load_json(stdout)
        if not isinstance(payload, dict):
            raise ParserError("Antigravity CLI JSON must be an object")
        if (
            payload.get("status") == "ERROR"
            or payload.get("type") in ("error", "failed")
            or payload.get("error") is not None
            or ("errors" in payload and payload["errors"] is not None and payload["errors"] != [])
            or payload.get("is_error", False) is not False
        ):
            raise cli_failure(payload, stderr, message="Antigravity CLI reported a failure")
        if payload.get("status") != "SUCCESS":
            raise ParserError("Antigravity CLI did not include a successful terminal status")
        if "event" in payload or "type" in payload:
            raise ParserError("Antigravity CLI returned an unsupported JSON dialect")

        response = payload.get("response")
        if not isinstance(response, str):
            raise ParserError("Antigravity CLI response must be textual")
        if not response.strip():
            raise cli_failure(stderr, message="Antigravity CLI response was empty", code="empty_response")

        metadata: dict[str, Any] = {}
        duration = bounded_number(payload.get("duration_seconds"))
        if duration is not None:
            metadata["duration_seconds"] = duration
        metadata.update(bounded_counts(payload, ("num_turns",)))
        usage = bounded_counts(payload.get("usage"), _USAGE_FIELDS)
        if usage:
            metadata["usage"] = usage

        # The JSON envelope does not independently identify the actual model.
        return ParsedCLIResponse(content=response.strip(), metadata=metadata)
