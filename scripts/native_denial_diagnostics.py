"""Project pinned native refusals into finite, verifier-only technical metadata.

Codex source: openai/codex 6ff1e1ca3b30fc8b914720681fcf5fe62e95f856,
exec/src/exec_events.rs:288-298 and the native diagnostic branches mirrored in
clink/parsers/codex_native.py. Agy's denied-action labels are free-form, so they
never identify a tool or operation here. No input-derived fingerprints survive.
"""

from __future__ import annotations

import re
from typing import Any

from clink.parsers.agy_native import AgyNativeParser
from clink.parsers.base import ParserError, load_json
from clink.parsers.codex_native import CodexNativeParser

MAX_EVENTS = 16384
MAX_BYTES = 10 * 1024 * 1024
MAX_DENIALS = 8
_COMMAND_MESSAGES = {
    "approval required by policy, but AskForApproval is set to Never": "command_approval_never",
    "approval required by policy, but AskForApproval::Granular.sandbox_approval is false": "command_sandbox_approval_disabled",
    "approval required by policy rule, but AskForApproval::Granular.rules is false": "command_rules_approval_disabled",
}
_MCP_MESSAGES = {
    "MCP tool call requires approval, but approval policy is never": "mcp_approval_never",
    "MCP tool call cancelled: interactive approval is unavailable in exec mode": "mcp_interactive_approval_unavailable",
}
_EXEC_POLICY_DENIAL = re.compile(
    r"`(?P<command>[^`\r\n]+)` rejected: policy forbids commands starting with `(?P<prefix>[^`\r\n]+)`\Z"
)
_CODEX_CATEGORIES = {
    "exec_policy_forbidden": "command",
    **dict.fromkeys(_COMMAND_MESSAGES.values(), "command"),
    **dict.fromkeys(_MCP_MESSAGES.values(), "mcp"),
}
_FIELDS = {"schema_version", "parser_valid", "permission_denial_count", "tool_error_count", "denials", "truncated"}
_DENIAL_FIELDS = {"tool_category", "reason_code", "fixture_operation", "signature"}
_COMMAND_OPERATIONS = {"unknown", "fixture_test", "fixture_cancel", "permission_canary"}


def _codex_denial(item: dict[str, Any]) -> tuple[str, str] | None:
    if item.get("type") == "command_execution" and item.get("status") == "declined" and item.get("exit_code") == -1:
        message = item.get("aggregated_output")
        if isinstance(message, str) and message in _COMMAND_MESSAGES:
            return "command", _COMMAND_MESSAGES[message]
        marker = _EXEC_POLICY_DENIAL.fullmatch(message) if isinstance(message, str) else None
        if (
            marker is not None
            and marker["command"] == item.get("command")
            and bool(marker["command"].strip())
            and bool(marker["prefix"].strip())
        ):
            return "command", "exec_policy_forbidden"
    if (
        item.get("type") == "mcp_tool_call"
        and item.get("status") == "failed"
        and "result" in item
        and item["result"] is None
    ):
        error = item.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str) and message in _MCP_MESSAGES:
            return "mcp", _MCP_MESSAGES[message]
    return None


