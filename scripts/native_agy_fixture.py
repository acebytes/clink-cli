"""Build disposable Antigravity canaries with narrowly scoped native hooks.

Only synthetic project files are written. Authentication, HOME, CLI flags and
user settings remain with the native CLI. Fixture-only approval hooks match
documented synthetic operations; explicit native user Ask/Deny rules still win.
Hook records contain fixed categories
and booleans. Raw identities and event bodies are never stored. One-way
conversation fingerprints stay only in the ignored fixture log; exported
observations contain correlation booleans.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import sys
from pathlib import Path
from typing import Any

from scripts.native_mcp_fixture import MCP_SERVER

SPECIALIST_TOKEN = "AGY_SPECIALIST_CANARY_586293"
MAX_OBSERVATION_BYTES = 65536
FIXTURE_TOOLS = "^(write_file|write_to_file|replace_file_content|multi_replace_file_content|run_command|mcp_native_fixture_canary|invoke_subagent)$"
APPROVAL_KINDS = ("test_command", "cancel_command", "arithmetic_edit", "mcp_canary", "subagent")
# The installed oneharness async command schema omits Cwd; its workspace comes from workspacePaths.
COMMAND_WAIT_MS = 10000
EXPECTED_ARITHMETIC = "def add(a, b):\n    return a + b\n"
CALL_CATEGORIES = (
    "run_command",
    "write_to_file",
    "write_file",
    "replace_file_content",
    "multi_replace_file_content",
    "mcp_native_fixture_canary",
    "invoke_subagent",
    "unknown",
)
DIAGNOSTIC_FLAGS = (
    "call_object",
    "call_name_textual",
    "args_field_present",
    "args_object",
    "approval_kind_supported",
    "expected_keys_present",
    "unexpected_keys_present",
    "neutral_extra_keys_only",
    "command_key_present",
    "command_textual",
    "command_matches",
    "command_argv_matches",
    "cwd_key_present",
    "cwd_textual",
    "cwd_matches",
    "wait_ms_key_present",
    "wait_ms_integer",
    "wait_ms_matches",
    "command_keys_exact",
    "command_summary_bounded",
    "workspace_paths_match",
    "write_target_key_present",
    "write_target_textual",
    "write_path_matches",
    "write_target_safe",
    "write_code_key_present",
    "write_code_textual",
    "write_code_matches",
    "write_overwrite_key_present",
    "write_overwrite_boolean",
    "write_overwrite_matches",
    "write_description_key_present",
    "write_description_textual",
    "write_description_bounded",
    "write_keys_exact",
    "mcp_empty_args",
    "subagent_request_matches",
    "subagents_key_present",
    "subagents_array",
)


def _subagent_request(root: Path) -> dict[str, Any]:
    return {
        "TypeName": "fixture-specialist",
        "Role": "synthetic file reader",
        "Prompt": (
            "Use read_file with exactly "
            + json.dumps({"Path": str(root / "specialist.txt")}, sort_keys=True)
            + ". Return its exact token. "
            "Do not edit files, run commands, or delegate."
        ),
    }


def _classic_subagent_request(root: Path) -> dict[str, Any]:
    return {"Subagents": [_subagent_request(root)]}


def agy_fixture_command(root: Path) -> str:
    """Use one exact absolute command for the Agy prompt and fixture permission hook."""
    if not root.is_absolute() or root != root.resolve() or not Path(sys.executable).is_absolute():
        raise ValueError("Agy fixture commands require canonical absolute paths")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(root / 'fixture.py'))}"


def agy_capability_prompt(root: Path) -> str:
    """Specify the exact synthetic native calls approved by this fixture's hook."""
    return (
        "For the arithmetic fix, use write_file with exactly this JSON argument object: "
        + json.dumps(
            {
                "Path": str(root / "arithmetic.py"),
                "Overwrite": True,
                "Content": EXPECTED_ARITHMETIC,
            },
            sort_keys=True,
        )
        + ". Call mcp_native_fixture_canary with exactly {} as its arguments. "
        "Use invoke_subagent with exactly this JSON argument object: "
        + json.dumps(_subagent_request(root), sort_keys=True)
        + ". Wait for the child's completion before the final answer; do not override its model. "
    )


