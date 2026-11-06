"""Parser for Gemini CLI JSON output; execution availability is adapter policy."""

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

_TOKEN_FIELDS = ("prompt", "candidates", "total", "cached", "thoughts", "tool")


class GeminiJSONParser(BaseParser):
    """Require response text and reject explicit Gemini errors."""

    name = "gemini_json"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        if not stdout.strip():
            raise cli_failure(stderr, message="Gemini CLI returned empty stdout")

        payload = load_json(stdout)
        if not isinstance(payload, dict):
            raise ParserError("Gemini CLI JSON must be an object")
        if "type" in payload and not isinstance(payload["type"], str):
            raise ParserError("Gemini CLI response type must be textual")
        if "status" in payload and not isinstance(payload["status"], str):
            raise ParserError("Gemini CLI response status must be textual")
        if (
            payload.get("type") in ("error", "failed")
            or payload.get("status") in ("error", "failed", "cancelled", "aborted", "interrupted")
            or payload.get("error") is not None
            or payload.get("errors")
            or payload.get("is_error", False) is not False
        ):
            raise cli_failure(payload, stderr, message="Gemini CLI reported a failure")
        if payload.get("status", "success") not in ("completed", "success"):
            raise ParserError("Gemini CLI response status did not indicate success")

        metadata = self._metadata(payload, stderr)
        response = payload.get("response")
        if not isinstance(response, str):
            raise ParserError("Gemini CLI response must be textual")
        if not response.strip():
            raise cli_failure(stderr, message="Gemini CLI response was empty", code="empty_response")

        return ParsedCLIResponse(content=response.strip(), metadata=metadata)

    def _metadata(self, payload: dict[str, Any], stderr: str) -> dict[str, Any]:
        stats = payload.get("stats", {})
        if not isinstance(stats, dict):
            raise ParserError("Gemini CLI stats must be an object")
        pending: list[object] = [stats]
        while pending:
            record = pending.pop()
            if isinstance(record, list):
                pending.extend(record)
            elif isinstance(record, dict):
                if "status" in record and not isinstance(record["status"], str):
                    raise ParserError("Gemini CLI stats status must be textual")
                if (
                    record.get("type") in ("error", "failed")
                    or record.get("status") in ("error", "failed", "cancelled", "aborted", "interrupted")
                    or record.get("error") is not None
                    or record.get("errors")
                    or record.get("is_error", False) is not False
                ):
                    raise cli_failure(record, stderr, message="Gemini CLI stats reported a failure")
                for field in ("totalErrors", "totalFail", "fail"):
                    if field not in record:
                        continue
                    errors = record[field]
                    if not isinstance(errors, int) or isinstance(errors, bool) or errors < 0:
                        raise ParserError("Gemini CLI failure counts must be nonnegative integers")
                    if errors:
                        raise cli_failure(stderr, message="Gemini CLI stats reported a failure")
                pending.extend(record.values())
        models = stats.get("models", {})
        if not isinstance(models, dict):
            raise ParserError("Gemini CLI model stats must be objects")

        metadata: dict[str, Any] = {}
        for model_stats in models.values():
            if not isinstance(model_stats, dict):
                raise ParserError("Gemini CLI model stats must be objects")
            api_stats = model_stats.get("api", {})
            if not isinstance(api_stats, dict):
                raise ParserError("Gemini CLI API stats must be an object")

        if len(models) == 1:
            model_name, model_stats = next(iter(models.items()))
            actual_model = model_identifier(model_name)
            if actual_model:
                metadata["actual_model"] = actual_model
            tokens = bounded_counts(model_stats.get("tokens"), _TOKEN_FIELDS)
            if tokens:
                metadata["usage"] = tokens
            latency = bounded_number(model_stats.get("api", {}).get("totalLatencyMs"))
            if latency is not None:
                metadata["latency_ms"] = latency

        return metadata
