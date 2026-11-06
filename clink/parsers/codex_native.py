"""Parse native Codex JSONL without retaining tool payloads or public sessions."""

from __future__ import annotations

import re
from typing import Any

from clink.native_contracts import ParsedNativeResponse
from clink.parsers.base import ParserError, bounded_counts, cli_failure, load_json

RESULT_CHARACTERS = 20000
MAX_EVENTS = 16384
_SESSION = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "item.started",
    "item.updated",
    "item.completed",
    "turn.completed",
    "turn.failed",
    "error",
}
_ITEM_TYPES = {
    "agent_message",
    "reasoning",
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "web_search",
    "todo_list",
    "error",
}
_TOOL_TYPES = {"command_execution", "file_change", "mcp_tool_call", "collab_tool_call"}
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "total_tokens",
    "reasoning_output_tokens",
)
_EXEC_POLICY_DENIAL = re.compile(
    r"`(?P<command>[^`\r\n]+)` rejected: policy forbids commands starting with `(?P<prefix>[^`\r\n]+)`\Z"
)
_COMMAND_APPROVAL_DENIALS = {
    "approval required by policy, but AskForApproval is set to Never",
    "approval required by policy, but AskForApproval::Granular.sandbox_approval is false",
    "approval required by policy rule, but AskForApproval::Granular.rules is false",
}
_MCP_APPROVAL_DENIALS = {
    "MCP tool call requires approval, but approval policy is never",
    "MCP tool call cancelled: interactive approval is unavailable in exec mode",
}


def validate_session_id(value: str | None) -> str | None:
    """Accept only canonical UUID-shaped private Codex thread identities."""

    if value is not None and (not isinstance(value, str) or not _SESSION.fullmatch(value)):
        raise ParserError("Codex native session identity is invalid")
    return value


def _validate_error(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("message"), str):
        raise ParserError("Codex native error must contain a textual message")


def _validate_item(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str) or value["type"] not in _ITEM_TYPES:
        raise ParserError("Codex native item type is unsupported")
    if not isinstance(value.get("id"), str) or not value["id"]:
        raise ParserError("Codex native item identity is invalid")
    item_type = value["type"]
    if item_type in {"agent_message", "reasoning"} and not isinstance(value.get("text"), str):
        raise ParserError("Codex native message must be textual")
    if item_type == "error" and not isinstance(value.get("message"), str):
        raise ParserError("Codex native item error must be textual")
    if item_type in _TOOL_TYPES:
        statuses = {"in_progress", "completed", "failed"}
        if item_type == "command_execution":
            # Pinned native status includes declined for both policy rejections
            # and runtime setup failures, so it does not alone prove a denial.
            statuses.add("declined")
        if not isinstance(value.get("status"), str) or value["status"] not in statuses:
            raise ParserError("Codex native tool status is invalid")
    if item_type == "command_execution":
        if not isinstance(value.get("command"), str) or not isinstance(value.get("aggregated_output"), str):
            raise ParserError("Codex native command fields must be textual")
        if "exit_code" not in value:
            raise ParserError("Codex native command exit code is missing")
        exit_code = value["exit_code"]
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise ParserError("Codex native command exit code must be an integer")
    if item_type == "mcp_tool_call":
        if not isinstance(value.get("server"), str) or not isinstance(value.get("tool"), str):
            raise ParserError("Codex native MCP tool identity must be textual")
        if value.get("error") is not None:
            _validate_error(value["error"])
        if value.get("result") is not None and not isinstance(value["result"], dict):
            raise ParserError("Codex native MCP result must be an object")
    return value


def _permission_denial(item: dict[str, Any]) -> bool:
    """Recognize a conservative subset of pinned 0.166.0 native diagnostics.

    The protocol loses typed rejection provenance; generic declined items can
    also represent setup failures. These exact native strings are a versioned
    dependency, not a general interpretation of assistant or command prose.
    """

    if item["type"] == "command_execution" and item["status"] == "declined" and item["exit_code"] == -1:
        if item["aggregated_output"] in _COMMAND_APPROVAL_DENIALS:
            return True
        marker = _EXEC_POLICY_DENIAL.fullmatch(item["aggregated_output"])
        return (
            marker is not None
            and marker["command"] == item["command"]
            and bool(marker["command"].strip())
            and bool(marker["prefix"].strip())
        )
    if item["type"] == "mcp_tool_call" and item["status"] == "failed":
        error = item.get("error")
        return (
            "result" in item
            and item["result"] is None
            and isinstance(error, dict)
            and error["message"] in _MCP_APPROVAL_DENIALS
        )
    return False


