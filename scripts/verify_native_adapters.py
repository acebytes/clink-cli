"""Verify explicitly selected native adapters with synthetic, bounded lifecycle tasks.

Candidate admission exists only in this process. Receipts contain technical
observations, never prompts, argv, raw streams, native session IDs or environment
values. Explicit --trust-grok-fixture and --trust-codex-fixture permit scoped
enrollment of their bound synthetic fixtures; cleanup removes unchanged owned
trust records. Native configuration and permissions remain authoritative.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from clink.backend import CLIBackend  # noqa: E402
from clink.contracts import AdapterPolicy, BackendError  # noqa: E402
from clink.native import NativeRuntime  # noqa: E402
from clink.native_policy import native_arguments, native_recipe_digest  # noqa: E402
from clink.parsers.agy_native import MAX_EVENTS as AGY_MAX_EVENTS  # noqa: E402
from clink.parsers.agy_native import AgyNativeParser  # noqa: E402
from clink.parsers.base import ParserError, load_json  # noqa: E402
from clink.policy import executable_digest  # noqa: E402
from clink.registry import ClinkRegistry  # noqa: E402
from clink.runner import ProcessPool  # noqa: E402
from clink.tasks import NativeTaskManager  # noqa: E402
from scripts.native_agy_fixture import (  # noqa: E402
    agy_capability_prompt,
    agy_command_prompt,
    agy_fixture_command,
    agy_fixture_observations,
    create_agy_fixture,
    observe_agy_stream,
)
from scripts.native_codex_fixture import CAPABILITY_PROMPT as CODEX_CAPABILITY_PROMPT  # noqa: E402
from scripts.native_codex_fixture import PERMISSION_COMMAND as CODEX_PERMISSION_COMMAND  # noqa: E402
from scripts.native_codex_fixture import PERMISSION_PROMPT as CODEX_PERMISSION_PROMPT  # noqa: E402
from scripts.native_codex_fixture import (  # noqa: E402
    codex_fixture_gaps,
    codex_fixture_observations,
    create_codex_fixture,
    observe_codex_stream,
)
from scripts.native_codex_trust import CLEANUP_ERROR_CODES as CODEX_CONTROL_CLEANUP_ERRORS  # noqa: E402
from scripts.native_codex_trust import ERROR_CODES as CODEX_TRUST_ERROR_CODES  # noqa: E402
from scripts.native_codex_trust import NativeRpc, TrustError, TrustScope, approved_binding  # noqa: E402
from scripts.native_denial_diagnostics import native_denial_diagnostic, valid_native_denial_diagnostic  # noqa: E402
from scripts.native_diagnostics import exception_diagnostic, valid_exception_diagnostic  # noqa: E402
from scripts.native_fixture_trust import grok_fixture_trust  # noqa: E402
from scripts.native_mcp_fixture import MCP_SERVER, mcp_environment_observed  # noqa: E402
from scripts.verify_native_cli import SENTINEL_NAME, SENTINEL_VALUE  # noqa: E402

ADAPTERS = ("grok", "agy", "codex")
RELEASES = {"grok": "1.0.70", "agy": "1.4.12", "codex": "0.166.0"}
AGY_PARSER_FAILURES = {
    "Antigravity native output was empty or exceeded the event limit": "empty_or_event_limit",
    "CLI returned malformed JSON": "malformed_json",
    "Antigravity native output used an unsupported stream dialect": "unsupported_stream_dialect",
    "Antigravity native output mixed JSON dialects": "mixed_json_dialects",
    "Antigravity native output continued after its terminal result": "events_after_terminal",
    "Antigravity native events must contain typed payload objects": "invalid_payload_object",
    "Antigravity native conversation identity is invalid": "invalid_conversation_identity",
    "Antigravity native session identity is invalid": "invalid_session_identity",
    "Antigravity native output changed its conversation identity": "conversation_identity_changed",
    "Antigravity native initialization is invalid": "invalid_initialization",
    "Antigravity native step omitted its conversation identity": "step_session_missing",
    "Antigravity native output omitted its terminal result": "terminal_result_missing",
    "Antigravity native terminal status is invalid": "invalid_terminal_status",
    "Antigravity native terminal finish reason is invalid": "invalid_finish_reason",
    "Antigravity native terminal error must be textual": "terminal_error_not_text",
    "Antigravity native response must be textual": "response_not_text",
    "Antigravity native denied actions must be a bounded array": "invalid_denied_actions",
    "Antigravity native denied action is invalid": "invalid_denied_action",
    "Antigravity native usage must be an object": "invalid_usage",
    "Antigravity native successful result omitted its conversation identity": "successful_session_missing",
    "Antigravity native step identity or state is invalid": "invalid_step_identity_or_state",
    "Antigravity native step text must be textual": "step_text_not_text",
    "Antigravity native tool information must be an object": "invalid_tool_info",
    "Antigravity native tool error is invalid": "invalid_tool_error",
    "CLI authentication is required": "authentication_required",
    "CLI rate limit was reached": "rate_limited",
}
AGY_PARSER_FAILURE_CODES = frozenset(AGY_PARSER_FAILURES.values()) | {"unknown_parser_error"}
AGY_PARSER_SHAPE_FLAGS = frozenset(
    {
        "output_empty",
        "event_limit_exceeded",
        "json_lines_valid",
        "init_seen",
        "step_seen",
        "result_seen",
        "result_payload_object",
        "response_field_present",
        "response_textual",
        "error_field_present",
        "error_textual",
        "session_field_present",
        "session_textual",
    }
)
AGY_PARSER_STATUSES = frozenset(
    {"UNKNOWN", "SUCCESS", "ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING", "RUNNING"}
)
AGY_PARSER_FINISH_REASONS = frozenset({"print_timeout", "interrupted", "error"})
CHECKS = ("edit", "followup", "permission", "cancel")
MAX_SECONDS = 180
MAX_SUBMISSIONS = 4
MAX_RECEIPT_BYTES = 1024 * 1024
SCOPE = "candidate_native_adapter_lifecycle"
CONTEXT_TOKEN = "NATIVE_FIXTURE_CONTEXT_274816"
SPECIALIST_TOKEN = "SPECIALIST_CANARY_854123"
INITIAL_ARITHMETIC = "def add(a, b):\n    return a - b\n"
CONFIG_GATES = {"project_trust_enrolled", "task_scope_controls_configured"}

FIXTURE_PROGRAM = '''"""Disposable arithmetic tests and a foreground cancellation target."""
import json
import os
import pathlib
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
if len(sys.argv) > 1 and sys.argv[1] == "cancel":
    child = subprocess.Popen([sys.executable, __file__, "child"])
    (ROOT / "child.pid").write_text(str(child.pid))
    (ROOT / "mutation.txt").write_text("known synthetic partial effect\\n")
    child.wait()
elif len(sys.argv) > 1 and sys.argv[1] == "child":
    (ROOT / "child-ready.txt").write_text("synthetic child entered its Python program\\n")
    time.sleep(240)
else:
    sys.dont_write_bytecode = True
    from arithmetic import add
    class ArithmeticTest(unittest.TestCase):
        def test_addition(self):
            self.assertEqual(add(2, 3), 5)
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ArithmeticTest))
    with (ROOT / "fixture-checks.jsonl").open("a") as output:
        output.write(json.dumps({"origin": "harness" if os.environ.get("NATIVE_FIXTURE_HOST_CHECK") else "native",
            "passed": result.wasSuccessful()}) + "\\n")
    sys.exit(0 if result.wasSuccessful() else 1)
'''

GROK_SCOPE_HOOK = '''"""Observe native tools; ask for the fixture permission canary only."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TEST_COMMAND = @TEST_COMMAND@
event = json.load(sys.stdin)
tool = event.get("toolName", event.get("tool_name", ""))
data = event.get("toolInput", event.get("tool_input")) or {}
event_label = event.get("hook_event_name", event.get("hookEventName", ""))
events = {"pretooluse": "pre_tool", "posttooluse": "post_tool", "subagentstart": "subagent_start", "subagentstop": "subagent_stop"}
event_kind = events.get(str(event_label).replace("_", "").lower(), "other")
fixture_subagent = event.get("subagentType") == "fixture-specialist"
fixture_read = False
canary_matches = False
allowed = False
category = "other"
decision = "native"
if isinstance(data, dict) and not event.get("toolInputTruncated", False):
    if tool in {"Read", "read_file", "Write", "write", "StrReplace", "search_replace"}:
        category = "read" if tool in {"Read", "read_file"} else "write"
        value = data.get("target_file", data.get("path", data.get("file_path", "")))
        if isinstance(value, str) and value:
            path = pathlib.Path(value)
            if not path.is_absolute():
                path = ROOT / path
            path = path.resolve()
            fixture_read = fixture_subagent and category == "read" and path == ROOT / "specialist.txt"
            if fixture_read and event_kind == "post_tool":
                result = event.get("toolResult", event.get("tool_response"))
                body = result.get("FileContent") if isinstance(result, dict) and result.get("type") == "ReadFile" else None
                canary_matches = isinstance(body, dict) and any(
                    isinstance(body.get(key), str) and "SPECIALIST_CANARY_854123" in body[key]
                    for key in ("raw_output", "content")
                )
            allowed = path in {ROOT / "arithmetic.py", ROOT / "fixture.py", ROOT / "permission-needed.txt"}
            if category == "write":
                allowed = allowed and path.name in {"arithmetic.py", "permission-needed.txt"}
            if allowed and category == "write" and path.name == "permission-needed.txt":
                decision = "ask"
    elif tool in {"Shell", "run_terminal_command", "Bash"}:
        category = "shell"
        allowed = data.get("command") in {TEST_COMMAND, TEST_COMMAND + " cancel"} and not data.get("run_in_background", False)
    elif tool in {"native_fixture__canary", "mcp__native_fixture__canary"}:
        category = "fixture_mcp"
        allowed = True
    elif tool in {"task", "spawn_subagent", "Task", "Agent"}:
        category = "subagent"
