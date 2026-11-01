"""Build bounded Codex 0.166.0 canaries without changing native permissions.

Only the verifier's marked synthetic project is written. Project trust and the
current hashes of passive hooks remain native prerequisites: creating these
files neither grants trust nor proves that a capability ran. Hooks retain only
fixed metadata, booleans and one-way child fingerprints in the ignored fixture
log. Raw identities and private JSONL are never saved; exports remain booleans.

Contracts are pinned to openai/codex commit
6ff1e1ca3b30fc8b914720681fcf5fe62e95f856: exec/src/exec_events.rs,
core/src/exec_policy.rs, hooks/schema/generated/, and config/src/config_toml.rs.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from typing import Any

from clink.parsers.base import ParserError, load_json
from clink.parsers.codex_native import CodexNativeParser
from scripts.native_mcp_fixture import mcp_server_program
from scripts.verify_native_cli import SENTINEL_NAME

SOURCE_COMMIT = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
SPECIALIST_ROLE = "fixture-specialist"
SPECIALIST_TOKEN = "CODEX_SPECIALIST_CANARY_452719"
PERMISSION_COMMAND = "/usr/bin/printf CLINK_NATIVE_POLICY_CANARY"
PERMISSION_PROMPT = (
    f"Use the native shell tool to run exactly `{PERMISSION_COMMAND}` once. "
    "This harmless synthetic command is forbidden by the fixture's native execpolicy rule. "
    "Do not infer the result without attempting the command. Stop immediately on denial; "
    "do not retry, change configuration, or use alternatives."
)
CAPABILITY_PROMPT = (
    "Call the native_fixture MCP canary. Use the configured fixture-specialist agent type "
    "to read its configured canary and return its exact token; wait for that child to finish. "
    "Do not override the child's model, reasoning effort, or permissions. "
)
MAX_OBSERVATION_BYTES = 65536
MAX_STREAM_BYTES = 10 * 1024 * 1024
_STREAM_FILE = "codex-stream-observed.json"
MCP_TOOL_ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}

HOOK_PROGRAM = '''"""Observe passive native lifecycle hooks; never approve or deny an operation."""
import fcntl
import hashlib
import json
import os
import pathlib
import stat
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SPECIALIST = @SPECIALIST@
TOKEN = @TOKEN@
PERMISSION_COMMAND = @PERMISSION_COMMAND@
MAXIMUM = 65536
raw = sys.stdin.read(1024 * 1024 + 1)
try:
    event = json.loads(raw) if len(raw) <= 1024 * 1024 else None
except (ValueError, UnicodeError):
    event = None
names = {"SessionStart": "session_start", "PreToolUse": "pre_tool", "PostToolUse": "post_tool",
         "SubagentStart": "subagent_start", "SubagentStop": "subagent_stop"}
valid = (isinstance(event, dict) and event.get("cwd") == str(ROOT)
    and isinstance(event.get("hook_event_name"), str) and event["hook_event_name"] in names)
if valid and event["hook_event_name"] == "SessionStart":
    valid = event.get("source") in {"startup", "resume", "clear", "compact", "fork"}
if valid and event["hook_event_name"] in {"SubagentStart", "SubagentStop"}:
    valid = (isinstance(event.get("agent_id"), str) and bool(event["agent_id"])
        and isinstance(event.get("agent_type"), str) and bool(event["agent_type"]))
    if valid and event["hook_event_name"] == "SubagentStop":
        valid = "last_assistant_message" in event and (event["last_assistant_message"] is None
            or isinstance(event["last_assistant_message"], str))
kind = names[event["hook_event_name"]] if valid else "unknown"
record = {"event": kind, "payload_valid": valid}
if valid:
    child = event.get("agent_type") == SPECIALIST and isinstance(event.get("agent_id"), str) and bool(event["agent_id"])
    message = event.get("last_assistant_message")
    record["fixture_subagent"] = child
    if child and kind in {"subagent_start", "subagent_stop"}:
        record["child_fingerprint"] = hashlib.sha256(event["agent_id"].encode("utf-8")).hexdigest()
    record["canary_matches"] = kind == "subagent_stop" and child and isinstance(message, str) and message.strip() == TOKEN
    data = event.get("tool_input")
    record["permission_command_matches"] = (kind == "pre_tool" and event.get("tool_name") == "Bash"
        and isinstance(data, dict) and data.get("command") == PERMISSION_COMMAND)
path = ROOT / "codex-hooks.jsonl"
encoded = (json.dumps(record, sort_keys=True) + "\\n").encode()
fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
with os.fdopen(fd, "ab") as output:
    fcntl.flock(output.fileno(), fcntl.LOCK_EX)
    info = os.fstat(output.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise RuntimeError("Synthetic hook observation file is unsafe")
    if info.st_size + len(encoded) <= MAXIMUM:
        output.write(encoded)
print("{}")
'''


def _fixture_root(root: Path) -> None:
    if not root.is_absolute() or root != root.resolve() or not root.is_dir():
        raise ValueError("Codex fixture requires an existing canonical synthetic project")
    marker = root / ".clink-native-verification-fixture"
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("Codex fixture requires the native verifier's synthetic-project marker")


def create_codex_fixture(root: Path) -> list[str]:
    """Add documented project configuration; leave HOME, trust and permissions alone."""
    _fixture_root(root)
    hook_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(root / 'codex_hook.py'))}"
    hooks = {
        "hooks": {
            name: [{"matcher": matcher, "hooks": [{"type": "command", "command": hook_command, "timeout": 5}]}]
            for name, matcher in (
                ("SessionStart", "startup|resume"),
                ("PreToolUse", ""),
                ("PostToolUse", ""),
                ("SubagentStart", "^fixture-specialist$"),
                ("SubagentStop", "^fixture-specialist$"),
            )
        }
    }
    config = (
        "[mcp_servers.native_fixture]\n"
        f"command = {json.dumps(sys.executable)}\n"
        f"args = {json.dumps([str(root / 'codex_mcp_server.py')])}\n"
        f"env_vars = {json.dumps([SENTINEL_NAME])}\n"
        "enabled = true\nrequired = true\nstartup_timeout_sec = 10\ntool_timeout_sec = 10\n\n"
        "[agents.fixture-specialist]\n"
        'description = "Read the synthetic native lifecycle specialist canary when explicitly delegated."\n'
        'config_file = "agents/fixture-specialist.toml"\n'
    )
    specialist = (
        f"Read only {root / 'codex-specialist.txt'} and return exactly its token with no other text. "
        "Do not edit files, run unrelated commands, use unrelated integrations, delegate, "
        "or change your inherited model, reasoning effort, configuration or permissions."
    )
    files = {
        "codex_hook.py": HOOK_PROGRAM.replace("@SPECIALIST@", repr(SPECIALIST_ROLE))
        .replace("@TOKEN@", repr(SPECIALIST_TOKEN))
        .replace("@PERMISSION_COMMAND@", repr(PERMISSION_COMMAND)),
        "codex_mcp_server.py": mcp_server_program(tool_annotations=MCP_TOOL_ANNOTATIONS, create_only_observation=True),
        "codex-specialist.txt": SPECIALIST_TOKEN + "\n",
        ".codex/config.toml": config,
        ".codex/hooks.json": json.dumps(hooks, indent=2) + "\n",
        ".codex/agents/fixture-specialist.toml": "developer_instructions = " + json.dumps(specialist) + "\n",
        # Omit justification so the native diagnostic retains its pinned policy format.
        ".codex/rules/native-fixture.rules": (
            'prefix_rule(pattern = ["/usr/bin/printf", "CLINK_NATIVE_POLICY_CANARY"], decision = "forbidden")\n'
        ),
    }
    for name in files:
        path = root / name
        if path.exists() or path.is_symlink():
            raise ValueError("Codex fixture refuses existing generated files")
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError("Codex fixture refuses unsafe configuration directories")
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    return sorted(files)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError:
        return []
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            return []
        raw = stream.read(MAX_OBSERVATION_BYTES + 1)
    if len(raw) > MAX_OBSERVATION_BYTES:
        return []
    rows = []
    for line in raw.splitlines():
        try:
            value = load_json(line)
        except ParserError:
            return []
        if not isinstance(value, dict):
            return []
        rows.append(value)
    return rows


def observe_codex_stream(root: Path, stdout: str) -> None:
    """Project private, valid JSONL into the exact harmless rule-refusal boolean."""
    _fixture_root(root)
    valid = False
    denied = False
    try:
        bounded = isinstance(stdout, str) and len(stdout.encode("utf-8")) <= MAX_STREAM_BYTES
    except UnicodeError:
        bounded = False
    if bounded:
        try:
            CodexNativeParser().parse(stdout)
            events = [load_json(line, allow_codex_web_search_ids=True) for line in stdout.splitlines() if line.strip()]
        except (ParserError, UnicodeError):
            events = []
        else:
            valid = True
        for event in events:
            if event.get("type") != "item.completed":
                continue
            item = event["item"]
            command = item.get("command")
            if (
                item["type"] == "command_execution"
                and item["status"] == "declined"
                and item["exit_code"] == -1
                and isinstance(command, str)
                and command
                and not any(character in command for character in "`\r\n")
                and item["aggregated_output"]
                == f"`{command}` rejected: policy forbids commands starting with `{PERMISSION_COMMAND}`"
            ):
                denied = True
    encoded = json.dumps({"stream_valid": valid, "permission_policy_declined_observed": denied}).encode() + b"\n"
    path = root / _STREAM_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "wb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ValueError("Codex fixture observation must be an owner-owned regular file")
        stream.truncate(0)
        stream.write(encoded)


def codex_fixture_observations(root: Path) -> dict[str, bool]:
    """Report passive capability evidence without inferring hook trust or fulfillment."""
    _fixture_root(root)
    events = [row for row in _read_rows(root / "codex-hooks.jsonl") if row.get("payload_valid") is True]
    stream = _read_rows(root / _STREAM_FILE)
    mcp = _read_rows(root / "mcp-called.json")
    started: set[str] = set()
    stopped: set[str] = set()
    canary_matches: set[str] = set()
    for row in events:
        child = row.get("child_fingerprint")
        if (
            row.get("fixture_subagent") is not True
            or not isinstance(child, str)
            or not re.fullmatch(r"[0-9a-f]{64}", child)
        ):
            continue
        if row.get("event") == "subagent_start":
            started.add(child)
        elif row.get("event") == "subagent_stop" and child in started:
            stopped.add(child)
            if row.get("canary_matches") is True:
                canary_matches.add(child)
    return {
        "scope_hook_observed": bool(events),
        "codex_session_start_observed": any(row.get("event") == "session_start" for row in events),
        "fixture_subagent_started": bool(started),
        "fixture_subagent_stopped": bool(stopped),
        "fixture_subagent_canary_matches": bool(canary_matches),
        "permission_hook_observed": any(row.get("permission_command_matches") is True for row in events),
        "permission_policy_declined_observed": len(stream) == 1
        and stream[0].get("stream_valid") is True
        and stream[0].get("permission_policy_declined_observed") is True,
        "mcp_environment_canary_matches": len(mcp) == 1 and mcp[0].get("inherited_sentinel_matches") is True,
    }


def codex_fixture_gaps(root: Path) -> list[str]:
    """Keep native trust/configuration unverified until a project canary actually ran."""
    observed = codex_fixture_observations(root)
    gaps = []
    if not any(
        observed[name]
        for name in ("scope_hook_observed", "mcp_environment_canary_matches", "permission_policy_declined_observed")
    ):
        gaps.append("codex_project_trust_unverified")
    if not observed["scope_hook_observed"]:
        gaps.append("codex_project_hook_trust_unverified")
    if not observed["mcp_environment_canary_matches"]:
        gaps.append("codex_mcp_environment_unverified")
    if not all(
        observed[name]
        for name in ("fixture_subagent_started", "fixture_subagent_stopped", "fixture_subagent_canary_matches")
    ):
        gaps.append("codex_named_subagent_unverified")
    if not observed["permission_policy_declined_observed"]:
        gaps.append("codex_permission_policy_unverified")
    return gaps
