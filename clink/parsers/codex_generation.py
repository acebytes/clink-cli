"""Strict, text-only Codex JSONL parsing for the separate generation profile."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .base import (
    MAX_METADATA_NUMBER,
    BaseParser,
    ParsedCLIResponse,
    ParserError,
    cli_failure,
    load_json,
    model_identifier,
)

MAX_EVENTS = 16384
_MAX_SOURCE_BYTES = 1024 * 1024
_USAGE_REQUIRED = frozenset({"input_tokens", "cached_input_tokens", "output_tokens"})
_USAGE_OPTIONAL = frozenset({"cache_write_input_tokens", "reasoning_output_tokens"})
_ITEM_EVENTS = frozenset({"item.started", "item.updated", "item.completed"})
_TOOL_ITEMS = frozenset(
    {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
        "todo_list",
        "image_generation",
    }
)

# Admission binds this explicit acceptance contract independently of advisory and
# native parsers. Change it whenever generation acceptance semantics change.
PARSER_CONTRACT = (
    "codex-generation-jsonl/v1\n"
    "strict-json:no-duplicate-keys,no-nonfinite-numbers;jsonl-newline=LF;max-events=16384\n"
    "thread.started:{type,thread_id:nonempty-utf8-text};turn.started:{type};one-thread,one-turn,in-order\n"
    "item.started|item.updated|item.completed:{type,item};item:{type:reasoning|agent_message,text:utf8,id?:nonempty-utf8}\n"
    "text-item-lifecycle:no-reused-completed-id,no-type-change,no-duplicate-start,no-incomplete-items\n"
    "final:exactly-one-completed-nonblank-agent_message;preserve-text-exactly;no-item-after-final\n"
    "turn.completed:{type,usage,model?:null|bounded-native-id};one-terminal,last-event\n"
    "usage:required=input_tokens,cached_input_tokens,output_tokens;"
    "optional=cache_write_input_tokens,reasoning_output_tokens;nonnegative-safe-json-integers\n"
    "error:{type,message:utf8};turn.failed:{type,error:{message:utf8}};item-error:{type,message:utf8,id?}\n"
    "unknown-keys,unknown-events,unknown-items,malformed,failed,incomplete=protocol_error;"
    "known-tool-items=command_execution,file_change,mcp_tool_call,collab_tool_call,web_search,todo_list,image_generation:"
    "unexpected_tool_event;known-failure-diagnostics=authentication_required|rate_limited\n"
    "metadata:usage,actual_model=null-when-unreported;no-thread-or-item-identities\n"
    "identity:sha256(canonical-json(contract,source_sha256(codex_generation.py,base.py)));source-limit=1048576-bytes-each\n"
)


def parser_contract_digest() -> str:
    """Bind the acceptance contract and both parser implementations for admission."""

    sources = {}
    try:
        for name in ("codex_generation.py", "base.py"):
            with Path(__file__).with_name(name).open("rb") as source:
                content = source.read(_MAX_SOURCE_BYTES + 1)
            if len(content) > _MAX_SOURCE_BYTES:
                raise _protocol_error("Codex generation parser source exceeds its identity limit")
            sources[name] = hashlib.sha256(content).hexdigest()
    except OSError:
        raise _protocol_error("Codex generation parser source identity is unavailable") from None
    identity = json.dumps(
        {"contract": PARSER_CONTRACT, "source_sha256": sources},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _protocol_error(message: str) -> ParserError:
    return ParserError(message, code="protocol_error")


def _object(value: object, required: frozenset[str], optional: frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - (required | optional):
        raise _protocol_error("Codex generation record has an unsupported shape")
    return value


def _text(value: object, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise _protocol_error("Codex generation text field is invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _protocol_error("Codex generation text is not valid UTF-8") from None
    return value


def _failure(value: dict[str, Any], stderr: str) -> ParserError:
    return cli_failure(value, stderr, message="Codex generation reported a failure", code="protocol_error")


class CodexGenerationParser(BaseParser):
    """Require a complete, unambiguous text-only generation turn."""

    name = "codex_generation_jsonl"

    def parse(self, stdout: str, stderr: str = "") -> ParsedCLIResponse:
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise _protocol_error("Codex generation streams must be textual")
        # splitlines() also splits valid literal Unicode separators inside JSON
        # strings. Only LF delimits JSONL; CRLF remains valid JSON whitespace.
        lines = [line for line in stdout.split("\n") if line.strip(" \t\r")]
        if not lines or len(lines) > MAX_EVENTS:
            raise cli_failure(stderr, message="Codex generation output is empty or too large", code="protocol_error")

        thread_started = False
        turn_started = False
        terminal = False
        final_text: str | None = None
        item_types: dict[str | None, str] = {}
        active_items: set[str | None] = set()
        completed_items: set[str] = set()
        metadata: dict[str, Any] = {"actual_model": None}

        for line in lines:
            try:
                event = load_json(line)
            except ParserError:
                raise _protocol_error("Codex generation returned malformed JSON") from None
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise _protocol_error("Codex generation events must be typed objects")
            if terminal:
                raise _protocol_error("Codex generation continued after its terminal result")
            event_type = event["type"]

            if event_type == "error":
                _object(event, frozenset({"type", "message"}))
                _text(event["message"])
                raise _failure(event, stderr)
            if event_type == "turn.failed":
                _object(event, frozenset({"type", "error"}))
                error = _object(event["error"], frozenset({"message"}))
                _text(error["message"])
                raise _failure(event, stderr)

            if event_type == "thread.started":
                _object(event, frozenset({"type", "thread_id"}))
                _text(event["thread_id"], nonempty=True)
                if thread_started or turn_started:
                    raise _protocol_error("Codex generation thread lifecycle is contradictory")
                thread_started = True
            elif event_type == "turn.started":
                _object(event, frozenset({"type"}))
                if not thread_started or turn_started:
                    raise _protocol_error("Codex generation turn lifecycle is contradictory")
                turn_started = True
            elif event_type in _ITEM_EVENTS:
                _object(event, frozenset({"type", "item"}))
                item = event["item"]
                if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                    raise _protocol_error("Codex generation items must be typed objects")
                item_type = item["type"]
                if item_type in _TOOL_ITEMS:
                    raise ParserError("Codex generation emitted an unexpected tool item", code="unexpected_tool_event")
                if item_type == "error":
                    _object(item, frozenset({"type", "message"}), frozenset({"id"}))
                    if "id" in item:
                        _text(item["id"], nonempty=True)
                    _text(item["message"])
                    raise _failure(item, stderr)
                if item_type not in {"agent_message", "reasoning"}:
                    raise _protocol_error("Codex generation item type is unsupported")
                _object(item, frozenset({"type", "text"}), frozenset({"id"}))
                text = _text(item["text"])
                item_id = _text(item["id"], nonempty=True) if "id" in item else None
                if not turn_started or final_text is not None:
                    raise _protocol_error("Codex generation text appeared outside the active turn")
                if item_id in completed_items or (item_id in item_types and item_types[item_id] != item_type):
                    raise _protocol_error("Codex generation item lifecycle is contradictory")
                if event_type == "item.started" and item_id in active_items:
                    raise _protocol_error("Codex generation item was started more than once")
                item_types[item_id] = item_type
                if event_type == "item.completed":
                    active_items.discard(item_id)
                    if item_id is not None:
                        completed_items.add(item_id)
                    else:
                        item_types.pop(None, None)
                    if item_type == "agent_message":
                        final_text = _text(text, nonempty=True)
                else:
                    active_items.add(item_id)
            elif event_type == "turn.completed":
                _object(event, frozenset({"type", "usage"}), frozenset({"model"}))
                if not turn_started or active_items or final_text is None:
                    raise _protocol_error("Codex generation turn is incomplete")
                usage = _object(event["usage"], _USAGE_REQUIRED, _USAGE_OPTIONAL)
                if any(type(count) is not int or not 0 <= count <= MAX_METADATA_NUMBER for count in usage.values()):
                    raise _protocol_error("Codex generation usage is invalid")
                actual_model = event.get("model")
                if actual_model is not None and model_identifier(actual_model) is None:
                    raise _protocol_error("Codex generation actual model is invalid")
                metadata.update(usage=usage.copy(), actual_model=actual_model)
                terminal = True
            else:
                raise _protocol_error("Codex generation event type is unsupported")

        if not terminal or final_text is None:
            raise cli_failure(stderr, message="Codex generation output is incomplete", code="protocol_error")
        return ParsedCLIResponse(content=final_text, metadata=metadata)
