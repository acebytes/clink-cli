"""Validate Grok's single native-generation final object without publishing diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .base import (
    MAX_METADATA_NUMBER,
    BaseParser,
    ParsedCLIResponse,
    ParserError,
    bounded_number,
    cli_failure,
    load_json,
    model_identifier,
)

MAX_STREAM_BYTES = 10 * 1024 * 1024
OUTPUT_BYTES = 131072
_SESSION = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_PERMISSION_REFUSAL = re.compile(
    r"permission required: [^\r\n]+ was not run, as a headless run cannot ask for approval\Z"
)
_MODEL_USAGE_COUNTS = frozenset({"inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"})
PARSER_CONTRACT = (
    "grok-native-generation-json/v1;strict-utf8-json;no-duplicate-keys-or-nonfinite-numbers;"
    "max-stream-bytes=10485760;one-final-object;session-uuid;stopReason=end_turn;"
    "answer=exact-nonblank-text;max-final-utf8-bytes=131072;overflow-refused;"
    "actual-model=one-valid-modelUsage-entry-with-valid-recognized-usage-counts-and-no-incomplete-ledger;"
    "errors-incomplete-and-source-supported-headless-refusal-refuse;no-raw-metadata;"
    "identity=sha256(canonical-json(contract,source-sha256(grok_native_generation.py,base.py)))"
)


def _protocol() -> ParserError:
    return ParserError("Grok native generation output violates its protocol", code="protocol_error")


def parser_contract_digest() -> str:
    sources = {}
    try:
        for name in ("grok_native_generation.py", "base.py"):
            with Path(__file__).with_name(name).open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise _protocol()
            sources[name] = hashlib.sha256(content).hexdigest()
    except OSError:
        raise _protocol() from None
    identity = json.dumps(
        {"contract": PARSER_CONTRACT, "source_sha256": sources}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _validate_json(value: Any) -> None:
    pending = [value]
    while pending:
        part = pending.pop()
        if isinstance(part, dict):
            pending.extend(part.keys())
            pending.extend(part.values())
        elif isinstance(part, list):
            pending.extend(part)
        elif isinstance(part, str):
            try:
                part.encode("utf-8")
            except UnicodeError:
                raise _protocol() from None
        elif isinstance(part, float) and not math.isfinite(part):
            raise _protocol()


def _actual_model(value: object) -> str | None:
    if not isinstance(value, dict) or len(value) != 1:
        return None
    model, usage = next(iter(value.items()))
    model = model_identifier(model)
    if model is None or model == "unknown" or not isinstance(usage, dict):
        return None
    counts = {key: value for key, value in usage.items() if key in _MODEL_USAGE_COUNTS}
    if not counts or any(type(count) is not int or not 0 <= count <= MAX_METADATA_NUMBER for count in counts.values()):
        return None
    if "modelCalls" in usage and (
        type(usage["modelCalls"]) is not int or not 1 <= usage["modelCalls"] <= MAX_METADATA_NUMBER
    ):
        return None
    if not any(counts.values()) and usage.get("modelCalls", 0) == 0:
        return None
    if "costUSD" in usage and bounded_number(usage["costUSD"]) is None:
        return None
    return model


class GrokNativeGenerationParser(BaseParser):
    """Publish one successful final text, preserving bytes and refusing truncation."""

    name = "grok_native_generation_json"

    def parse(self, stdout: str, stderr: str = "") -> ParsedCLIResponse:
        try:
            if len(stdout.encode("utf-8")) > MAX_STREAM_BYTES:
                raise _protocol()
            stderr.encode("utf-8")
        except (AttributeError, UnicodeError):
            raise _protocol() from None
        if any(_PERMISSION_REFUSAL.fullmatch(line) for line in stderr.splitlines()):
            raise ParserError("Grok native generation requires permission", code="permission_needed")
        if not stdout.strip():
            raise cli_failure(stderr, message="Grok native generation omitted its final output", code="protocol_error")
        try:
            payload = load_json(stdout)
        except ParserError:
            raise _protocol() from None
        _validate_json(payload)
        if not isinstance(payload, dict):
            raise _protocol()
        if any(name in payload for name in ("event", "status")) or ("type" in payload and payload["type"] != "error"):
            raise _protocol()
        if "is_error" in payload and type(payload["is_error"]) is not bool:
            raise _protocol()
        if "errors" in payload and payload["errors"] is not None and not isinstance(payload["errors"], list):
            raise _protocol()
        if (
            payload.get("type") == "error"
            or payload.get("is_error") is True
            or payload.get("error") is not None
            or payload.get("errors")
        ):
            raise cli_failure(payload, stderr, message="Grok native generation did not complete", code="protocol_error")
        session = payload.get("sessionId")
        if not isinstance(session, str) or not _SESSION.fullmatch(session) or payload.get("stopReason") != "end_turn":
            raise _protocol()
        text = payload.get("text")
        if not isinstance(text, str):
            raise _protocol()
        if not text.strip():
            raise ParserError("Grok native generation final response was empty", code="empty_response")
        if len(text.encode("utf-8")) > OUTPUT_BYTES:
            raise ParserError("Grok native generation final response exceeded its byte limit", code="output_limit")
        return ParsedCLIResponse(
            content=text,
            metadata={
                "actual_model": (
                    _actual_model(payload.get("modelUsage"))
                    if payload.get("usage_is_incomplete", False) is False
                    else None
                ),
                "tool_error_count": 0,
                "permission_denial_count": 0,
            },
        )
