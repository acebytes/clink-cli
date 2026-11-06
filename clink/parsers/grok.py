"""Parse Grok Build's single-document JSON result without admitting execution."""

from __future__ import annotations

from typing import Any

from .base import BaseParser, ParsedCLIResponse, ParserError, bounded_counts, cli_failure, load_json, model_identifier

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
    "total_tokens",
)


class GrokJSONParser(BaseParser):
    """Require a complete end_turn response, rejecting partial or error results."""

    name = "grok_json"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        if not stdout.strip():
            raise cli_failure(stderr, message="Grok CLI returned empty stdout")

        payload = load_json(stdout)
        if not isinstance(payload, dict):
            raise ParserError("Grok CLI JSON must be an object")
        if (
            payload.get("type") in ("error", "failed")
            or payload.get("error") is not None
            or ("errors" in payload and payload["errors"] is not None and payload["errors"] != [])
            or payload.get("is_error", False) is not False
        ):
            raise cli_failure(payload, stderr, message="Grok CLI reported a failure")
        if "type" in payload or "event" in payload or "status" in payload:
            raise ParserError("Grok CLI returned an unsupported JSON dialect")
        # Installed 1.0.70 documents end_turn, max_tokens, max_turn_requests,
        # refusal and cancelled. Only end_turn establishes a complete answer.
        if payload.get("stopReason") != "end_turn":
            raise ParserError("Grok CLI did not include a complete terminal stop reason")

        text = payload.get("text")
        if not isinstance(text, str):
            raise ParserError("Grok CLI response must be textual")
        if not text.strip():
            raise cli_failure(stderr, message="Grok CLI response was empty", code="empty_response")

        metadata: dict[str, Any] = bounded_counts(payload, ("num_turns",))
        usage = bounded_counts(payload.get("usage"), _USAGE_FIELDS)
        if usage:
            metadata["usage"] = usage

        model_usage = payload.get("modelUsage")
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            model_name, model_stats = next(iter(model_usage.items()))
            actual_model = model_identifier(model_name)
            if actual_model and actual_model != "unknown" and isinstance(model_stats, dict):
                metadata["actual_model"] = actual_model

        return ParsedCLIResponse(content=text.strip(), metadata=metadata)
