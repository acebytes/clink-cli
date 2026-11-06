"""Validate Antigravity generation finals independently of native task admission."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .base import MAX_METADATA_NUMBER, BaseParser, ParsedCLIResponse, ParserError, cli_failure, load_json

MAX_EVENTS = 16384
MAX_STREAM_BYTES = 10 * 1024 * 1024
OUTPUT_BYTES = 131072
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
PARSER_CONTRACT = (
    "agy-native-generation-stream-json/v1;strict-utf8-jsonl;no-duplicate-keys-or-nonfinite-numbers;"
    "max-events=16384;max-stream-bytes=10485760;one-init-before-steps;one-conversation;one-last-result;"
    "answer=exact-nonblank-result.response;status=SUCCESS;empty-finished-reason-and-error;"
    "max-final-utf8-bytes=131072;overflow-refused;init-model-is-an-echo;actual-model=unknown;"
    "errors-incomplete-and-denied-actions-refuse;no-raw-metadata;"
    "identity=sha256(canonical-json(contract,source-sha256(agy_native_generation.py,base.py)))"
)


def _protocol() -> ParserError:
    return ParserError("Antigravity native generation output violates its protocol", code="protocol_error")


def parser_contract_digest() -> str:
    sources = {}
    try:
        for name in ("agy_native_generation.py", "base.py"):
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


def _step(step: dict[str, Any], failed_steps: set[int]) -> None:
    index = step.get("step_index")
    state = step.get("state")
    step_type = step.get("step_type", "")
    if (
        type(index) is not int
        or not 0 <= index <= MAX_METADATA_NUMBER
        or not isinstance(state, str)
        or state not in {"ACTIVE", "DONE", "ERROR"}
        or not isinstance(step_type, str)
        or (step_type and not _SYMBOL.fullmatch(step_type))
        or ("text_delta" in step and not isinstance(step["text_delta"], str))
    ):
        raise _protocol()
    tool = step.get("tool_info")
    if tool is None:
        return
    if not isinstance(tool, dict):
        raise _protocol()
    error = tool.get("error")
    if error is not None:
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("type"), str)
            or not isinstance(error.get("message"), str)
        ):
            raise _protocol()
        if state in {"DONE", "ERROR"}:
            failed_steps.add(index)


class AgyNativeGenerationParser(BaseParser):
    """Publish only the final successful response and bounded failure counts."""

    name = "agy_native_generation_jsonl"

    def parse(self, stdout: str, stderr: str = "") -> ParsedCLIResponse:
        try:
            if len(stdout.encode("utf-8")) > MAX_STREAM_BYTES:
                raise _protocol()
            stderr.encode("utf-8")
        except (AttributeError, UnicodeError):
            raise _protocol() from None
        lines = [line for line in stdout.split("\n") if line.strip(" \t\r")]
        if not lines:
            raise cli_failure(
                stderr, message="Antigravity native generation omitted its final output", code="protocol_error"
            )
        if len(lines) > MAX_EVENTS:
            raise _protocol()
        session = None
        terminal = None
        failed_steps: set[int] = set()
        for line in lines:
            try:
                event = load_json(line)
            except ParserError:
                raise _protocol() from None
            _validate_json(event)
            if (
                terminal is not None
                or not isinstance(event, dict)
                or not isinstance(event.get("event"), str)
                or event["event"] not in {"init", "step_update", "result"}
                or any(name in event for name in ("type", "status"))
            ):
                raise _protocol()
            kind = event["event"]
            body = event.get(kind)
            if not isinstance(body, dict):
                raise _protocol()
            observed = event.get("conversation_id") if kind == "init" else body.get("conversation_id")
            if not isinstance(observed, str) or not _SESSION.fullmatch(observed):
                raise _protocol()
            if kind == "init":
                if session is not None:
                    raise _protocol()
                session = observed
            else:
                if session is None or session != observed:
                    raise _protocol()
                if kind == "step_update":
                    _step(body, failed_steps)
                else:
                    terminal = body
        if terminal is None:
            raise cli_failure(
                stderr, message="Antigravity native generation omitted its terminal result", code="protocol_error"
            )
        denials = terminal.get("denied_actions", [])
        if not isinstance(denials, list) or len(denials) > MAX_EVENTS:
            raise _protocol()
        for denial in denials:
            if (
                not isinstance(denial, dict)
                or not isinstance(denial.get("display_name"), str)
                or ("action" in denial and not isinstance(denial["action"], str))
            ):
                raise _protocol()
        if denials:
            raise ParserError("Antigravity native generation requires permission", code="permission_needed")
        error = terminal.get("error", "")
        finished = terminal.get("finished_reason", "")
        if not isinstance(error, str) or not isinstance(finished, str):
            raise _protocol()
        if terminal.get("status") != "SUCCESS" or error or finished:
            raise cli_failure(
                terminal, stderr, message="Antigravity native generation did not complete", code="protocol_error"
            )
        text = terminal.get("response")
        if not isinstance(text, str) or ("usage" in terminal and not isinstance(terminal["usage"], dict)):
            raise _protocol()
        if not text.strip():
            raise ParserError("Antigravity native generation final response was empty", code="empty_response")
        if len(text.encode("utf-8")) > OUTPUT_BYTES:
            raise ParserError(
                "Antigravity native generation final response exceeded its byte limit", code="output_limit"
            )
        return ParsedCLIResponse(
            content=text,
            metadata={"actual_model": None, "tool_error_count": len(failed_steps), "permission_denial_count": 0},
        )