with (ROOT / "hooks.jsonl").open("a") as output:
    output.write(json.dumps({"event": event_kind, "category": category, "allowed": allowed, "decision": decision,
        "fixture_subagent": fixture_subagent, "fixture_read": fixture_read, "canary_matches": canary_matches}) + "\\n")
print(json.dumps({"decision": "ask", "reason": "Synthetic fixture permission canary."} if decision == "ask" else {}))
'''


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("Verification receipt exceeds its byte limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Verification receipts must be JSON objects")
    return value


def verification_path(path: Path) -> Path:
    root = (PROJECT / "docs" / "verification").resolve()
    resolved = path.expanduser().resolve()
    if root not in resolved.parents or resolved.suffix != ".json":
        raise ValueError("Evidence must be a durable JSON file inside docs/verification")
    return resolved


def persist(path: Path, receipt: dict[str, Any], *, exclusive: bool = False) -> None:
    encoded = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if len(encoded.encode()) > MAX_RECEIPT_BYTES:
        raise ValueError("Verification evidence exceeded its byte limit")
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + ".writing")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def fixture_command() -> str:
    return f"{shlex.quote(sys.executable)} fixture.py"


def create_fixture(root: Path, adapter: str) -> list[str]:
    """Write synthetic project files; never inspect or modify native user settings."""
    root.mkdir(mode=0o700)
    (root / ".clink-native-verification-fixture").write_text("synthetic-native-adapter-fixture-v1\n")
    (root / "arithmetic.py").write_text(INITIAL_ARITHMETIC, encoding="utf-8")
    fixture_program = FIXTURE_PROGRAM
    if adapter == "agy":
        fixture_program = fixture_program.replace(
            "ROOT = pathlib.Path(__file__).resolve().parent\n",
            "ROOT = pathlib.Path(__file__).resolve().parent\n"
            "if pathlib.Path.cwd().resolve() != ROOT:\n"
            "    raise SystemExit('Synthetic Agy fixture requires its exact project working directory')\n",
            1,
        )
    (root / "fixture.py").write_text(fixture_program, encoding="utf-8")
    (root / "mcp_server.py").write_text(MCP_SERVER, encoding="utf-8")
    (root / "native-role.txt").write_text("Perform only the explicitly assigned synthetic fixture operation.\n")
    test_command = agy_fixture_command(root) if adapter == "agy" else fixture_command()
    instructions = (
        "This independent disposable project is the complete task scope. Do not access credentials, "
        "host files, unrelated tools or integrations. Never change configuration. Stop on permission denial. "
        f"Use exactly `{test_command}` for tests and append ` cancel` only when explicitly asked.\n"
    )
    (root / "AGENTS.md").write_text(instructions, encoding="utf-8")
    config_files = [".clink-native-verification-fixture", "AGENTS.md", "fixture.py", "mcp_server.py", "native-role.txt"]
    if adapter == "grok":
        (root / ".grok" / "hooks").mkdir(parents=True)
        (root / ".grok" / "agents").mkdir()
        (root / "specialist.txt").write_text(SPECIALIST_TOKEN + "\n")
        (root / ".grok" / "agents" / "fixture-specialist.md").write_text(
            "---\nname: fixture-specialist\ndescription: Read the synthetic specialist canary when explicitly delegated.\n"
            "tools:\n  - read_file\n---\nRead only specialist.txt in this synthetic project and return its exact token. "
            "Do not edit files, run commands, or delegate.\n"
        )
        (root / "GROK.md").write_text(instructions, encoding="utf-8")
        (root / "scope_hook.py").write_text(
            GROK_SCOPE_HOOK.replace("@TEST_COMMAND@", repr(fixture_command())), encoding="utf-8"
        )
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(root / 'scope_hook.py'))}"
        (root / ".grok" / "hooks" / "native-fixture.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        event_name: [{"matcher": "", "hooks": [{"type": "command", "command": command, "timeout": 10}]}]
                        for event_name in ("PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop")
                    }
                }
            )
        )
        rules = [
            "Read(./arithmetic.py)",
            "Read(./fixture.py)",
            "Edit(./arithmetic.py)",
            "Write(./arithmetic.py)",
            f"Bash({fixture_command()})",
            f"Bash({fixture_command()} cancel)",
            "MCPTool(native_fixture__canary)",
            # Grok maps all subagent spawns to the generic tool ID `task`.
            # Its native rules cannot select one specialist. This grants that
            # tool only in the disposable project, without changing its mode.
            "task",
        ]
        config = (
            "[permission]\nallow = " + json.dumps(rules) + "\n"
            'ask = ["Write(./permission-needed.txt)", "Edit(./permission-needed.txt)"]\n'
            "[mcp_servers.native_fixture]\ncommand = " + json.dumps(sys.executable) + "\n"
            "args = " + json.dumps([str(root / "mcp_server.py")]) + "\nenabled = true\n"
            'env = { NATIVE_FIXTURE_SENTINEL = "${NATIVE_FIXTURE_SENTINEL}" }\n'
        )
        (root / ".grok" / "config.toml").write_text(config, encoding="utf-8")
        config_files += [
            "GROK.md",
            "scope_hook.py",
            ".grok/config.toml",
            ".grok/hooks/native-fixture.json",
            ".grok/agents/fixture-specialist.md",
            "specialist.txt",
        ]
    elif adapter == "agy":
        config_files += create_agy_fixture(root, test_command=test_command)
    elif adapter == "codex":
        config_files += create_codex_fixture(root)
    git_environment = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=" + os.devnull, "init", "-q", str(root)],
        env=git_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=True,
    )
    return config_files


def fixture_digest(root: Path, files: list[str]) -> str:
    if any((root / name).is_symlink() or not (root / name).is_file() for name in files):
        raise ValueError("Fixture configuration must retain its generated regular files")
    return digest(json.dumps({name: digest((root / name).read_bytes()) for name in sorted(files)}, sort_keys=True))


def fixture_intact(root: Path, evidence: dict[str, Any]) -> bool:
    try:
        return fixture_digest(root, evidence["fixture_config_files"]) == evidence["fixture_config_sha256"]
    except (OSError, ValueError):
        return False


def fixture_test(root: Path) -> bool:
    result = subprocess.run(
        [sys.executable, "fixture.py"],
        cwd=root,
        env={"NATIVE_FIXTURE_HOST_CHECK": "1", "PATH": os.environ.get("PATH", "")},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def preflight(executable: str, adapter: str) -> dict[str, Any]:
    """Probe only version; bind the installed executable and native recipe without admission."""
    before_digest = executable_digest(executable)
    observed = subprocess.run(
        [executable, "--version"], stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False
    )
    after_digest = executable_digest(executable)
    if before_digest != after_digest:
        raise ValueError("Installed CLI executable changed during its version probe; no submission made")
    version = observed.stdout.decode("utf-8").strip()
    if observed.returncode != 0 or len(version) > 128 or not re.fullmatch(r"[A-Za-z0-9 ()._+-]+", version):
        raise ValueError("Installed CLI version could not be safely established")
    if not re.search(r"(?<![0-9.])" + re.escape(RELEASES[adapter]) + r"(?![0-9.])", version):
        raise ValueError("Installed CLI differs from the researched release")
    return {
        "adapter": adapter,
        "observed_version": version,
        "executable_sha256": after_digest,
        "native_recipe_sha256": native_recipe_digest(adapter),
    }


def safe_identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value) else None


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: snapshot[key]
        for key in ("possible_partial_effects", "cancellation_requested", "result_unavailable", "followup_available")
        if type(snapshot.get(key)) is bool
    }
    summary["state"] = (
        snapshot.get("state")
        if snapshot.get("state")
        in {"queued", "running", "completed", "permission_needed", "failed", "cancelled", "uncertain", "unavailable"}
        else "unknown"
    )
    code = safe_identifier(snapshot.get("error", {}).get("code"))
    if code:
        summary["error_code"] = code
    metadata = snapshot.get("result", {}).get("metadata", {})
    actual_model = safe_identifier(metadata.get("actual_model"))
    summary["actual_model"] = actual_model
    summary["actual_model_origin"] = "native_terminal_metadata" if actual_model else "unknown"
    return summary


def agy_parser_diagnostic(stdout: str, stderr: str) -> dict[str, Any]:
    """Retain fixed failure/shape categories, never native values or exception text."""
    try:
        AgyNativeParser().parse(stdout, stderr)
    except ParserError as exc:
        code = AGY_PARSER_FAILURES.get(str(exc), "unknown_parser_error")
    else:
        return {}
    shape: dict[str, Any] = dict.fromkeys(AGY_PARSER_SHAPE_FLAGS, False)
    lines = [line for line in stdout.splitlines() if line.strip()]
    shape.update(
        output_empty=not lines,
        event_limit_exceeded=len(lines) > AGY_MAX_EVENTS,
        json_lines_valid=len(lines) <= AGY_MAX_EVENTS,
        terminal_status="missing",
        finished_reason="missing",
    )
    terminal: dict[str, Any] | None = None
    for line in lines[:AGY_MAX_EVENTS]:
        try:
            event = load_json(line)
        except ParserError:
            shape["json_lines_valid"] = False
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        if kind == "init":
            shape["init_seen"] = True
        elif kind == "step_update":
            shape["step_seen"] = True
        elif kind == "result":
            shape["result_seen"] = True
            body = event.get("result")
            shape["result_payload_object"] = isinstance(body, dict)
            terminal = body if isinstance(body, dict) else None
    if terminal is not None:
        for field, prefix in (("response", "response"), ("error", "error"), ("conversation_id", "session")):
            shape[prefix + "_field_present"] = field in terminal
            shape[prefix + "_textual"] = isinstance(terminal.get(field), str)
        status = terminal.get("status")
        if "status" not in terminal:
            shape["terminal_status"] = "missing"
        elif isinstance(status, str) and status in AGY_PARSER_STATUSES:
            shape["terminal_status"] = status
        else:
            shape["terminal_status"] = "invalid"
        reason = terminal.get("finished_reason")
        if "finished_reason" not in terminal:
            shape["finished_reason"] = "missing"
        elif reason == "":
            shape["finished_reason"] = "empty"
        elif isinstance(reason, str) and reason in AGY_PARSER_FINISH_REASONS:
            shape["finished_reason"] = reason
        else:
            shape["finished_reason"] = "invalid"
    return {"native_parser_failure_code": code, "native_parser_shape": shape}


def valid_agy_parser_diagnostic(entry: dict[str, Any]) -> bool:
    """Accept only the exact fixed Agy diagnostic vocabulary in prior receipts."""
    code, shape = entry.get("native_parser_failure_code"), entry.get("native_parser_shape")
    if not isinstance(code, str) or code not in AGY_PARSER_FAILURE_CODES or not isinstance(shape, dict):
        return False
    if set(shape) != AGY_PARSER_SHAPE_FLAGS | {"terminal_status", "finished_reason"}:
        return False
    if any(type(shape[key]) is not bool for key in AGY_PARSER_SHAPE_FLAGS):
        return False
    return (
        isinstance(shape["terminal_status"], str)
        and shape["terminal_status"] in AGY_PARSER_STATUSES | {"missing", "invalid"}
        and isinstance(shape["finished_reason"], str)
        and shape["finished_reason"] in AGY_PARSER_FINISH_REASONS | {"missing", "invalid", "empty"}
    )


def load_history(previous: Path | None, adapter: str) -> tuple[list[dict[str, Any]], int, str | None]:
    if previous is None:
        return [], MAX_SUBMISSIONS, None
    previous = verification_path(previous)
    receipt = read_json(previous)
    ledger = receipt.get("submission_ledger")
    allowance = receipt.get("submission_allowance_total")
    if receipt.get("scope") != SCOPE or receipt.get("adapter") != adapter or not isinstance(ledger, list):
        raise ValueError("Prior evidence belongs to a different verification lineage")
    if type(allowance) is not int or not MAX_SUBMISSIONS <= allowance <= 64 or len(ledger) > allowance:
        raise ValueError("Prior evidence has invalid submission accounting")
    allowed = {
        "ordinal",
        "check",
        "spawn_observed",
        "reason",
        "state",
        "error_code",
        "passed",
        "observations",
        "possible_partial_effects",
        "cancellation_requested",
        "result_unavailable",
        "followup_available",
        "actual_model",
        "actual_model_origin",
        "process_group_absent_after_cleanup",
        "exit_status",
        "stdout_bytes",
        "stderr_bytes",
        "exception_class",
        "exception_location",
        "os_error_number",
        "cleanup_exception",
        "cleanup_observation_exception",
        "native_parser_failure_code",
        "native_parser_shape",
        "native_denial_diagnostic",
    }
    for ordinal, entry in enumerate(ledger, 1):
        if (
            not isinstance(entry, dict)
            or set(entry) - allowed
            or entry.get("ordinal") != ordinal
            or entry.get("check") not in CHECKS
        ):
            raise ValueError("Prior evidence has an invalid submission ledger")
        observations = entry.get("observations", {})
        if not isinstance(observations, dict) or any(
            not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,64}", key) or type(value) is not bool
            for key, value in observations.items()
        ):
            raise ValueError("Prior evidence contains nontechnical observation data")
        for key in {
            "spawn_observed",
            "passed",
            "possible_partial_effects",
            "cancellation_requested",
            "result_unavailable",
            "followup_available",
            "process_group_absent_after_cleanup",
        }:
            if key in entry and type(entry[key]) is not bool:
                raise ValueError("Prior evidence contains invalid technical flags")
        for key in {"error_code", "actual_model"}:
            if entry.get(key) is not None and safe_identifier(entry[key]) is None:
                raise ValueError("Prior evidence contains invalid technical identifiers")
        if entry.get("reason") not in {None, "cancelled", "backend_error", "exited", "unexpected_exception"}:
            raise ValueError("Prior evidence contains an invalid technical outcome")
        diagnostic = {
            key: entry[key] for key in ("exception_class", "exception_location", "os_error_number") if key in entry
        }
        if (diagnostic or entry.get("reason") == "unexpected_exception") and not valid_exception_diagnostic(diagnostic):
            raise ValueError("Prior evidence contains an invalid technical exception diagnostic")
        for key in ("cleanup_exception", "cleanup_observation_exception"):
            if key in entry and not valid_exception_diagnostic(entry[key]):
                raise ValueError("Prior evidence contains an invalid technical cleanup diagnostic")
        if any(key in entry for key in ("native_parser_failure_code", "native_parser_shape")):
            if adapter != "agy" or not valid_agy_parser_diagnostic(entry):
                raise ValueError("Prior evidence contains an invalid technical native parser diagnostic")
        if "native_denial_diagnostic" in entry and not valid_native_denial_diagnostic(
            entry["native_denial_diagnostic"], adapter
        ):
            raise ValueError("Prior evidence contains an invalid technical native denial diagnostic")
    if receipt.get("submissions_consumed_total") != len(ledger):
        raise ValueError("Prior evidence submission count does not match its ledger")
    return json.loads(json.dumps(ledger)), allowance, digest(previous.read_bytes())


def configuration_gates(proof: Path | None, binding: dict[str, Any], adapter: str) -> list[str]:
    if adapter in {"agy", "codex"}:
        # Documented project configuration is prepared; actual hook inheritance remains a live gate.
        return []
    pending = set(CONFIG_GATES)
    # No researched deterministic task-local permission guard exists yet for these adapters.
    if adapter != "grok":
        return sorted(pending)
    if proof is not None:
        observed = read_json(verification_path(proof))
        if any(observed.get(key) != value for key, value in binding.items()):
            raise ValueError("Configuration evidence does not match this exact fixture and candidate")
        gates = observed.get("prepared_controls")
        if not isinstance(gates, list) or any(gate not in CONFIG_GATES for gate in gates):
            raise ValueError("Prepared controls contain unknown trust or scope gates")
        pending -= set(gates)
    return sorted(pending)


class CandidateRuntime(NativeRuntime):
    """Inject exactly one harness-local policy without changing production admission."""

    def __init__(self, backend: CLIBackend, candidate: AdapterPolicy):
        super().__init__(backend)
        self.candidate = candidate

    def _policy(self, adapter: str) -> AdapterPolicy:
        return self.candidate if adapter == self.candidate.adapter else AdapterPolicy(adapter)


class VerificationPool(ProcessPool):
    """Journal every reserved submission before attempting an OS launch."""

    def __init__(self, evidence: dict[str, Any], path: Path, fixture_root: Path | None = None):
        super().__init__()
        self.evidence, self.path = evidence, path
        self.active_check = "edit"
        self.current: dict[str, Any] | None = None
        self.invocation_count = 0
        self.fixture_root = fixture_root

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        ledger = self.evidence["submission_ledger"]
        if self.invocation_count >= MAX_SUBMISSIONS or len(ledger) >= self.evidence["submission_allowance_total"]:
            raise BackendError("verification_required", "The native verification submission allowance is exhausted.")
        if self.evidence.get("adapter") in {"agy", "codex"} and self.fixture_root is not None:
            names = (
                (
                    "agy-hooks.jsonl",
                    "agy-permission-hook-observed.json",
                    "agy-subagent-observed.json",
                    "agy-hook-log-limit",
                    "mcp-called.json",
                )
                if self.evidence["adapter"] == "agy"
                else ("codex-hooks.jsonl", "codex-stream-observed.json", "mcp-called.json")
            )
            for name in names:
                (self.fixture_root / name).unlink(missing_ok=True)
        self.invocation_count += 1
        entry: dict[str, Any] = {"ordinal": len(ledger) + 1, "check": self.active_check, "spawn_observed": False}
        self.current = entry
        ledger.append(entry)
        self.evidence["submissions_consumed_total"] = len(ledger)
        self.evidence["submissions_in_this_invocation"] = self.invocation_count
        persist(self.path, self.evidence)
        original_started = kwargs.get("on_started")

        def started() -> None:
            entry["spawn_observed"] = True
            persist(self.path, self.evidence)
            if original_started:
                original_started()

        kwargs["on_started"] = started
        try:
            output = await super().execute(*args, **kwargs)
        except asyncio.CancelledError:
            entry["reason"] = "cancelled"
            raise
        except BackendError as exc:
            entry["reason"] = "backend_error"
            entry["error_code"] = safe_identifier(exc.code) or "unknown"
            raise
        except Exception as exc:
            entry["reason"] = "unexpected_exception"
            entry.update(exception_diagnostic(exc))
            raise
        finally:
            persist(self.path, self.evidence)
        entry.update(
            {
                "reason": "exited",
                "exit_status": output.returncode,
                "stdout_bytes": output.stdout_bytes,
                "stderr_bytes": output.stderr_bytes,
            }
        )
        adapter = self.evidence.get("adapter")
        if adapter in {"agy", "codex"}:
            entry["native_denial_diagnostic"] = native_denial_diagnostic(
                adapter,
                output.stdout,
                test_command=fixture_command() if adapter == "codex" else None,
                permission_command=CODEX_PERMISSION_COMMAND if adapter == "codex" else None,
            )
        if self.evidence.get("adapter") == "agy":
            entry.update(agy_parser_diagnostic(output.stdout, output.stderr))
            if self.fixture_root is not None:
                observe_agy_stream(self.fixture_root, output.stdout)
        elif self.evidence.get("adapter") == "codex" and self.fixture_root is not None:
            observe_codex_stream(self.fixture_root, output.stdout)
        persist(self.path, self.evidence)
        return output

    async def _cleanup(self, process: Any) -> None:
        try:
            await super()._cleanup(process)
        except Exception as exc:
            if self.current is not None:
                self.current["cleanup_exception"] = exception_diagnostic(exc)
            raise
        finally:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                absent = True
            except OSError as exc:
                absent = False
                if self.current is not None:
                    self.current["cleanup_observation_exception"] = exception_diagnostic(exc)
            else:
                absent = False
            if self.current is not None:
                self.current["process_group_absent_after_cleanup"] = absent


async def terminal(manager: NativeTaskManager, handle: str, timeout: float) -> dict[str, Any]:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        snapshot = await manager.status(handle)
        if snapshot["state"] not in {"queued", "running"}:
            return snapshot
        await asyncio.sleep(0.05)
    return await manager.cancel(handle)


def fixture_observations(root: Path) -> dict[str, Any]:
    observed = {"fixture_changed": (root / "arithmetic.py").read_text() != INITIAL_ARITHMETIC}
    checks = []
    if (root / "fixture-checks.jsonl").exists():
        raw = (root / "fixture-checks.jsonl").read_bytes()
        if len(raw) <= 65536:
            checks = [json.loads(line) for line in raw.splitlines() if line.strip()]
    observed["native_test_failure_observed"] = any(
        row.get("origin") == "native" and row.get("passed") is False for row in checks
    )
    observed["native_test_success_observed"] = any(
        row.get("origin") == "native" and row.get("passed") is True for row in checks
    )
    observed["scope_hook_observed"] = (root / "hooks.jsonl").is_file() and (root / "hooks.jsonl").stat().st_size > 0
    hook_events = []
    if observed["scope_hook_observed"]:
        raw = (root / "hooks.jsonl").read_bytes()
        if len(raw) <= 65536:
            hook_events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    observed["fixture_subagent_started"] = any(
        row.get("event") == "subagent_start" and row.get("fixture_subagent") is True for row in hook_events
    )
    observed["fixture_subagent_stopped"] = any(
        row.get("event") == "subagent_stop" and row.get("fixture_subagent") is True for row in hook_events
    )
    observed["fixture_subagent_canary_matches"] = any(
        row.get("event") == "post_tool"
        and row.get("fixture_subagent") is True
        and row.get("fixture_read") is True
        and row.get("canary_matches") is True
        for row in hook_events
    )
    observed["mcp_environment_canary_matches"] = mcp_environment_observed(root)
    return observed


def fixture_interpreter_paths() -> set[Path]:
    """Include this process's kernel executable, distinct from macOS's launcher."""
    paths = {Path(sys.executable).resolve()}
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(os.getpid()), "-o", "comm="],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return paths
    observed = result.stdout.strip()
    if result.returncode == 0 and len(observed) <= 4096 and "\n" not in observed:
        candidate = Path(observed)
        if candidate.is_absolute() and candidate.is_file():
            paths.add(candidate.resolve())
    return paths


def fixture_child_identity(root: Path) -> tuple[int, str] | None:
    """Pin the exact synthetic child before cancellation; never trust a bare PID."""
    path = root / "child.pid"
    if not path.is_file():
        return None
    text = path.read_text().strip()
    if not text.isascii() or not text.isdigit() or not 1 < int(text) < 2**31:
        return None
    pid = int(text)
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = result.stdout.strip().split(None, 5)
    if result.returncode != 0 or len(fields) != 6:
        return None
    try:
        command = shlex.split(fields[5])
    except ValueError:
        return None
    if len(command) != 3 or Path(command[0]).resolve() not in fixture_interpreter_paths():
        return None
    if command[1:] != [str(root / "fixture.py"), "child"]:
        return None
    return pid, digest(result.stdout)


def descendant_status(identity: tuple[int, str] | None) -> tuple[bool, bool]:
    """Observe only a previously identified child; never signal mutable fixture PIDs."""
    if identity is None:
        return False, False
    pid, _fingerprint = identity
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True, False
    except PermissionError:
        return False, False
    # A surviving or reused PID is not verified cleanup. Never signal it from fixture metadata.
    return False, False


async def run_checks(
    executable: str,
    adapter: str,
    root: Path,
    candidate: AdapterPolicy,
    checks: tuple[str, ...],
    evidence: dict[str, Any],
    path: Path,
    timeout: float,
    model: str | None = None,
) -> None:
    manifest = root / "client.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": adapter,
                "adapter": adapter,
                "command": executable,
                "working_dir": str(root),
                "allowed_roots": [str(root)],
                "timeout_seconds": math.ceil(timeout),
                "roles": {"default": {"prompt_path": str(root / "native-role.txt")}},
                "native_task": {
                    "enabled": True,
                    "inherit_env": [SENTINEL_NAME],
                    "required_env": [SENTINEL_NAME],
                    "roles": {"default": {"prompt_path": str(root / "native-role.txt")}},
                },
            }
        )
    )

    class FixtureRegistry(ClinkRegistry):
        def _config_sources(self):
            yield manifest, True, False

    prior_default = os.environ.get("CLINK_DEFAULT_CLI")
    os.environ["CLINK_DEFAULT_CLI"] = adapter
    try:
        backend = CLIBackend(FixtureRegistry())
    finally:
        if prior_default is None:
            os.environ.pop("CLINK_DEFAULT_CLI", None)
        else:
            os.environ["CLINK_DEFAULT_CLI"] = prior_default
    backend.pool = VerificationPool(evidence, path, root)
    runtime = CandidateRuntime(backend, candidate)
    manager = NativeTaskManager(runtime)
    prior_sentinel = os.environ.get(SENTINEL_NAME)
    os.environ[SENTINEL_NAME] = SENTINEL_VALUE
    handle = None
    session = None
    configuration_prepared = not evidence["configuration_preparation_gates"]
    try:
        if not fixture_intact(root, evidence):
            evidence["stopped_after"] = "fixture_configuration_changed"
            return
        evidence["fixture_initial_test_failed"] = not fixture_test(root)
        if not evidence["fixture_initial_test_failed"]:
            evidence["stopped_after"] = "initial_fixture_test_did_not_fail"
            return
        for check in checks:
            if not fixture_intact(root, evidence):
                evidence["stopped_after"] = "fixture_configuration_changed"
                break
            backend.pool.active_check = check
            if check == "permission" and not configuration_prepared:
                evidence["gates"].append(
                    "codex_permission_denial_signal_unverified"
                    if adapter == "codex"
                    else "permission_fixture_unprepared"
                )
                continue
            if check == "followup":
                if handle is None or session is None:
                    evidence["stopped_after"] = "native_followup_session_unavailable"
                    break
                submission = await manager.followup(
                    {
                        "task_handle": handle,
                        "request_key": "fixture-followup",
                        "timeout_seconds": timeout,
                        "prompt": "Return only the continuation token from the previous input. Do not use tools.",
                    }
                )
            else:
                test_command = agy_fixture_command(root) if adapter == "agy" else fixture_command()
                prompt = {
                    "edit": f"Run exactly `{test_command}` to observe failure. Fix add in arithmetic.py, then run "
                    f"exactly `{test_command}` again. "
                    + (agy_command_prompt(root) if adapter == "agy" else "")
                    + ("Call the native_fixture MCP canary. " if configuration_prepared and adapter == "grok" else "")
                    + (
                        (
                            agy_capability_prompt(root)
                            if adapter == "agy"
                            else (
                                CODEX_CAPABILITY_PROMPT
                                if adapter == "codex"
                                else "Use spawn_subagent (Task) with subagent_type fixture-specialist and run_in_background false "
                                "to read its canary in the foreground; do not override its model. "
                            )
                        )
                        if configuration_prepared
                        else ""
                    )
                    + f"Remember continuation token {CONTEXT_TOKEN}. Use only the assigned fixture operations. "
                    "Never change configuration or use unrelated files/integrations. Stop on permission denial.",
                    "permission": (
                        CODEX_PERMISSION_PROMPT
                        if adapter == "codex"
                        else "Use the native write tool to create permission-needed.txt containing permission canary. "
                        "This operation requires permission. Stop immediately on denial; do not retry or use alternatives."
                    ),
                    "cancel": f"Run exactly `{test_command} cancel` as a foreground shell operation. "
                    "Do not use other commands/tools or background mode. This is a synthetic cancellation test. "
                    + (agy_command_prompt(root) if adapter == "agy" else ""),
                }[check]
                submission = await manager.start(
                    {
                        "cli_name": adapter,
                        "project_dir": str(root),
                        "request_key": "fixture-" + check,
                        "timeout_seconds": timeout,
                        "prompt": prompt,
                        **({"model": model} if model is not None else {}),
                    }
                )
            current_handle = submission["task_handle"]
            if check == "cancel":
                until = time.monotonic() + timeout
                owned_child = None
                while time.monotonic() < until:
                    if (root / "mutation.txt").exists() and (root / "child-ready.txt").exists():
                        owned_child = fixture_child_identity(root)
                        if owned_child is not None:
                            break
                    if (await manager.status(current_handle))["state"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.05)
                snapshot = await manager.cancel(current_handle)
            else:
                snapshot = await terminal(manager, current_handle, timeout)
            entry = backend.pool.current
            if entry is None:
                evidence["stopped_after"] = "no_process_submission_observed"
                break
            entry.update(snapshot_summary(snapshot))
            if not fixture_intact(root, evidence):
                entry.update({"passed": False, "observations": {"fixture_configuration_intact": False}})
                evidence["stopped_after"] = "fixture_configuration_changed"
                persist(path, evidence)
                break
            observations: dict[str, Any] = {}
            if check == "edit":
                handle = current_handle
                session = manager._records[handle].session_id
                observations = {
                    **fixture_observations(root),
                    **(agy_fixture_observations(root) if adapter == "agy" else {}),
                    **(codex_fixture_observations(root) if adapter == "codex" else {}),
                    "independent_final_test_passed": fixture_test(root),
                }
                core = (
                    "fixture_changed",
                    "native_test_failure_observed",
                    "native_test_success_observed",
                    "independent_final_test_passed",
                )
                passed = (
                    snapshot["state"] == "completed" and session is not None and all(observations[key] for key in core)
                )
                if configuration_prepared:
                    passed = (
                        passed
                        and observations["scope_hook_observed"]
                        and observations["mcp_environment_canary_matches"]
                        and observations["fixture_subagent_started"]
                        and observations["fixture_subagent_stopped"]
                        and (
                            observations["fixture_subagent_read_observed"]
                            if adapter == "agy"
                            else observations["fixture_subagent_canary_matches"]
                        )
                    )
                    if adapter == "agy":
                        passed = (
                            passed
                            and observations["agy_session_start_observed"]
                            and not observations["hook_observation_limit_reached"]
                        )
                        if not observations["fixture_subagent_read_observed"]:
                            evidence["gates"].append("agy_subagent_hook_inheritance_unverified")
                    elif adapter == "codex":
                        passed = passed and observations["codex_session_start_observed"]
                        evidence["gates"].extend(
                            gap for gap in codex_fixture_gaps(root) if gap != "codex_permission_policy_unverified"
                        )
                else:
                    evidence["gates"].append("configuration_canary_unverified")
            elif check == "followup":
                observations = {
                    "context_token_matches": CONTEXT_TOKEN in snapshot.get("result", {}).get("content", ""),
                    "same_native_session": session is not None
                    and manager._records[current_handle].session_id == session,
                }
                passed = snapshot["state"] == "completed" and all(observations.values())
            elif check == "permission":
                observations = {"protected_file_untouched": not (root / "permission-needed.txt").exists()}
                if adapter == "codex":
                    observed = codex_fixture_observations(root)
                    observations = {
                        "permission_policy_declined_observed": observed["permission_policy_declined_observed"]
                    }
                    if not observations["permission_policy_declined_observed"]:
                        evidence["gates"].append("codex_permission_policy_unverified")
                elif adapter == "agy":
                    observed = agy_fixture_observations(root)
                    observations.update(
                        permission_hook_observed=observed["permission_hook_observed"],
                        protected_file_untouched=observed["protected_file_untouched"],
                    )
                passed = snapshot["state"] == "permission_needed" and all(observations.values())
            else:
                absent, emergency = descendant_status(owned_child)
                observations = {
                    "observed_mutation": (root / "mutation.txt").exists(),
                    "synthetic_child_ready_observed": (root / "child-ready.txt").exists(),
                    "synthetic_descendant_absent": absent,
                    "emergency_cleanup_requested": emergency,
                    "descendant_identity_observed": owned_child is not None,
                    "pool_released": backend.pool.running == 0,
                    "project_leases_released": not manager._leases,
                }
                if adapter == "agy":
                    observed = agy_fixture_observations(root)
                    observations["agy_fixture_cancel_command_approval_emitted"] = observed[
                        "agy_fixture_cancel_command_approval_emitted"
                    ]
                passed = (
                    snapshot["state"] == "cancelled" and observations["observed_mutation"] and absent and not emergency
                )
                passed = passed and observations["pool_released"] and observations["project_leases_released"]
                evidence["cleanup_scope"] = (
                    "CLI process group and identified synthetic child; detached or remote work not proven"
                )
            entry.update({"observations": observations, "passed": passed})
            persist(path, evidence)
            if not passed:
                evidence["stopped_after"] = check + "_gate_failed"
                break
    finally:
        await manager.shutdown()
        await backend.close()
        evidence["pool_running_after_shutdown"] = backend.pool.running
        evidence["project_leases_after_shutdown"] = len(manager._leases)
        evidence["fixture_configuration_intact_after_shutdown"] = fixture_intact(root, evidence)
        if not evidence["fixture_configuration_intact_after_shutdown"]:
            evidence["stopped_after"] = "fixture_configuration_changed"
        if prior_sentinel is None:
            os.environ.pop(SENTINEL_NAME, None)
        else:
            os.environ[SENTINEL_NAME] = prior_sentinel
        persist(path, evidence)


def codex_selection_matches(receipt: dict[str, Any], evidence: dict[str, Any], phase: str, model: str | None) -> bool:
    """Gate configured selection without presenting it as native-reported actual execution."""
    settings = receipt.get("configured_" + phase, {})
    settings = settings if isinstance(settings, dict) else {}
    selected = model if model is not None else settings.get("model")
    selection = {
        "configured_model": safe_identifier(settings.get("model")),
        "requested_model": model,
        "selected_model": safe_identifier(selected),
        "model_selection_origin": "explicit_request" if model is not None else "native_configuration",
        "configured_reasoning_effort": safe_identifier(settings.get("model_reasoning_effort")),
    }
    for value, expected, label in (
        (selected, "gpt-6.1-sol", "model"),
        (settings.get("model_reasoning_effort"), "ultra", "reasoning_effort"),
    ):
        known = isinstance(value, str) and value not in {"unavailable", ""}
        matched = known and value == expected
        selection[label + "_known"] = known
        selection[label + "_matches"] = matched
        if not matched:
            gate = "codex_configured_" + label + ("_mismatch" if known else "_unverified")
            if gate not in evidence["gates"]:
                evidence["gates"].append(gate)
    evidence.setdefault(
        "codex_configured_selection",
        {"origin": "native_config_selection_snapshot_not_actual_model"},
    )[phase] = selection
    return selection["model_matches"] and selection["reasoning_effort_matches"]


async def run_codex_trusted_checks(
    executable: str,
    root: Path,
    candidate: AdapterPolicy,
    checks: tuple[str, ...],
    evidence: dict[str, Any],
    path: Path,
    timeout: float,
    binding: Any,
    model: str | None,
) -> None:
    """Use separate bounded control sessions for enrollment and finally cleanup."""
    scope = None
    preparation = None
    cleanup = None
    try:
        preparation = NativeRpc(Path(executable), root, allow_writes=True)
        with preparation as rpc:
            scope = TrustScope(rpc, binding)
            inventory = scope.inventory()
            if inventory.get("managed_requirements_observed") is not True:
                evidence["gates"].append("codex_managed_requirements_unverified")
                evidence["stopped_after"] = "codex_managed_requirements_gate"
                return
            if not codex_selection_matches(inventory, evidence, "before", model):
                evidence["stopped_after"] = "codex_configured_selection_gate"
                return
            enrolled = scope.enroll()
            if enrolled.get("managed_requirements_observed") is not True:
                evidence["gates"].append("codex_managed_requirements_unverified")
                evidence["stopped_after"] = "codex_managed_requirements_gate"
                return
            if not codex_selection_matches(enrolled, evidence, "after", model):
                evidence["stopped_after"] = "codex_configured_selection_gate"
                return
        if preparation.cleanup_complete is not True or preparation.cleanup_failure is not None:
            evidence["stopped_after"] = "codex_trust_control_cleanup_failed"
            return
        evidence["configuration_preparation_gates"] = []
        await run_checks(executable, "codex", root, candidate, checks, evidence, path, timeout, model=model)
    except TrustError as exc:
        evidence["codex_trust_error_code"] = exc.code if exc.code in CODEX_TRUST_ERROR_CODES else "unknown_trust_error"
        evidence["stopped_after"] = "codex_fixture_trust_gate_failed"
    finally:
        try:
            if scope is not None:
                if scope.owned:
                    cleanup = NativeRpc(Path(executable), root, allow_writes=True)
                    with cleanup as rpc:
                        scope.rpc = rpc
                        scope.cleanup()
                else:
                    scope.cleanup()
        except Exception as exc:
            evidence["codex_trust_cleanup_error_code"] = (
                exc.code
                if isinstance(exc, TrustError) and exc.code in CODEX_TRUST_ERROR_CODES
                else "unknown_trust_error"
            )
            evidence["gates"].append("codex_fixture_trust_cleanup_failed")
        finally:
            control_reaped = preparation is not None and preparation.cleanup_complete is True
            if cleanup is not None:
                control_reaped = control_reaped and cleanup.cleanup_complete is True
            evidence["codex_trust_control_groups_reaped"] = control_reaped
            if not control_reaped:
                evidence["gates"].append("codex_trust_control_cleanup_unverified")
            for phase, control in (("preparation", preparation), ("cleanup", cleanup)):
                if control is not None and control.cleanup_failure is not None:
                    failure = control.cleanup_failure
                    evidence["codex_" + phase + "_control_cleanup_failure"] = (
                        failure
                        if isinstance(failure, str) and failure in CODEX_CONTROL_CLEANUP_ERRORS
                        else "unknown_control_cleanup_error"
                    )
                    if "codex_trust_control_cleanup_failed" not in evidence["gates"]:
                        evidence["gates"].append("codex_trust_control_cleanup_failed")
            if scope is not None:
                evidence["codex_fixture_trust"] = dict(scope.receipt)
                confirmed = scope.receipt.get("native_write_confirmed") is True
                uncertain = bool(scope.receipt.get("unconfirmed_write_record_count", 0)) or (
                    scope.receipt.get("native_write_attempted") is True and not confirmed
                )
                evidence["host_configuration_changed"] = True if confirmed else None if uncertain else False
                evidence["host_configuration_change_unconfirmed"] = uncertain
                if scope.receipt.get("native_write_attempted") is True:
                    evidence["host_configuration_change_scope"] = (
                        "approved_codex_fixture_records_and_native_legacy_normalization"
                    )
                complete = (
                    scope.receipt.get("cleanup_complete") is True
                    and not scope.owned
                    and not scope.receipt.get("unconfirmed_write_record_count", 0)
                    and "codex_trust_cleanup_error_code" not in evidence
                )
                evidence["codex_owned_trust_entries_removed"] = complete
                if not complete and "codex_fixture_trust_cleanup_failed" not in evidence["gates"]:
                    evidence["gates"].append("codex_fixture_trust_cleanup_failed")
            persist(path, evidence)


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    if not args.live:
        raise ValueError("No native verification without explicit --live opt-in")
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= MAX_SECONDS:
        raise ValueError("Timeout must be between 1 and 180 seconds")
    checks = tuple(check for check in CHECKS if check in args.checks.split(","))
    if not checks or set(args.checks.split(",")) - set(CHECKS):
        raise ValueError("Unknown native verification checks")
    if "followup" in checks and "edit" not in checks:
        raise ValueError("Follow-up requires an edit submission in the same process; sessions are not persisted")
    if args.model is not None and (args.adapter != "codex" or args.model != "gpt-6.1-sol"):
        raise ValueError("This verifier permits only the explicitly selected Codex execution model gpt-6.1-sol")
    codex_binding = None
    if args.trust_codex_fixture:
        if args.adapter != "codex" or args.fixture_root is None or args.prior_evidence is None or args.prepare_only:
            raise ValueError("Codex trust requires its live exact existing fixture and prior evidence")
        try:
            codex_binding = approved_binding(args.fixture_root.expanduser())
        except TrustError as exc:
            if exc.code == "fixture_root_not_registered":
                raise ValueError("Codex trust is limited to the explicitly approved existing fixture") from None
            raise
        if args.fixture_root.expanduser().resolve(strict=True) != codex_binding.root:
            raise ValueError("Codex trust is limited to the explicitly approved existing fixture")
    path = verification_path(args.evidence)
    if path.exists():
        raise ValueError("Evidence path already exists; preserve genuine prior attempts")
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger, allowance, previous_digest = load_history(args.prior_evidence, args.adapter)
    if not 0 <= args.additional_submissions <= MAX_SUBMISSIONS:
        raise ValueError("A bounded additional allowance must be between zero and four")
    allowance += args.additional_submissions
    if allowance > 64:
        raise ValueError("The investigation lineage reached its bounded submission limit")
    repeated = {entry["check"] for entry in ledger}.intersection(checks)
    if repeated - set(args.replace_check):
        raise ValueError("Previously attempted checks require explicit --replace-check; counters are retained")
    if len(ledger) + len(checks) > allowance and not args.prepare_only:
        raise ValueError("Selected checks exceed the remaining cumulative submission allowance")
    executable = shutil.which(args.adapter)
    if not executable:
        raise ValueError("Selected native CLI executable is unavailable")
    executable = str(Path(executable).resolve(strict=True))
    binding = preflight(executable, args.adapter)
    if args.trust_grok_fixture and args.adapter != "grok":
        raise ValueError("Fixture trust enrollment is implemented only for Grok")
    fixture_parent = PROJECT / "test_output" / "native-adapters"
    fixture_parent.mkdir(parents=True, exist_ok=True)
    root = args.fixture_root.resolve() if args.fixture_root else fixture_parent / (path.stem + ".fixture")
    if fixture_parent.resolve() not in root.resolve().parents:
        raise ValueError("Fixture projects must remain inside test_output/native-adapters")
    if root.exists():
        if args.prior_evidence is None:
            raise ValueError("Reusing a fixture requires its prior technical receipt")
        prior = read_json(verification_path(args.prior_evidence))
        config_files = prior.get("fixture_config_files")
        if not isinstance(config_files, list) or any(
            not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts for name in config_files
        ):
            raise ValueError("Prior fixture file mapping is invalid")
        if prior.get("project_root_sha256") != digest(str(root)):
            raise ValueError("Prior evidence does not bind this fixture project")
    else:
        config_files = create_fixture(root, args.adapter)
    binding.update(
        {"project_root_sha256": digest(str(root)), "fixture_config_sha256": fixture_digest(root, config_files)}
    )
    if args.prior_evidence is not None and root.exists():
        prior = read_json(verification_path(args.prior_evidence))
        if (
            prior.get("project_root_sha256") == binding["project_root_sha256"]
            and prior.get("fixture_config_sha256") != binding["fixture_config_sha256"]
        ):
            raise ValueError("The existing fixture configuration changed since its pinned receipt")
    preflight_path = path.with_name(path.stem + ".preflight.json")
    persist(preflight_path, {"scope": "native_adapter_candidate_preflight", **binding}, exclusive=True)
    preflight_path.chmod(0o444)
    pending = configuration_gates(args.prepared_controls, binding, args.adapter)
    if not native_arguments(args.adapter):
        pending.append("native_runtime_recipe_unavailable")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "scope": SCOPE,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **binding,
        "candidate_admission_override": "harness_only_no_production_policy_changes",
        "candidate_preflight_sha256": digest(preflight_path.read_bytes()),
        "prior_evidence_sha256": previous_digest,
        "fixture_config_files": config_files,
        "maximum_submissions_per_invocation": MAX_SUBMISSIONS,
        "submission_allowance_total": allowance,
        "submissions_consumed_total": len(ledger),
        "submissions_in_this_invocation": 0,
        "submission_ledger": ledger,
        "selected_checks": list(checks),
        "explicit_replacement_checks": args.replace_check,
        "additional_submission_allowance": args.additional_submissions,
        "automatic_retries": 0,
        "gates": [],
        "configuration_preparation_gates": pending,
        "native_verified": False,
        "prepared_controls": ["native_policy_preserved", "synthetic_scope_prepared"],
        "raw_output_retained": False,
        "prompts_recorded": False,
        "argv_recorded": False,
        "native_session_ids_recorded": False,
        "environment_values_recorded": False,
        "native_home_and_auth_preserved": True,
        "explicit_request_model": args.model,
        "host_configuration_changed": False,
        "internal_native_subagent_submissions": "not_counted_by_top_level_process_bound",
    }
    persist(path, evidence, exclusive=True)
    if "native_runtime_recipe_unavailable" in pending or args.prepare_only:
        evidence["stopped_after"] = (
            "native_runtime_recipe_unavailable"
            if "native_runtime_recipe_unavailable" in pending
            else "explicit_prepare_only"
        )
        persist(path, evidence)
        return evidence
    candidate = AdapterPolicy(
        args.adapter,
        version=binding["observed_version"],
        verified=True,
        verification_status="candidate_verification_only",
        executable_sha256=binding["executable_sha256"],
        recipe_sha256=binding["native_recipe_sha256"],
        evidence=str(preflight_path.relative_to(PROJECT)),
        evidence_sha256=digest(preflight_path.read_bytes()),
    )
    try:
        if args.trust_grok_fixture:
            native_home = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok"))).expanduser().resolve()
            trust_receipt: dict[str, bool] = {}
            try:
                with grok_fixture_trust(root, native_home) as trust_receipt:
                    evidence["configuration_preparation_gates"] = []
                    evidence["host_configuration_changed"] = True
                    evidence["host_configuration_change_scope"] = "exact_synthetic_fixture_trust_entry_only"
                    await run_checks(executable, args.adapter, root, candidate, checks, evidence, path, args.timeout)
            finally:
                evidence["fixture_trust"] = trust_receipt
                evidence["host_configuration_restored"] = trust_receipt.get("fixture_trust_removed") is True
                persist(path, evidence)
        elif args.trust_codex_fixture:
            await run_codex_trusted_checks(
                executable, root, candidate, checks, evidence, path, args.timeout, codex_binding, args.model
            )
        else:
            await run_checks(
                executable, args.adapter, root, candidate, checks, evidence, path, args.timeout, model=args.model
            )
    except (BackendError, OSError, ValueError, RuntimeError) as exc:
        evidence["stopped_after"] = "verification_exception"
        evidence["exception_type"] = type(exc).__name__
        if isinstance(exc, BackendError):
            evidence["error_code"] = safe_identifier(exc.code) or "unknown"
    current = (
        evidence["submission_ledger"][-evidence["submissions_in_this_invocation"] :]
        if evidence["submissions_in_this_invocation"]
        else []
    )
    evidence["native_verified"] = (
        checks == CHECKS
        and len(current) == len(CHECKS)
        and all(entry.get("passed") is True for entry in current)
        and all(entry.get("process_group_absent_after_cleanup") is True for entry in current)
        and not evidence["gates"]
        and "stopped_after" not in evidence
    )
    persist(path, evidence)
    return evidence


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt in to the selected bounded native submissions.")
    parser.add_argument("--adapter", choices=ADAPTERS, required=True)
    parser.add_argument("--evidence", type=Path, required=True, help="Unique durable JSON path in docs/verification.")
    parser.add_argument("--timeout", type=float, default=MAX_SECONDS)
    parser.add_argument("--model", help="Explicit Codex-only execution model; currently restricted to gpt-6.1-sol.")
    parser.add_argument("--checks", default=",".join(CHECKS))
    parser.add_argument(
        "--prepare-only", action="store_true", help="Collect version and fixture preflight without model work."
    )
    parser.add_argument("--fixture-root", type=Path, help="Reuse only a prior receipt's independent synthetic project.")
    parser.add_argument(
        "--prior-evidence", type=Path, help="Continue a receipt lineage without resetting spent submissions."
    )
    parser.add_argument(
        "--prepared-controls",
        type=Path,
        help="Pinned exact fixture trust enrollment and configured scope controls; not live verification.",
    )
    parser.add_argument(
        "--trust-grok-fixture",
        action="store_true",
        help="Opt in to exact synthetic Grok trust enrollment and precise cleanup.",
    )
    parser.add_argument(
        "--trust-codex-fixture",
        action="store_true",
        help="Opt in to the explicitly approved existing Codex fixture trust records and precise cleanup.",
    )
    parser.add_argument("--replace-check", choices=CHECKS, action="append", default=[])
    parser.add_argument(
        "--additional-submissions",
        type=int,
        default=0,
        help="Explicit bounded repair allowance; retains all prior consumption.",
    )
    return parser


def main() -> None:
    parser = argument_parser()
    args = parser.parse_args()
    try:
        receipt = asyncio.run(verify(args))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.error("Verification stopped before submission: " + type(exc).__name__)
    print(
        json.dumps(
            {
                "evidence": str(args.evidence.resolve()),
                "native_verified": receipt["native_verified"],
                "submissions_in_this_invocation": receipt["submissions_in_this_invocation"],
                "gates": receipt["gates"],
            }
        )
    )


if __name__ == "__main__":
    main()