def native_denial_diagnostic(
    adapter: str,
    stdout: str,
    *,
    test_command: str | None = None,
    permission_command: str | None = None,
) -> dict[str, Any]:
    """Observe validated structured refusals without changing parser or task state."""
    result: dict[str, Any] = {
        "schema_version": 1,
        "parser_valid": False,
        "permission_denial_count": 0,
        "tool_error_count": 0,
        "denials": [],
        "truncated": False,
    }
    if (
        not isinstance(adapter, str)
        or adapter not in {"agy", "codex"}
        or not isinstance(stdout, str)
        or len(stdout) > MAX_BYTES
    ):
        return result
    if len(stdout.encode("utf-8", errors="replace")) > MAX_BYTES:
        return result
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines or len(lines) > MAX_EVENTS:
        return result
    try:
        parsed = (AgyNativeParser() if adapter == "agy" else CodexNativeParser()).parse(stdout, "")
    except ParserError:
        return result
    counts = [parsed.metadata.get(key, 0) for key in ("permission_denial_count", "tool_error_count")]
    if any(type(count) is not int or not 0 <= count <= MAX_EVENTS for count in counts):
        return result
    denials = []
    if adapter == "agy":
        denials = [
            {
                "tool_category": "unknown",
                "reason_code": "agy_denied_action",
                "fixture_operation": "unknown",
                "signature": "agy_1_4_12_denied_action",
            }
            for _ in range(min(counts[0], MAX_DENIALS))
        ]
    else:
        for line in lines:
            try:
                event = load_json(line, allow_codex_web_search_ids=True)
            except ParserError:
                return result
            if not isinstance(event, dict) or event.get("type") != "item.completed":
                continue
            item = event.get("item")
            matched = _codex_denial(item) if isinstance(item, dict) else None
            if matched is None:
                continue
            category, reason = matched
            operation = "unknown"
            if category == "command":
                command = item["command"]
                if permission_command is not None and command == permission_command:
                    operation = "permission_canary"
                elif test_command is not None and command == test_command:
                    operation = "fixture_test"
                elif test_command is not None and command == test_command + " cancel":
                    operation = "fixture_cancel"
            elif (
                item.get("server") == "native_fixture"
                and item.get("tool") == "canary"
                and "arguments" in item
                and isinstance(item["arguments"], dict)
                and item["arguments"] == {}
            ):
                operation = "mcp_canary"
            denials.append(
                {
                    "tool_category": category,
                    "reason_code": reason,
                    "fixture_operation": operation,
                    "signature": "codex_0_166_0_" + reason,
                }
            )
            if len(denials) == MAX_DENIALS:
                break
    result.update(
        parser_valid=True,
        permission_denial_count=counts[0],
        tool_error_count=counts[1],
        denials=denials,
        truncated=counts[0] > len(denials),
    )
    return result


def valid_native_denial_diagnostic(value: Any, adapter: str) -> bool:
    """Reject arbitrary keys, tokens, types and cross-adapter receipt categories."""
    if (
        not isinstance(adapter, str)
        or adapter not in {"agy", "codex"}
        or not isinstance(value, dict)
        or set(value) != _FIELDS
    ):
        return False
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        return False
    if type(value["parser_valid"]) is not bool or type(value["truncated"]) is not bool:
        return False
    if any(
        type(value[key]) is not int or not 0 <= value[key] <= MAX_EVENTS
        for key in ("permission_denial_count", "tool_error_count")
    ):
        return False
    denials = value["denials"]
    if not isinstance(denials, list) or len(denials) > min(MAX_DENIALS, value["permission_denial_count"]):
        return False
    if value["truncated"] is not (value["permission_denial_count"] > len(denials)):
        return False
    if not value["parser_valid"] and (value["permission_denial_count"] or value["tool_error_count"] or denials):
        return False
    for denial in denials:
        if (
            not isinstance(denial, dict)
            or set(denial) != _DENIAL_FIELDS
            or any(not isinstance(part, str) for part in denial.values())
        ):
            return False
        reason, category, operation = denial["reason_code"], denial["tool_category"], denial["fixture_operation"]
        if adapter == "agy":
            if denial != {
                "tool_category": "unknown",
                "reason_code": "agy_denied_action",
                "fixture_operation": "unknown",
                "signature": "agy_1_4_12_denied_action",
            }:
                return False
        elif (
            reason not in _CODEX_CATEGORIES
            or category != _CODEX_CATEGORIES[reason]
            or denial["signature"] != "codex_0_166_0_" + reason
            or operation not in (_COMMAND_OPERATIONS if category == "command" else {"unknown", "mcp_canary"})
        ):
            return False
    return True
