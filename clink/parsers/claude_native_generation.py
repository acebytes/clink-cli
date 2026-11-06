"""Validate Claude generation streams without publishing tool or intermediate text."""

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

MAX_EVENTS = 16384
MAX_STREAM_BYTES = 10 * 1024 * 1024
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_NOTICE_SUBTYPES = frozenset(
    {
        "commands_changed",
        "session_title_changed",
        "informational",
        "instruction_size_warning",
        "api_retry",
        "thinking_tokens",
    }
)
_SYSTEM_SUBTYPES = (
    frozenset(
        {"init", "status", "compact_boundary", "hook_started", "hook_progress", "hook_response", "permission_denied"}
    )
    | _NOTICE_SUBTYPES
)
_RETRY_ERRORS = frozenset(
    {"overloaded", "rate_limit", "authentication_failed", "server_error", "cloud_credential_error", "unknown"}
)
_DIAGNOSTIC_REASONS = frozenset(
    {
        "stream_bounds",
        "json_invalid",
        "event_envelope",
        "unknown_event",
        "unknown_system_subtype",
        "notice_shape",
        "notice_stop",
        "session_invalid",
        "session_mismatch",
        "init_required",
        "duplicate_init",
        "init_model_invalid",
        "message_shape",
        "native_failure",
        "permission_denied",
        "rate_limited",
        "terminal_shape",
        "empty_result",
        "post_terminal",
        "terminal_missing",
    }
)
_DIAGNOSTIC_PREFIX = "Claude native generation output violates its protocol"
_DIAGNOSTIC = re.compile(re.escape(_DIAGNOSTIC_PREFIX) + r" \[reason=([a-z_]+);event_index=(null|[1-9][0-9]{0,4})\]\Z")
PARSER_CONTRACT = (
    "claude-native-generation-stream-json/v3;strict-utf8-jsonl;no-duplicate-keys-or-nonfinite-numbers;"
    "max-events=16384;max-stream-bytes=10485760;one-init-before-messages;one-session;one-last-result;"
    "answer=exact-nonblank-successful-terminal-result.result;primary-model=system.init.model;"
    "modelUsage-is-not-primary-model;errors-and-permission-denials-refuse;no-raw-metadata;"
    "six-validated-operational-system-notices;thinking-progress-after-init;only-validated-api-retry-error-is-recoverable;"
    "informational-stop-and-prompt-submit-outcome-refuse;closed-diagnostic-reason-and-event-index;"
    "identity=sha256(canonical-json(contract,source-sha256(claude_native_generation.py,base.py)))"
)


class _ClaudeParseError(ParserError):
    def __init__(self, reason: str, event_index: int | None = None, *, code: str = "protocol_error"):
        self.reason = reason
        index = "null" if event_index is None else str(event_index)
        super().__init__(f"{_DIAGNOSTIC_PREFIX} [reason={reason};event_index={index}]", code=code)


def _protocol(reason: str = "event_envelope") -> ParserError:
    return _ClaudeParseError(reason)


def protocol_diagnostic(message: object) -> dict[str, Any] | None:
    """Extract only a closed Claude diagnostic, never native output or arbitrary errors."""
    match = _DIAGNOSTIC.fullmatch(message) if isinstance(message, str) else None
    if match is None or match[1] not in _DIAGNOSTIC_REASONS:
        return None
    index = None if match[2] == "null" else int(match[2])
    if index is not None and index > MAX_EVENTS:
        return None
    return {"reason": match[1], "event_index": index}