class CodexNativeParser:
    """Use the terminal turn outcome while preserving recoverable tool failures."""

    def parse(self, stdout: str, stderr: str = "") -> ParsedNativeResponse:
        if not isinstance(stdout, str):
            raise ParserError("Codex native output must be textual")
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines or len(lines) > MAX_EVENTS:
            raise ParserError("Codex native output was empty or exceeded the event limit")
        session_id = None
        terminal: dict[str, Any] | None = None
        fatal: dict[str, Any] | None = None
        item_types: dict[str, str] = {}
        completed_items: set[str] = set()
        tool_errors = 0
        permission_denials = 0
        content = ""
        message_count = 0

        for line in lines:
            event = load_json(line, allow_codex_web_search_ids=True)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ParserError("Codex native events must be typed JSON objects")
            event_type = event["type"]
            if event_type not in _EVENT_TYPES:
                raise ParserError("Codex native event type is unsupported")
            # The installed CLI emits `error` followed by `turn.failed` for one
            # fatal outcome. No other post-terminal event or duplicate is valid.
            if terminal is not None or (fatal is not None and event_type != "turn.failed"):
                raise ParserError("Codex native output continued after its terminal outcome")
            if "thread_id" in event:
                observed_session = event["thread_id"]
                validate_session_id(observed_session)
                if observed_session is None:
                    raise ParserError("Codex native thread identity is missing")
                if session_id is not None and session_id != observed_session:
                    raise ParserError("Codex native output changed its session identity")
                session_id = observed_session
            elif event_type == "thread.started":
                raise ParserError("Codex native thread identity is missing")

            if event_type.startswith("item."):
                item = _validate_item(event.get("item"))
                item_id, item_type = item["id"], item["type"]
                if item_id in completed_items or (item_id in item_types and item_types[item_id] != item_type):
                    raise ParserError("Codex native item lifecycle is contradictory")
                item_types[item_id] = item_type
                if event_type == "item.completed":
                    completed_items.add(item_id)
                    if item_type in _TOOL_TYPES and item["status"] == "in_progress":
                        raise ParserError("Codex native completed tool is still in progress")
                    failed = item_type == "error" or (
                        item_type in _TOOL_TYPES
                        and (
                            item["status"] in {"failed", "declined"}
                            or (item_type == "command_execution" and item.get("exit_code") not in (None, 0))
                            or (item_type == "mcp_tool_call" and item.get("error") is not None)
                        )
                    )
                    tool_errors += int(failed)
                    permission_denials += int(_permission_denial(item))
                    if item_type == "agent_message":
                        separator = "\n\n" if message_count else ""
                        remaining = RESULT_CHARACTERS + 1 - len(content)
                        if remaining > 0:
                            content += (separator + item["text"][:remaining])[:remaining]
                        message_count += 1
            elif event_type == "error":
                _validate_error(event)
                fatal = event
            elif event_type == "turn.failed":
                _validate_error(event.get("error"))
                terminal = event
            elif event_type == "turn.completed":
                if (
                    event.get("status", "completed") not in ("completed", "success")
                    or event.get("error") is not None
                    or event.get("errors")
                    or event.get("is_error", False) is not False
                ):
                    raise ParserError("Codex native successful turn contains contradictory failure markers")
                if not isinstance(event.get("usage"), dict):
                    raise ParserError("Codex native usage must be an object")
                terminal = event

        if terminal is None and fatal is None:
            raise cli_failure(stderr, message="Codex native output had no terminal outcome")
        success = terminal is not None and terminal["type"] == "turn.completed"
        if success and session_id is None:
            raise ParserError("Codex native successful turn omitted its session identity")
        state = "permission_needed" if permission_denials else "completed" if success else "failed"
        metadata: dict[str, Any] = {
            "tool_error_count": tool_errors,
            "permission_denial_count": permission_denials,
            "truncated": len(content) > RESULT_CHARACTERS,
        }
        if success and (usage := bounded_counts(terminal.get("usage"), _USAGE_FIELDS)):
            metadata["usage"] = usage
        if state == "failed":
            metadata["error_code"] = cli_failure(terminal, fatal, stderr, code="native_error").code
        return ParsedNativeResponse(state, content[:RESULT_CHARACTERS], session_id, metadata)