def agy_command_prompt(root: Path) -> str:
    """Bind the installed model-visible foreground command argument contract."""
    return (
        "For each requested run_command call, use exactly three arguments: CommandLine set to the exact requested "
        f"absolute command, toolSummary set to a short synthetic-operation description, and WaitMsBeforeAsync set to {COMMAND_WAIT_MS}. "
        "Omit all other arguments. Do not request background, persistent, or sandbox-bypass execution. "
    )


HOOK_PROGRAM = '''"""Record only synthetic metadata and force the exact approval canary."""
import json
import hashlib
import fcntl
import os
import pathlib
import shlex
import stat
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAXIMUM = 65536
TEST_COMMAND = @TEST_COMMAND@
SUBAGENT_REQUEST = @SUBAGENT_REQUEST@
CLASSIC_SUBAGENT_REQUEST = {"Subagents": [SUBAGENT_REQUEST]}
COMMAND_WAIT_MS = @COMMAND_WAIT_MS@
EXPECTED_ARITHMETIC = @EXPECTED_ARITHMETIC@
CALL_CATEGORIES = @CALL_CATEGORIES@
DIAGNOSTIC_FLAGS = @DIAGNOSTIC_FLAGS@

def write_observation(name, record, append=False):
    encoded = (json.dumps(record, sort_keys=True) + "\\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(ROOT / name, flags | (os.O_APPEND if append else 0), 0o600)
    with os.fdopen(fd, "ab" if append else "wb") as output:
        fcntl.flock(output.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(output.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise RuntimeError("Synthetic observation file is unsafe")
        if append and info.st_size + len(encoded) > MAXIMUM:
            return False
        if not append:
            output.truncate(0)
        output.write(encoded)
    return True

def exact_arithmetic_file(target):
    if target != str(ROOT / "arithmetic.py"):
        return False
    try:
        info = os.lstat(target)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1

def bounded_text(value):
    return isinstance(value, str) and len(value) <= 4096

def predicate_diagnostic(call, workspace_paths):
    observed = dict.fromkeys(DIAGNOSTIC_FLAGS, False)
    observed["call_name_category"] = "unknown"
    observed["workspace_paths_match"] = workspace_paths == [str(ROOT)]
    observed["call_object"] = isinstance(call, dict)
    if not isinstance(call, dict):
        return observed
    name = call.get("name")
    observed["call_name_textual"] = isinstance(name, str)
    category = name if isinstance(name, str) and name in CALL_CATEGORIES else "unknown"
    observed["call_name_category"] = category
    observed["args_field_present"] = "args" in call
    args = call.get("args")
    observed["args_object"] = isinstance(args, dict)
    if not isinstance(args, dict):
        return observed
    command_keys = {"CommandLine", "Cwd", "WaitMsBeforeAsync"}
    modern_command_keys = {"CommandLine", "toolSummary", "WaitMsBeforeAsync"}
    write_keys = {"TargetFile", "Overwrite", "CodeContent", "Description"}
    modern_write_keys = {"Path", "Overwrite", "Content"}
    selected_command_keys = modern_command_keys if "toolSummary" in args else command_keys
    expected_by_kind = {
        "run_command": selected_command_keys, "write_to_file": write_keys, "write_file": modern_write_keys,
        "mcp_native_fixture_canary": set(),
        "invoke_subagent": {"Subagents"} if "Subagents" in args else {"TypeName", "Role", "Prompt"}
    }
    expected = expected_by_kind.get(category, set())
    allowed = expected | ({"IsDaemon", "BypassSandbox"} if category == "run_command" and "toolSummary" in args else set())
    extra = set(args) - expected
    observed["approval_kind_supported"] = category in expected_by_kind
    observed["expected_keys_present"] = category in expected_by_kind and expected <= set(args)
    observed["unexpected_keys_present"] = bool(set(args) - allowed)
    neutral = {"IsDaemon": False, "RunPersistent": False, "BypassSandbox": False,
               "RequestedTerminalID": "", "NotificationTimeoutSeconds": 0}
    observed["neutral_extra_keys_only"] = (
        category == "run_command" and bool(extra) and extra <= set(neutral)
        and all(type(args[key]) is type(neutral[key]) and args[key] == neutral[key] for key in extra)
    )
    for key, prefix in (("CommandLine", "command"), ("Cwd", "cwd"), ("WaitMsBeforeAsync", "wait_ms"),
                        ("TargetFile", "write_target"), ("CodeContent", "write_code"),
                        ("Overwrite", "write_overwrite"), ("Description", "write_description"),
                        ("Subagents", "subagents")):
        observed[prefix + "_key_present"] = key in args
    observed["command_textual"] = isinstance(args.get("CommandLine"), str)
    observed["cwd_textual"] = isinstance(args.get("Cwd"), str)
    observed["wait_ms_integer"] = type(args.get("WaitMsBeforeAsync")) is int
    observed["write_target_textual"] = isinstance(args.get("TargetFile"), str)
    observed["write_code_textual"] = isinstance(args.get("CodeContent"), str)
    observed["write_overwrite_boolean"] = type(args.get("Overwrite")) is bool
    observed["write_description_textual"] = isinstance(args.get("Description"), str)
    observed["subagents_array"] = isinstance(args.get("Subagents"), list)
    observed["command_matches"] = args.get("CommandLine") in (TEST_COMMAND, TEST_COMMAND + " cancel")
    if observed["command_textual"]:
        try:
            actual = shlex.split(args["CommandLine"])
            observed["command_argv_matches"] = actual in (shlex.split(TEST_COMMAND), shlex.split(TEST_COMMAND + " cancel"))
        except ValueError:
            pass
    observed["cwd_matches"] = args.get("Cwd") == str(ROOT)
    observed["wait_ms_matches"] = observed["wait_ms_integer"] and args["WaitMsBeforeAsync"] == COMMAND_WAIT_MS
    observed["command_keys_exact"] = set(args) in (command_keys, modern_command_keys)
    observed["command_summary_bounded"] = bounded_text(args.get("toolSummary"))
    target = args.get("Path") if category == "write_file" else args.get("TargetFile")
    content = args.get("Content") if category == "write_file" else args.get("CodeContent")
    if category == "write_file":
        observed["write_target_key_present"] = "Path" in args
        observed["write_target_textual"] = isinstance(target, str)
        observed["write_code_key_present"] = "Content" in args
        observed["write_code_textual"] = isinstance(content, str)
    observed["write_path_matches"] = target == str(ROOT / "arithmetic.py")
    observed["write_target_safe"] = exact_arithmetic_file(target)
    observed["write_code_matches"] = content == EXPECTED_ARITHMETIC
    observed["write_overwrite_matches"] = args.get("Overwrite") is True
    observed["write_description_bounded"] = bounded_text(args.get("Description"))
    observed["write_keys_exact"] = set(args) in (write_keys, modern_write_keys)
    observed["mcp_empty_args"] = category == "mcp_native_fixture_canary" and args == {}
    observed["subagent_request_matches"] = category == "invoke_subagent" and args in (SUBAGENT_REQUEST, CLASSIC_SUBAGENT_REQUEST)
    return observed

def fixture_approval(call, workspace_paths):
    if not isinstance(call, dict):
        return None
    name, args = call.get("name"), call.get("args")
    if not isinstance(args, dict):
        return None
    if name == "run_command":
        classic = set(args) == {"CommandLine", "Cwd", "WaitMsBeforeAsync"} and args.get("Cwd") == str(ROOT)
        required = {"CommandLine", "toolSummary", "WaitMsBeforeAsync"}
        modern = (
            required <= set(args) <= required | {"IsDaemon", "BypassSandbox"}
            and bounded_text(args.get("toolSummary")) and workspace_paths == [str(ROOT)]
            and args.get("IsDaemon", False) is False and args.get("BypassSandbox", False) is False
        )
        if (classic or modern) and type(args.get("WaitMsBeforeAsync")) is int and args["WaitMsBeforeAsync"] == COMMAND_WAIT_MS:
            if args.get("CommandLine") == TEST_COMMAND:
                return "test_command"
            if args.get("CommandLine") == TEST_COMMAND + " cancel":
                return "cancel_command"
    if (
        name == "write_to_file" and set(args) == {"TargetFile", "Overwrite", "CodeContent", "Description"}
        and exact_arithmetic_file(args.get("TargetFile")) and args.get("Overwrite") is True
        and args.get("CodeContent") == EXPECTED_ARITHMETIC and bounded_text(args.get("Description"))
    ):
        return "arithmetic_edit"
    if (
        name == "write_file" and set(args) == {"Path", "Overwrite", "Content"}
        and exact_arithmetic_file(args.get("Path")) and args.get("Overwrite") is True
        and args.get("Content") == EXPECTED_ARITHMETIC
    ):
        return "arithmetic_edit"
    if name == "mcp_native_fixture_canary" and args == {}:
        return "mcp_canary"
    if name == "invoke_subagent" and args in (SUBAGENT_REQUEST, CLASSIC_SUBAGENT_REQUEST):
        return "subagent"
    return None

kind = sys.argv[1] if len(sys.argv) == 2 else "unknown"
raw = sys.stdin.read(1024 * 1024 + 1)
try:
    event = json.loads(raw) if len(raw) <= 1024 * 1024 else None
except (ValueError, UnicodeError):
    event = None
valid = isinstance(event, dict)
record = {"event": kind if kind in {"session_start", "pre_tool", "post_tool", "stop"} else "unknown", "payload_valid": valid}
decision = {"decision": "allow"} if kind == "pre_tool" else {"decision": "stop"} if kind == "stop" else {}
if valid:
    identity = event.get("conversationId")
    if isinstance(identity, str) and 0 < len(identity) <= 128:
        record["conversation_fingerprint"] = hashlib.sha256(identity.encode()).hexdigest()
if kind == "pre_tool" and valid:
    call = event.get("toolCall")
    record.update(predicate_diagnostic(call, event.get("workspacePaths")))
    record["fixture_predicate_unmatched"] = False
    args = call.get("args") if isinstance(call, dict) else None
    targets = (args.get("TargetFile"), args.get("Path")) if isinstance(args, dict) else ()
    matched = any(isinstance(target, str) and target == str(ROOT / "permission-needed.txt") for target in targets)
    record["permission_target_matches"] = matched
    if matched:
        write_observation("agy-permission-hook-observed.json", {"target_matches": True, "force_ask_emitted": True})
        decision = {"decision": "force_ask", "reason": "Synthetic approval probe"}
    else:
        approved_kind = fixture_approval(call, event.get("workspacePaths"))
        record["fixture_predicate_unmatched"] = approved_kind is None
        if approved_kind is not None:
            record["fixture_auto_approval_emitted"] = True
            record["fixture_approval_kind"] = approved_kind
            # Native auto_approve handles defaults; explicit user Ask/Deny rules retain priority.
            decision = {"decision": "auto_approve"}
if kind == "post_tool" and valid:
    call = event.get("toolCall")
    args = call.get("args") if isinstance(call, dict) else None
    record["exact_canary_read_completed"] = (
        isinstance(call, dict) and event.get("error", "") == "" and (
            (call.get("name") == "read_file" and args == {"Path": str(ROOT / "specialist.txt")})
            or (call.get("name") == "view_file" and args == {"AbsolutePath": str(ROOT / "specialist.txt")})
        )
    )
if kind == "stop" and valid:
    record["loop_idle_stop_observed"] = (
        event.get("fullyIdle") is True and event.get("terminationReason") == "model_stop" and event.get("error", "") == ""
    )
if not write_observation("agy-hooks.jsonl", record, append=True):
    write_observation("agy-hook-log-limit", {"observation_limit_reached": True})
print(json.dumps(decision))
'''


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            return []
        data = stream.read(MAX_OBSERVATION_BYTES + 1)
    if len(data) > MAX_OBSERVATION_BYTES:
        return []
    rows = []
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            return []
        if not isinstance(value, dict):
            return []
        rows.append(value)
    return rows