def parser_contract_digest() -> str:
    sources = {}
    try:
        for name in ("claude_native_generation.py", "base.py"):
            with Path(__file__).with_name(name).open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise _protocol("stream_bounds")
            sources[name] = hashlib.sha256(content).hexdigest()
    except OSError:
        raise _protocol("stream_bounds") from None
    identity = json.dumps(
        {"contract": PARSER_CONTRACT, "source_sha256": sources}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _valid_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _validate_json(value: Any) -> None:
    pending = [value]
    while pending:
        part = pending.pop()
        if isinstance(part, dict):
            if not all(_valid_text(key) for key in part):
                raise _protocol("json_invalid")
            pending.extend(part.values())
        elif isinstance(part, list):
            pending.extend(part)
        elif isinstance(part, str) and not _valid_text(part):
            raise _protocol("json_invalid")
        elif isinstance(part, float) and not math.isfinite(part):
            raise _protocol("json_invalid")


def _integer(value: object, minimum: int = 0, maximum: int = MAX_METADATA_NUMBER) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _notice(event: dict[str, Any]) -> None:
    subtype = event["subtype"]
    if subtype == "session_title_changed":
        valid = isinstance(event.get("title"), str) and bool(event["title"].strip())
    elif subtype == "commands_changed":
        commands = event.get("commands")
        valid = isinstance(commands, list) and all(
            isinstance(command, dict)
            and all(isinstance(command.get(field), str) for field in ("name", "description", "argumentHint"))
            and (
                "aliases" not in command
                or isinstance(command["aliases"], list)
                and all(isinstance(alias, str) for alias in command["aliases"])
            )
            and ("builtin" not in command or type(command["builtin"]) is bool)
            for command in commands
        )
    elif subtype == "informational":
        level = event.get("level")
        valid = (
            isinstance(event.get("content"), str)
            and isinstance(level, str)
            and level in {"info", "notice", "suggestion", "warning"}
            and ("tool_use_id" not in event or isinstance(event["tool_use_id"], str))
            and ("prevent_continuation" not in event or type(event["prevent_continuation"]) is bool)
        )
    elif subtype == "thinking_tokens":
        valid = all(
            _integer(event.get(field), -MAX_METADATA_NUMBER) for field in ("estimated_tokens", "estimated_tokens_delta")
        ) and ("user_message_uuid" not in event or isinstance(event["user_message_uuid"], str))
    elif subtype == "instruction_size_warning":
        valid = all(_integer(event.get(field)) for field in ("total_chars", "total_limit_chars", "file_count")) and (
            "largest_chars" not in event or _integer(event["largest_chars"])
        )
    else:
        error = event.get("error")
        response = event.get("no_response")
        valid = (
            _integer(event.get("attempt"), 1)
            and _integer(event.get("max_retries"))
            and bounded_number(event.get("retry_delay_ms")) is not None
            and "error_status" in event
            and (event["error_status"] is None or _integer(event["error_status"]))
            and isinstance(error, str)
            and error in _RETRY_ERRORS
            and (
                "no_response" not in event
                or isinstance(response, dict)
                and all(bounded_number(response.get(field)) is not None for field in ("waited_ms", "retry_wait_ms"))
            )
        )
    if not valid:
        raise _protocol("notice_shape")


def _failure(event: dict[str, Any], stderr: str, *, validated_retry: bool = False) -> None:
    if "is_error" in event and type(event["is_error"]) is not bool:
        raise _protocol()
    if "errors" in event and not isinstance(event["errors"], list):
        raise _protocol()
    status = event.get("status")
    subtype = event.get("subtype")
    if status is not None and not isinstance(status, str):
        raise _protocol()
    if subtype is not None and not isinstance(subtype, str):
        raise _protocol()
    if (
        event.get("type") == "error"
        or event.get("is_error") is True
        or (event.get("error") is not None and not validated_retry)
        or event.get("errors")
        or status in {"error", "failed", "cancelled", "aborted", "interrupted"}
        or (isinstance(subtype, str) and subtype.startswith("error"))
    ):
        raise cli_failure(event, stderr, message="Claude native generation did not complete", code="protocol_error")


class ClaudeNativeGenerationParser(BaseParser):
    """Publish only one validated final result, preserving its exact text."""

    name = "claude_native_generation_jsonl"

    def parse(self, stdout: str, stderr: str = "") -> ParsedCLIResponse:
        if not _valid_text(stdout) or not _valid_text(stderr):
            raise _protocol("json_invalid")
        if len(stdout.encode()) > MAX_STREAM_BYTES:
            raise _protocol("stream_bounds")
        lines = [line for line in stdout.split("\n") if line.strip(" \t\r")]
        if len(lines) > MAX_EVENTS:
            raise _protocol("stream_bounds")
        if not lines:
            failure = cli_failure(stderr, code="protocol_error")
            raise _ClaudeParseError("terminal_missing", code=failure.code)
        initialized = False
        session: str | None = None
        actual_model: str | None = None
        terminal: dict[str, Any] | None = None
        for event_index, line in enumerate(lines, 1):
            try:
                try:
                    event = load_json(line)
                except ParserError:
                    raise _protocol("json_invalid") from None
                _validate_json(event)
                if terminal is not None:
                    raise _protocol("post_terminal")
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise _protocol("event_envelope")
                kind = event["type"]
                subtype = event.get("subtype")
                notice = kind == "system" and isinstance(subtype, str) and subtype in _NOTICE_SUBTYPES
                if "permission_denials" in event:
                    denials = event["permission_denials"]
                    if not isinstance(denials, list):
                        raise _protocol("terminal_shape" if kind == "result" else "event_envelope")
                    if denials:
                        raise _ClaudeParseError("permission_denied", code="permission_needed")
                if notice:
                    _notice(event)
                _failure(event, stderr, validated_retry=notice and subtype == "api_retry")
                observed = event.get("session_id")
                if observed is not None:
                    if not isinstance(observed, str) or not _SESSION.fullmatch(observed):
                        raise _protocol("session_invalid")
                    if session is not None and session != observed:
                        raise _protocol("session_mismatch")
                    session = observed
                if notice and observed is None:
                    raise _protocol("session_invalid")
                if kind == "system":
                    if subtype not in _SYSTEM_SUBTYPES:
                        raise _protocol("unknown_system_subtype")
                    if subtype == "permission_denied":
                        raise _ClaudeParseError("permission_denied", code="permission_needed")
                    if subtype == "thinking_tokens" and not initialized:
                        raise _protocol("init_required")
                    if subtype == "informational" and (
                        event.get("prevent_continuation") is True or "prompt_submit_outcome" in event
                    ):
                        raise _protocol("notice_stop")
                    if subtype == "init":
                        if initialized:
                            raise _protocol("duplicate_init")
                        actual_model = model_identifier(event.get("model"))
                        if observed is None:
                            raise _protocol("session_invalid")
                        if actual_model is None:
                            raise _protocol("init_model_invalid")
                        initialized = True
                elif kind in {"assistant", "user"}:
                    if not initialized:
                        raise _protocol("init_required")
                    if not isinstance(event.get("message"), dict):
                        raise _protocol("message_shape")
                    blocks = event["message"].get("content")
                    if not isinstance(blocks, list):
                        raise _protocol("message_shape")
                    for block in blocks:
                        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                            raise _protocol("message_shape")
                        _failure(block, stderr)
                        if block["type"] == "text" and not isinstance(block.get("text"), str):
                            raise _protocol("message_shape")
                elif kind == "result":
                    if not initialized:
                        raise _protocol("init_required")
                    if (
                        observed is None
                        or event.get("subtype") != "success"
                        or event.get("is_error") is not False
                        or event.get("status", "success") not in {"success", "completed"}
                        or not isinstance(event.get("result"), str)
                    ):
                        raise _protocol("terminal_shape")
                    if not event["result"].strip():
                        raise _ClaudeParseError("empty_result", code="empty_response")
                    terminal = event
                elif kind == "rate_limit_event":
                    if not initialized:
                        raise _protocol("init_required")
                    if not isinstance(event.get("rate_limit_info"), dict):
                        raise _protocol("event_envelope")
                    if event["rate_limit_info"].get("status") == "rejected":
                        raise _ClaudeParseError("rate_limited", code="rate_limited")
                else:
                    raise _protocol("unknown_event")
            except ParserError as error:
                reason = error.reason if isinstance(error, _ClaudeParseError) else "native_failure"
                raise _ClaudeParseError(reason, event_index, code=error.code) from None
        if terminal is None:
            failure = cli_failure(stderr, code="protocol_error")
            raise _ClaudeParseError("terminal_missing", code=failure.code)
        return ParsedCLIResponse(
            content=terminal["result"],
            metadata={"actual_model": actual_model, "tool_error_count": 0, "permission_denial_count": 0},
        )
