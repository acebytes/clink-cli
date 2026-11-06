"""Validate native generation telemetry without publishing operational output."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .base import MAX_METADATA_NUMBER, BaseParser, ParsedCLIResponse, ParserError, cli_failure, load_json

MAX_EVENTS = 16384
_MAX_SOURCE_BYTES = 1024 * 1024
_USAGE_REQUIRED = frozenset({"input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"})
_USAGE_OPTIONAL = frozenset({"cache_write_input_tokens"})
_ITEM_EVENTS = frozenset({"item.started", "item.updated", "item.completed"})
_TOOL_ITEMS = frozenset({"command_execution", "file_change", "mcp_tool_call", "collab_tool_call"})
_STATUSES = frozenset({"in_progress", "completed", "failed"})
_SESSION = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_EXEC_POLICY_DENIAL = re.compile(
    r"`(?P<command>[^`\r\n]+)` rejected: policy forbids commands starting with `(?P<prefix>[^`\r\n]+)`\Z"
)
_COMMAND_APPROVAL_DENIALS = frozenset(
    {
        "approval required by policy, but AskForApproval is set to Never",
        "approval required by policy, but AskForApproval::Granular.sandbox_approval is false",
        "approval required by policy rule, but AskForApproval::Granular.rules is false",
    }
)
_MCP_APPROVAL_DENIALS = frozenset(
    {
        "MCP tool call requires approval, but approval policy is never",
        "MCP tool call cancelled: interactive approval is unavailable in exec mode",
    }
)

# This independent contract binds only the new profile. The service validates
# the private final-message file separately; streamed text is never an answer.
PARSER_CONTRACT = (
    "codex-native-generation-jsonl/v1\n"
    "source=codex-6ff1e1ca3b30fc8b914720681fcf5fe62e95f856;max-events=16384;jsonl-newline=LF\n"
    "strict-json:no-duplicate-keys-except-pinned-direct-web-search-ids,no-nonfinite-numbers,utf8-text\n"
    "thread.started:{type,thread_id:uuid};turn.started:{type};one-thread,one-turn,in-order\n"
    "item.started|updated|completed:{type,item};item:{id:nonblank-text,type:known,typed-payload}\n"
    "known-items=agent_message,reasoning,command_execution,file_change,mcp_tool_call,collab_tool_call,"
    "web_search,todo_list,error;unknown-fields-rejected;opaque-JsonValue-subtrees-preserved-only-during-validation\n"
    "lifecycle:no-reused-completed-id,no-type-change,no-duplicate-start,no-incomplete-observed-items;"
    "completed-without-start-allowed;pre-turn-completed-error-warnings-allowed\n"
    "final:one-last-turn.completed:{type,usage};no-stream-message-required;content-always-empty;"
    "private-final-file-validation-owned-by-service;no-stream-finalfile-equality\n"
    "usage:required=input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens;"
    "optional=cache_write_input_tokens;nonnegative-safe-json-integers\n"
    "recoverable-completed-tool-failures-and-item-errors-counted;"
    "exact-source-supported-command-and-MCP-refusals=permission_needed\n"
    "error:{type,message:utf8};turn.failed:{type,error:{message:utf8}};"
    "failed,unknown,malformed,incomplete=protocol_error;known-auth-and-rate-diagnostics-safe-codes\n"
    "metadata:usage,actual_model=null,tool_error_count,permission_denial_count;no-identities-or-bodies\n"
    "identity:sha256(canonical-json(contract,source_sha256(codex_native_generation.py,base.py)));"
    "source-limit=1048576-bytes-each\n"
)


def _protocol_error(message: str = "Codex native generation output violates its protocol") -> ParserError:
    return ParserError(message, code="protocol_error")


def parser_contract_digest() -> str:
    """Bind the native generation contract and its parser dependencies."""

    sources = {}
    try:
        for name in ("codex_native_generation.py", "base.py"):
            with Path(__file__).with_name(name).open("rb") as source:
                content = source.read(_MAX_SOURCE_BYTES + 1)
            if len(content) > _MAX_SOURCE_BYTES:
                raise _protocol_error("Codex native generation parser source exceeds its identity limit")
            sources[name] = hashlib.sha256(content).hexdigest()
    except OSError:
        raise _protocol_error("Codex native generation parser source identity is unavailable") from None
    identity = json.dumps(
        {"contract": PARSER_CONTRACT, "source_sha256": sources},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _object(
    value: object, required: set[str] | frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - (required | optional):
        raise _protocol_error()
    return value


def _text(value: object, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise _protocol_error()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _protocol_error() from None
    return value


def _json_value(value: object) -> None:
    """Validate wire JSON without interpreting tool-owned nested objects."""

    pending = [value]
    while pending:
        part = pending.pop()
        if isinstance(part, dict):
            for key in part:
                _text(key)
            pending.extend(part.values())
        elif isinstance(part, list):
            pending.extend(part)
        elif isinstance(part, str):
            _text(part)
        elif isinstance(part, float) and not math.isfinite(part):
            raise _protocol_error()


def _error(value: object) -> dict[str, Any]:
    error = _object(value, {"message"})
    _text(error["message"])
    return error


def _status(value: object, *, command: bool = False) -> None:
    if not isinstance(value, str) or value not in (_STATUSES | {"declined"} if command else _STATUSES):
        raise _protocol_error()


def _item(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _protocol_error()
    _text(value.get("id"), nonempty=True)
    item_type = _text(value.get("type"))
    common = {"id", "type"}
    if item_type in {"agent_message", "reasoning"}:
        _object(value, common | {"text"})
        _text(value["text"])
    elif item_type == "error":
        _object(value, common | {"message"})
        _text(value["message"])
    elif item_type == "command_execution":
        _object(value, common | {"command", "aggregated_output", "exit_code", "status"})
        _text(value["command"])
        _text(value["aggregated_output"])
        exit_code = value["exit_code"]
        if exit_code is not None and (type(exit_code) is not int or not -(2**31) <= exit_code < 2**31):
            raise _protocol_error()
        _status(value["status"], command=True)
    elif item_type == "file_change":
        _object(value, common | {"changes", "status"})
        _status(value["status"])
        if not isinstance(value["changes"], list):
            raise _protocol_error()
        for change in value["changes"]:
            _object(change, {"path", "kind"})
            _text(change["path"])
            if not isinstance(change["kind"], str) or change["kind"] not in {"add", "delete", "update"}:
                raise _protocol_error()
    elif item_type == "mcp_tool_call":
        _object(value, common | {"server", "tool", "arguments", "result", "error", "status"})
        _text(value["server"])
        _text(value["tool"])
        _status(value["status"])
        if value["error"] is not None:
            _error(value["error"])
        if value["result"] is not None:
            result = _object(value["result"], {"content", "structured_content"}, frozenset({"_meta"}))
            if not isinstance(result["content"], list):
                raise _protocol_error()
    elif item_type == "collab_tool_call":
        _object(
            value, common | {"tool", "sender_thread_id", "receiver_thread_ids", "prompt", "agents_states", "status"}
        )
        if not isinstance(value["tool"], str) or value["tool"] not in {
            "spawn_agent",
            "send_input",
            "wait",
            "close_agent",
        }:
            raise _protocol_error()
        _text(value["sender_thread_id"], nonempty=True)
        if value["prompt"] is not None:
            _text(value["prompt"])
        if not isinstance(value["receiver_thread_ids"], list) or not isinstance(value["agents_states"], dict):
            raise _protocol_error()
        for receiver in value["receiver_thread_ids"]:
            _text(receiver, nonempty=True)
        for agent_id, state in value["agents_states"].items():
            _text(agent_id, nonempty=True)
            _object(state, {"status", "message"})
            if not isinstance(state["status"], str) or state["status"] not in {
                "pending_init",
                "running",
                "interrupted",
                "completed",
                "errored",
                "shutdown",
                "not_found",
            }:
                raise _protocol_error()
            if state["message"] is not None:
                _text(state["message"])
        _status(value["status"])
    elif item_type == "web_search":
        _object(value, common | {"query", "action"}, frozenset({"results"}))
        _text(value["query"])
        # The imported WebSearchAction is tool-owned. The pinned mapper exposes
        # its container, while results explicitly contain arbitrary JsonValue.
        if not isinstance(value["action"], dict):
            raise _protocol_error()
        if "results" in value and not isinstance(value["results"], list):
            raise _protocol_error()
    elif item_type == "todo_list":
        _object(value, common | {"items"})
        if not isinstance(value["items"], list):
            raise _protocol_error()
        for step in value["items"]:
            _object(step, {"text", "completed"})
            _text(step["text"])
            if type(step["completed"]) is not bool:
                raise _protocol_error()
    else:
        raise _protocol_error()
    return value


def _permission_denial(item: dict[str, Any]) -> bool:
    """Recognize only pinned refusal signatures, never arbitrary tool prose."""

    if item["type"] == "command_execution" and item["status"] == "declined" and item["exit_code"] == -1:
        output = item["aggregated_output"]
        if output in _COMMAND_APPROVAL_DENIALS:
            return True
        marker = _EXEC_POLICY_DENIAL.fullmatch(output)
        return (
            marker is not None
            and marker["command"] == item["command"]
            and bool(marker["command"].strip())
            and bool(marker["prefix"].strip())
        )
    return (
        item["type"] == "mcp_tool_call"
        and item["status"] == "failed"
        and item["result"] is None
        and item["error"] is not None
        and item["error"]["message"] in _MCP_APPROVAL_DENIALS
    )


class CodexNativeGenerationParser(BaseParser):
    """Validate one native turn; leave final-message publication to its owner."""

    name = "codex_native_generation_jsonl"

    def parse(self, stdout: str, stderr: str = "") -> ParsedCLIResponse:
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise _protocol_error()
        _text(stdout)
        _text(stderr)
        lines = [line for line in stdout.split("\n") if line.strip(" \t\r")]
        if not lines or len(lines) > MAX_EVENTS:
            raise cli_failure(
                stderr, message="Codex native generation output is empty or too large", code="protocol_error"
            )
        thread_started = False
        turn_started = False
        terminal = False
        fatal: dict[str, Any] | None = None
        failed: dict[str, Any] | None = None
        item_types: dict[str, str] = {}
        active_items: set[str] = set()
        completed_items: set[str] = set()
        metadata: dict[str, Any] = {"actual_model": None, "tool_error_count": 0, "permission_denial_count": 0}

        for line in lines:
            try:
                event = load_json(line, allow_codex_web_search_ids=True)
            except ParserError:
                raise _protocol_error() from None
            _json_value(event)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str) or terminal:
                raise _protocol_error()
            event_type = event["type"]
            # Native terminal handling reconciles outstanding tool/todo items
            # before emitting turn.failed. A prior fatal error still prevents
            # success, but must not discard its safe authentication/rate code.
            if fatal is not None and event_type not in {"item.completed", "turn.failed"}:
                raise _protocol_error()

            if event_type == "error":
                _object(event, {"type", "message"})
                _text(event["message"])
                fatal = event
            elif event_type == "turn.failed":
                _object(event, {"type", "error"})
                _error(event["error"])
                failed = event
                terminal = True
            elif event_type == "thread.started":
                _object(event, {"type", "thread_id"})
                session = _text(event["thread_id"])
                if thread_started or turn_started or not _SESSION.fullmatch(session):
                    raise _protocol_error()
                thread_started = True
            elif event_type == "turn.started":
                _object(event, {"type"})
                if not thread_started or turn_started:
                    raise _protocol_error()
                turn_started = True
            elif event_type in _ITEM_EVENTS:
                _object(event, {"type", "item"})
                item = _item(event["item"])
                item_id, item_type = item["id"], item["type"]
                if not thread_started or (
                    not turn_started and not (item_type == "error" and event_type == "item.completed")
                ):
                    raise _protocol_error()
                if item_id in completed_items or (item_id in item_types and item_types[item_id] != item_type):
                    raise _protocol_error()
                if event_type == "item.started" and item_id in item_types:
                    raise _protocol_error()
                item_types[item_id] = item_type
                if event_type == "item.completed":
                    if item_type in _TOOL_ITEMS and item["status"] == "in_progress":
                        raise _protocol_error()
                    active_items.discard(item_id)
                    completed_items.add(item_id)
                    tool_failed = item_type == "error" or (
                        item_type in _TOOL_ITEMS
                        and (
                            item["status"] in {"failed", "declined"}
                            or (item_type == "command_execution" and item["exit_code"] not in (None, 0))
                            or (item_type == "mcp_tool_call" and item["error"] is not None)
                        )
                    )
                    metadata["tool_error_count"] += int(tool_failed)
                    metadata["permission_denial_count"] += int(_permission_denial(item))
                else:
                    active_items.add(item_id)
            elif event_type == "turn.completed":
                _object(event, {"type", "usage"})
                if not turn_started or active_items:
                    raise _protocol_error()
                usage = _object(event["usage"], _USAGE_REQUIRED, _USAGE_OPTIONAL)
                if any(type(count) is not int or not 0 <= count <= MAX_METADATA_NUMBER for count in usage.values()):
                    raise _protocol_error()
                metadata["usage"] = usage.copy()
                terminal = True
            else:
                raise _protocol_error()

        if metadata["permission_denial_count"]:
            raise ParserError("Codex native generation requires permission", code="permission_needed")
        if failed is not None or fatal is not None or not terminal:
            raise cli_failure(
                failed, fatal, stderr, message="Codex native generation did not complete", code="protocol_error"
            )
        return ParsedCLIResponse(content="", metadata=metadata)