def _write_observation(path: Path, value: dict[str, bool]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "wb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Agy observation must be an owner-owned singly linked regular file")
        stream.truncate(0)
        stream.write(json.dumps(value, sort_keys=True).encode() + b"\n")


def create_agy_fixture(root: Path, *, test_command: str) -> list[str]:
    """Add documented project hooks, MCP and a read-only configured subagent."""
    if not root.is_absolute() or root != root.resolve() or not root.is_dir():
        raise ValueError("Agy fixture requires an existing canonical synthetic project")
    marker = root / ".clink-native-verification-fixture"
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("Agy fixture requires the native verifier's synthetic-project marker")
    if test_command != agy_fixture_command(root):
        raise ValueError("Agy fixture requires its exact absolute synthetic command")
    hook_path = root / "agy_hook.py"
    hook_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(hook_path))}"
    hooks = {
        "clink-native-fixture": {
            "SessionStart": [{"type": "command", "command": hook_command + " session_start", "timeout": 5}],
            "PreToolUse": [
                {
                    "matcher": FIXTURE_TOOLS,
                    "hooks": [
                        {"type": "command", "command": hook_command + " pre_tool", "timeout": 5},
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "^(view_file|read_file)$",
                    "hooks": [
                        {"type": "command", "command": hook_command + " post_tool", "timeout": 5},
                    ],
                }
            ],
            "Stop": [{"type": "command", "command": hook_command + " stop", "timeout": 5}],
        }
    }
    files = {
        "agy_hook.py": HOOK_PROGRAM.replace("@TEST_COMMAND@", repr(test_command))
        .replace("@SUBAGENT_REQUEST@", repr(_subagent_request(root)))
        .replace("@COMMAND_WAIT_MS@", repr(COMMAND_WAIT_MS))
        .replace("@EXPECTED_ARITHMETIC@", repr(EXPECTED_ARITHMETIC))
        .replace("@CALL_CATEGORIES@", repr(CALL_CATEGORIES))
        .replace("@DIAGNOSTIC_FLAGS@", repr(DIAGNOSTIC_FLAGS)),
        "agy_mcp_server.py": MCP_SERVER,
        ".agents/hooks.json": json.dumps(hooks, indent=2) + "\n",
        ".agents/mcp_config.json": json.dumps(
            {
                "mcpServers": {
                    "native_fixture": {
                        "command": sys.executable,
                        "args": [str(root / "agy_mcp_server.py")],
                        "timeoutSeconds": 10,
                    }
                }
            },
            indent=2,
        )
        + "\n",
        ".agents/agents/fixture-specialist.md": (
            "---\nname: fixture-specialist\ndescription: Read the synthetic specialist canary when explicitly delegated.\n"
            "tools:\n  - read_file\nhooks:\n  - ../hooks.json\nsubagent: true\nmainAgent: false\nmodel: inherit\n---\n"
            "Read only specialist.txt in this synthetic project and return its exact token. "
            "Do not edit files, run commands, or delegate.\n"
        ),
        "specialist.txt": SPECIALIST_TOKEN + "\n",
    }
    for name in files:
        path = root / name
        if path.exists() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
            raise ValueError("Agy fixture refuses existing files or symlinked configuration paths")
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    return sorted(files)


def agy_fixture_observations(root: Path) -> dict[str, bool]:
    """Return bounded metadata; result envelopes must establish native refusal separately."""
    events = _read_rows(root / "agy-hooks.jsonl")
    permissions = _read_rows(root / "agy-permission-hook-observed.json")
    permission_observed = (
        len(permissions) == 1
        and permissions[0].get("target_matches") is True
        and permissions[0].get("force_ask_emitted") is True
    )
    protected = root / "permission-needed.txt"
    observations = {
        "scope_hook_observed": any(event.get("payload_valid") is True for event in events),
        "agy_session_start_observed": any(
            event.get("event") == "session_start" and event.get("payload_valid") is True for event in events
        ),
        "permission_hook_observed": permission_observed,
        "protected_file_untouched": not protected.exists() and not protected.is_symlink(),
        "hook_observation_limit_reached": (root / "agy-hook-log-limit").exists(),
    }
    observations.update(
        {
            "agy_fixture_"
            + kind
            + "_approval_emitted": any(
                event.get("event") == "pre_tool"
                and event.get("fixture_auto_approval_emitted") is True
                and event.get("fixture_approval_kind") == kind
                for event in events
            )
            for kind in APPROVAL_KINDS
        }
    )
    unmatched = [
        event
        for event in events
        if event.get("event") == "pre_tool" and event.get("fixture_predicate_unmatched") is True
    ]
    observations.update(
        agy_unmatched_pre_tool_observed=bool(unmatched),
        agy_multiple_unmatched_pre_tools=len(unmatched) > 1,
    )
    observations.update(
        {"agy_" + flag: any(event.get(flag) is True for event in unmatched) for flag in DIAGNOSTIC_FLAGS}
    )
    observations.update(
        {
            "agy_call_is_" + category: any(event.get("call_name_category") == category for event in unmatched)
            for category in CALL_CATEGORIES
        }
    )
    rows = _read_rows(root / "agy-subagent-observed.json")
    correlations = rows[0] if len(rows) == 1 else {}
    observations.update(
        {
            "fixture_subagent_started": correlations.get("child_session_start_observed") is True,
            "fixture_subagent_stopped": correlations.get("child_loop_stopped_observed") is True,
            "fixture_subagent_read_observed": correlations.get("child_exact_canary_read_observed") is True,
            "fixture_subagent_token_return_verified": False,
        }
    )
    return observations


def observe_agy_stream(root: Path, stdout: str) -> None:
    """Correlate private native child identities in memory and persist booleans only.

    PostToolUse has no result body, so a completed exact child read is the
    strongest available canary observation. It does not prove returned text.
    """
    roots = set()
    children = set()
    valid = len(stdout.encode("utf-8", errors="replace")) <= 10 * 1024 * 1024
    if valid:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                valid = False
                break
            if not isinstance(event, dict):
                continue
            if event.get("event") == "init":
                identity = event.get("conversation_id")
                if isinstance(identity, str) and 0 < len(identity) <= 128:
                    roots.add(hashlib.sha256(identity.encode()).hexdigest())
            step = event.get("step_update") if event.get("event") == "step_update" else None
            info = step.get("subagent_info") if isinstance(step, dict) else None
            agents = info.get("subagents") if isinstance(info, dict) else None
            if isinstance(agents, list):
                for child in agents:
                    if not isinstance(child, dict) or child.get("type_name") != "fixture-specialist":
                        continue
                    identity = child.get("conversation_id")
                    if isinstance(identity, str) and 0 < len(identity) <= 128:
                        children.add(hashlib.sha256(identity.encode()).hexdigest())
    events = _read_rows(root / "agy-hooks.jsonl")
    children -= roots
    observations = {
        "child_identity_distinct_from_root": valid and len(roots) == 1 and bool(children),
        "child_session_start_observed": False,
        "child_loop_stopped_observed": False,
        "child_exact_canary_read_observed": False,
        "token_return_verified": False,
    }
    if observations["child_identity_distinct_from_root"]:
        # One named child must supply all observations; never combine unrelated child events.
        for fingerprint in children:
            matching = [
                event
                for event in events
                if event.get("conversation_fingerprint") == fingerprint and event.get("payload_valid") is True
            ]
            started = any(event.get("event") == "session_start" for event in matching)
            stopped = any(
                event.get("event") == "stop" and event.get("loop_idle_stop_observed") is True for event in matching
            )
            read = any(
                event.get("event") == "post_tool" and event.get("exact_canary_read_completed") is True
                for event in matching
            )
            if started and stopped and read:
                observations.update(
                    child_session_start_observed=True,
                    child_loop_stopped_observed=True,
                    child_exact_canary_read_observed=True,
                )
                break
    _write_observation(root / "agy-subagent-observed.json", observations)
