"""Collect opt-in, bounded native Claude lifecycle evidence without retaining sessions.

This deliberately preserves the real HOME and ordinary Claude configuration.
Only the generated project's synthetic task receives additional scope guards.
It neither reads credentials nor changes host configuration. Raw CLI output and
session identifiers remain in memory; durable evidence contains technical fields.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_VERSION = "2.1.304 (Claude Code)"
MAX_CAPTURE = 2 * 1024 * 1024
MAX_SECONDS = 180
NATIVE_ARGUMENTS = ("--print", "--output-format", "stream-json", "--verbose", "--permission-prompts", "none")
SENTINEL_NAME = "NATIVE_FIXTURE_SENTINEL"
SENTINEL_VALUE = "synthetic-native-environment-v1"
CONTEXT_TOKEN = "NATIVE_CONTEXT_648237"
ENVIRONMENT_NAMES = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
)

FIXTURE_PROGRAM = '''"""Synthetic test and cancellation target; no external operations."""
import pathlib
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent

if len(sys.argv) > 1 and sys.argv[1] == "cancel":
    child = subprocess.Popen([sys.executable, __file__, "child"])
    (ROOT / "child.pid").write_text(str(child.pid))
    (ROOT / "mutation.txt").write_text("known partial effect\\n")
    child.wait()
elif len(sys.argv) > 1 and sys.argv[1] == "child":
    time.sleep(160)
else:
    from arithmetic import add

    class ArithmeticTest(unittest.TestCase):
        def test_addition(self):
            self.assertEqual(add(2, 3), 5)

    unittest.main()
'''

SCOPE_HOOK = '''"""Record metadata and constrain only the synthetic verification task."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TEST_COMMAND = @TEST_COMMAND@
event = json.load(sys.stdin)
name = event.get("hook_event_name", "unknown")
tool = event.get("tool_name", "")
data = event.get("tool_input") or {}
allowed = True
if name == "PreToolUse":
    if tool in {"Read", "Edit", "Write"}:
        path = pathlib.Path(data.get("file_path", ""))
        if not path.is_absolute():
            path = ROOT / path
        allowed = path.resolve() in {ROOT / f for f in (
            "arithmetic.py", "fixture.py", "specialist.txt", "permission-needed.txt"
        )}
        if tool in {"Edit", "Write"}:
            allowed = allowed and path.resolve().name in {"arithmetic.py", "permission-needed.txt"}
    elif tool == "Bash":
        allowed = data.get("command") in {
            TEST_COMMAND, TEST_COMMAND + " cancel",
            "python fixture.py", "python fixture.py cancel",
            "python3 fixture.py", "python3 fixture.py cancel",
        } and not data.get("run_in_background", False)
    elif tool in {"Agent", "Task"}:
        allowed = data.get("subagent_type") == "fixture-specialist" and not data.get("run_in_background", False)
    elif tool == "ToolSearch":
        query = data.get("query", "")
        allowed = isinstance(query, str) and ("native_fixture" in query or "canary" in query)
    elif tool == "mcp__native_fixture__canary":
        allowed = True
    else:
        allowed = False
category = tool if tool in {"Read", "Edit", "Write", "Bash", "Agent", "Task", "ToolSearch"} else (
    "fixture_mcp" if tool == "mcp__native_fixture__canary" else "other"
)
with (ROOT / "hooks.jsonl").open("a") as output:
    output.write(json.dumps({"event": name, "tool": category, "allowed": allowed}) + "\\n")
if name == "PreToolUse":
    decision = "allow" if allowed else "deny"
    if allowed and tool in {"Write", "Edit"} and pathlib.Path(data.get("file_path", "")).name == "permission-needed.txt":
        decision = "ask"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": "Scoped native permission for this synthetic verification task."}}))
'''

MCP_SERVER = '''"""Minimal synthetic stdio MCP server with inherited-environment canary."""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}}, "serverInfo": {"name": "native-fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "canary", "description": "Verify synthetic inherited environment.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
    elif method == "tools/call" and request.get("params", {}).get("name") == "canary":
        matched = os.environ.get("NATIVE_FIXTURE_SENTINEL") == "synthetic-native-environment-v1"
        (ROOT / "mcp-called.json").write_text(json.dumps({"inherited_sentinel_matches": matched}))
        result = {"content": [{"type": "text", "text": "MCP_CANARY_OK" if matched else "MCP_CANARY_FAILED"}],
            "isError": not matched}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
'''


def sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def create_fixture(root: Path) -> None:
    """Create only synthetic configuration, permissions, tools and project content."""
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / "arithmetic.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "fixture.py").write_text(FIXTURE_PROGRAM)
    (root / "scope_hook.py").write_text(SCOPE_HOOK.replace("@TEST_COMMAND@", repr(fixture_command())))
    (root / "mcp_server.py").write_text(MCP_SERVER)
    (root / "specialist.txt").write_text("SPECIALIST_CANARY_9361\n")
    (root / ".claude" / "agents" / "fixture-specialist.md").write_text(
        "---\nname: fixture-specialist\ndescription: Read the synthetic specialist canary when asked.\n"
        "tools: Read\nmodel: inherit\n---\nRead specialist.txt and return its exact canary. Do not edit files.\n"
    )
    hook = {"type": "command", "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(root / 'scope_hook.py'))}"}
    settings = {
        "permissions": {
            "allow": [
                "Read(./arithmetic.py)",
                "Read(./fixture.py)",
                "Read(./specialist.txt)",
                "Edit(./arithmetic.py)",
                "Write(./arithmetic.py)",
                "Bash(python fixture.py)",
                "Bash(python fixture.py cancel)",
                "Bash(python3 fixture.py)",
                "Bash(python3 fixture.py cancel)",
                "Agent(fixture-specialist)",
                "Task(fixture-specialist)",
                "mcp__native_fixture__canary",
            ],
            "ask": ["Write(./permission-needed.txt)", "Edit(./permission-needed.txt)"],
        },
        "hooks": {
            name: [{"matcher": "*", "hooks": [hook]}]
            for name in ("SessionStart", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop")
        },
    }
    (root / ".claude" / "settings.json").write_text(json.dumps(settings))
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "native_fixture": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(root / "mcp_server.py")],
                        "env": {SENTINEL_NAME: "${" + SENTINEL_NAME + "}"},
                    }
                }
            }
        )
    )
    (root / "CLAUDE.md").write_text(
        "This is an authorized disposable synthetic verification project. Work only on the explicitly requested "
        "fixture operations. Never access host files, credentials or unrelated integrations. "
        f"Use `{fixture_command()}` for tests, and the fixture-specialist agent when explicitly asked. "
        "The project hook enforces this scope. Do not change configuration. Stop after any permission denial.\n"
    )


def fixture_command() -> str:
    return f"{shlex.quote(sys.executable)} fixture.py"


def environment(source: dict[str, str]) -> dict[str, str]:
    """Preserve normal auth locations without examining credential values."""
    result = {name: source[name] for name in ENVIRONMENT_NAMES if name in source}
    result[SENTINEL_NAME] = SENTINEL_VALUE
    result["CLINK_DELEGATION_DEPTH"] = "1"
    return result


def stop_group(process: subprocess.Popen[bytes]) -> bool:
    # Reap a completed leader before probing its group (macOS can otherwise
    # report EPERM for an already exited, unreaped sandbox child).
    process.poll()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            continue
    process.wait(timeout=3)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return True
    return False


def run_once(
    argv: list[str],
    root: Path,
    env: dict[str, str],
    prompt: str,
    *,
    timeout: float = MAX_SECONDS,
    cancel_marker: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Launch exactly once; cancel after a known side effect, never retry."""
    if timeout <= 0 or timeout > MAX_SECONDS:
        raise ValueError("Native verification deadline must be between 0 and 180 seconds")
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    reason = "exited"
    try:
        assert process.stdin is not None
        process.stdin.write(prompt.encode())
        process.stdin.close()
        for name in streams:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        while selector.get_map():
            if cancel_marker is not None and cancel_marker.exists():
                reason = "cancelled_after_mutation"
                break
            if time.monotonic() - started >= timeout:
                reason = "deadline"
                break
            for key, _ in selector.select(0.05):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.data].extend(chunk)
                if sum(map(len, streams.values())) > MAX_CAPTURE:
                    reason = "output_limit"
                    break
            if reason == "output_limit":
                break
    finally:
        cleaned = stop_group(process)
        selector.close()
        for name in streams:
            getattr(process, name).close()
    events = []
    malformed = 0
    for line in streams["stdout"].splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            malformed += 1
            continue
        if isinstance(value, dict):
            events.append(value)
    summary = summarize(events)
    summary.update(
        {
            "launch_count": 1,
            "automatic_retries": 0,
            "reason": reason,
            "returncode": process.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_bytes": len(streams["stdout"]),
            "stderr_bytes": len(streams["stderr"]),
            "malformed_line_count": malformed,
            "process_group_absent_after_cleanup": cleaned,
            "stderr_sha256": sha256(streams["stderr"]),
            "raw_output_retained": False,
        }
    )
    return summary, events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Export only bounded technical metadata; never tool input or session text."""
    counts: collections.Counter[str] = collections.Counter()
    tool_counts: collections.Counter[str] = collections.Counter()
    sessions = set()
    models = set()
    terminal = None
    known_test_failure = 0
    known_test_success = 0
    known_missing_command = 0
    for event in events:
        kind = event.get("type")
        subtype = event.get("subtype")
        if isinstance(kind, str) and re.fullmatch(r"[a-z_]{1,64}", kind):
            label = kind
            if isinstance(subtype, str) and re.fullmatch(r"[a-z_]{1,64}", subtype):
                label += ":" + subtype
            counts[label] += 1
        sid = event.get("session_id")
        if isinstance(sid, str):
            sessions.add(sha256(sid))
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model")
            if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", model):
                models.add(model)
        message = event.get("message")
        for block in (message.get("content", []) if isinstance(message, dict) else []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name")
                category = (
                    name
                    if name in {"Read", "Edit", "Write", "Bash", "Agent", "Task", "ToolSearch"}
                    else ("fixture_mcp" if name == "mcp__native_fixture__canary" else "other")
                )
                tool_counts[category] += 1
            elif block.get("type") == "tool_result":
                content = str(block.get("content", ""))
                known_test_failure += "FAILED (failures=1)" in content and "Ran 1 test" in content
                known_test_success += "Ran 1 test" in content and bool(re.search(r"\bOK\b", content))
                known_missing_command += "command not found: python" in content
        if event.get("type") == "result":
            terminal = event
    result: dict[str, Any] = {
        "event_counts": dict(counts),
        "tool_use_counts": dict(tool_counts),
        "session_id_sha256": sorted(sessions),
        "actual_models": sorted(models),
        "terminal_result_count": counts["result:success"]
        + sum(n for key, n in counts.items() if key.startswith("result:") and key != "result:success"),
    }
    if terminal is not None:
        result["terminal"] = {
            key: terminal[key]
            for key in ("subtype", "is_error", "num_turns")
            if type(terminal.get(key)) in {str, int, bool}
        }
        result["terminal"]["keys"] = sorted(
            set(terminal)
            & {
                "type",
                "subtype",
                "is_error",
                "duration_ms",
                "duration_api_ms",
                "num_turns",
                "result",
                "session_id",
                "total_cost_usd",
                "usage",
                "modelUsage",
                "permission_denials",
                "errors",
                "uuid",
            }
        )
        denials = terminal.get("permission_denials")
        result["terminal"]["permission_denial_count"] = len(denials) if isinstance(denials, list) else 0
        result["terminal"]["permission_denial_shapes"] = (
            [sorted(d) for d in denials[:8] if isinstance(d, dict)] if isinstance(denials, list) else []
        )
    result["known_fixture_test_failure_count"] = known_test_failure
    result["known_fixture_test_success_count"] = known_test_success
    result["known_missing_python_command_count"] = known_missing_command
    return result


def successful(summary: dict[str, Any]) -> bool:
    terminal = summary.get("terminal", {})
    return (
        summary.get("reason") == "exited"
        and summary.get("returncode") == 0
        and (terminal.get("subtype") == "success" and terminal.get("is_error") is False)
        and terminal.get("permission_denial_count", 0) == 0
    )


def hook_summary(root: Path) -> dict[str, Any]:
    events = [json.loads(line) for line in (root / "hooks.jsonl").read_text().splitlines()]
    counts = collections.Counter(event["event"] for event in events)
    denied = sum(event["allowed"] is False for event in events)
    return {"event_counts": dict(counts), "scope_denial_count": denied}


def verify(executable: str, *, timeout: float = MAX_SECONDS) -> dict[str, Any]:
    env = environment(dict(os.environ))
    version = subprocess.run([executable, "--version"], env=env, capture_output=True, timeout=15, check=False)
    observed = version.stdout.decode().strip()
    if observed != EXPECTED_VERSION:
        raise ValueError("Installed Claude version differs from the version-specific verification target")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "scope": "standalone_native_cli_synthetic_project",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "observed_version": observed,
        "executable_sha256": sha256(Path(executable).read_bytes()),
        "native_arguments": list(NATIVE_ARGUMENTS),
        "environment_names": sorted(env),
        "environment_values_recorded": False,
        "native_auth_location_preserved": True,
        "auth_material_read_or_copied": False,
        "host_configuration_changed": False,
        "maximum_model_invocations": 4,
        "model_invocations_submitted": 0,
        "attempts": [],
    }
    with tempfile.TemporaryDirectory(prefix="clink-native-fixture-") as directory:
        root = Path(directory).resolve()
        create_fixture(root)
        before = subprocess.run([sys.executable, "fixture.py"], cwd=root, capture_output=True, check=False)
        evidence["fixture_initial_test_failed"] = before.returncode != 0
        prompt = (
            f"Perform this synthetic task exactly: run `{fixture_command()}` to observe the failing test; "
            f"read arithmetic.py and fix its add function with Edit; run `{fixture_command()}` again. "
            "Then invoke fixture-specialist to read its canary. Call the native_fixture MCP canary tool "
            "(discover it with ToolSearch if needed). Stop after any permission denial and do not try alternatives. "
            "Do not touch any other files or integrations. Remember the continuation token "
            + CONTEXT_TOKEN
            + " for a later message. Report the test and both canaries briefly."
        )
        evidence["model_invocations_submitted"] += 1
        first, events = run_once([executable, *NATIVE_ARGUMENTS], root, env, prompt, timeout=timeout)
        first["kind"] = "edit_test_hook_subagent_mcp"
        evidence["attempts"].append(first)
        after = subprocess.run([sys.executable, "fixture.py"], cwd=root, capture_output=True, check=False)
        evidence["fixture_final_test_passed"] = after.returncode == 0
        evidence["fixture_changed"] = (root / "arithmetic.py").read_text() != "def add(a, b):\n    return a - b\n"
        evidence["hooks"] = hook_summary(root) if (root / "hooks.jsonl").exists() else {"event_counts": {}}
        evidence["synthetic_mcp"] = (
            json.loads((root / "mcp-called.json").read_text())
            if (root / "mcp-called.json").exists()
            else {"inherited_sentinel_matches": False}
        )
        session = next((e.get("session_id") for e in reversed(events) if e.get("type") == "result"), None)
        if not successful(first) or not isinstance(session, str):
            evidence["stopped_after"] = "initial_native_attempt_not_successful"
            return evidence
        evidence["model_invocations_submitted"] += 1
        followup, resumed = run_once(
            [executable, *NATIVE_ARGUMENTS, "--resume", session],
            root,
            env,
            "Return only the continuation token I asked you to remember previously. Do not use tools.",
            timeout=timeout,
        )
        followup["kind"] = "native_followup"
        followup["context_token_matches"] = any(
            e.get("type") == "result" and CONTEXT_TOKEN in e.get("result", "") for e in resumed
        )
        followup["same_native_session"] = followup["session_id_sha256"] == first["session_id_sha256"]
        evidence["attempts"].append(followup)
        if not successful(followup):
            evidence["stopped_after"] = "followup_not_successful"
            return evidence
        evidence["model_invocations_submitted"] += 1
        denial, _ = run_once(
            [executable, *NATIVE_ARGUMENTS],
            root,
            env,
            "Use Write to create permission-needed.txt with the text permission canary. "
            "This action needs native permission. If denied, stop immediately and report permission needed; "
            "do not retry or use another tool.",
            timeout=timeout,
        )
        denial.update(
            {"kind": "permission_needed", "protected_file_absent": not (root / "permission-needed.txt").exists()}
        )
        evidence["attempts"].append(denial)
        evidence["model_invocations_submitted"] += 1
        cancelled, _ = run_once(
            [executable, *NATIVE_ARGUMENTS],
            root,
            env,
            f"Run exactly `{fixture_command()} cancel` using Bash without background mode. "
            "This is a synthetic cancellation test. Do not run any other tools or commands.",
            timeout=timeout,
            cancel_marker=root / "mutation.txt",
        )
        cancelled.update({"kind": "cancel_after_mutation", "partial_effect_present": (root / "mutation.txt").exists()})
        evidence["attempts"].append(cancelled)
        pid_path = root / "child.pid"
        if pid_path.exists():
            pid = int(pid_path.read_text())
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                cancelled["synthetic_descendant_alive_after_cleanup"] = False
            else:
                cancelled["synthetic_descendant_alive_after_cleanup"] = True
                os.kill(pid, signal.SIGKILL)
                cancelled["synthetic_descendant_cleanup_requested"] = True
        explicit_error, _ = run_once(
            [executable, *NATIVE_ARGUMENTS, "--clink-invalid-verification-flag"], root, env, "", timeout=15
        )
        explicit_error["kind"] = "explicit_cli_error_no_retry"
        evidence["attempts"].append(explicit_error)
        evidence["model_invocations_submitted"] = 4
    return evidence


def candidate_preflight(executable: str, previous: Path) -> tuple[str, str, str]:
    """Require observed version and prior exact binary before candidate admission."""
    version = subprocess.run(
        [executable, "--version"],
        env=environment(dict(os.environ)),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if version.returncode != 0 or version.stdout.decode("utf-8").strip() != EXPECTED_VERSION:
        raise ValueError("Installed Claude version differs from the version-specific verification target")
    observed = version.stdout.decode("utf-8").strip()
    previous_bytes = previous.read_bytes()
    receipt = json.loads(previous_bytes)
    if not isinstance(receipt, dict) or receipt.get("observed_version") != observed:
        raise ValueError("Prior native evidence does not match the observed Claude version")
    executable_sha256 = sha256(Path(executable).read_bytes())
    if receipt.get("executable_sha256") != executable_sha256:
        raise ValueError("Installed Claude binary differs from the prior native evidence")
    return observed, executable_sha256, sha256(previous_bytes)


async def verify_candidate_runtime(
    executable: str, evidence: dict[str, Any], timeout: int, *, repair_tests_cancel: bool = False
) -> None:
    """Exercise actual registry/runtime/pool/manager with a harness-only candidate policy.

    The override admits only this verification harness using the pinned prior
    observation. It never modifies the global production admission mapping.
    """
    project = Path(__file__).resolve().parents[1]
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    from clink.backend import CLIBackend
    from clink.contracts import AdapterPolicy
    from clink.native import NativeRuntime
    from clink.native_policy import native_recipe_digest
    from clink.registry import ClinkRegistry
    from clink.runner import ProcessPool
    from clink.tasks import NativeTaskManager

    previous = project / (
        "docs/verification/native-claude-2.1.304-attempt2.json"
        if repair_tests_cancel
        else "docs/verification/native-claude-2.1.304-attempt1.json"
    )
    evidence.setdefault("model_invocations_submitted", 0)
    observed, executable_sha256, previous_sha256 = candidate_preflight(executable, previous)
    evidence.update(
        {
            "observed_version": observed,
            "executable_sha256": executable_sha256,
            "candidate_preflight": {
                "actual_version_matches": True,
                "prior_version_matches": True,
                "prior_binary_matches": True,
            },
        }
    )
    candidate = AdapterPolicy(
        "claude",
        version=observed,
        verified=True,
        verification_status="candidate_verification_only",
        executable_sha256=executable_sha256,
        recipe_sha256=native_recipe_digest(),
        evidence=str(previous.relative_to(project)),
        evidence_sha256=previous_sha256,
    )

    class CandidateRuntime(NativeRuntime):
        @staticmethod
        def _policy(adapter: str) -> AdapterPolicy:
            return candidate if adapter == "claude" else AdapterPolicy(adapter)

    class RecordingPool(ProcessPool):
        async def _cleanup(self, process: Any) -> None:
            await super()._cleanup(process)
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                absent = True
            else:
                absent = False
            evidence.setdefault("cleanup_process_group_absent", []).append(absent)

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            evidence["model_invocations_submitted"] += 1
            if evidence["model_invocations_submitted"] > (2 if repair_tests_cancel else 4):
                raise RuntimeError("Candidate verification model invocation ceiling reached")
            entry = {"kind": evidence["active_check"], "launch_count": 1, "automatic_retries": 0}
            evidence["attempts"].append(entry)
            try:
                output = await super().execute(*args, **kwargs)
            except asyncio.CancelledError:
                entry.update({"reason": "cancelled", "raw_output_retained": False})
                raise
            else:
                rows = [json.loads(line) for line in output.stdout.splitlines() if line.strip()]
                entry.update(summarize(rows))
                entry.update(
                    {
                        "reason": "exited",
                        "returncode": output.returncode,
                        "stdout_bytes": output.stdout_bytes,
                        "stderr_bytes": output.stderr_bytes,
                        "raw_output_retained": False,
                    }
                )
                return output

    async def terminal(manager: NativeTaskManager, handle: str) -> dict[str, Any]:
        until = time.monotonic() + timeout + 10
        while time.monotonic() < until:
            snapshot = await manager.status(handle)
            if snapshot["state"] not in {"queued", "running"}:
                return snapshot
            await asyncio.sleep(0.05)
        await manager.cancel(handle)
        raise TimeoutError("Candidate verification status deadline exceeded")

    def record(snapshot: dict[str, Any]) -> None:
        entry = evidence["attempts"][-1]
        entry.update(
            {
                key: snapshot[key]
                for key in (
                    "state",
                    "execution_outcome",
                    "possible_partial_effects",
                    "cancellation_requested",
                    "result_unavailable",
                    "followup_available",
                )
                if key in snapshot
            }
        )
        if "error" in snapshot:
            entry["error_code"] = snapshot["error"]["code"]
        metadata = snapshot.get("result", {}).get("metadata", {})
        entry["runtime_metadata"] = {
            key: metadata[key]
            for key in (
                "actual_model",
                "exit_status",
                "permission_denial_count",
                "tool_error_count",
                "configuration_sources",
                "environment_names",
                "cleanup_scope",
            )
            if key in metadata
        }

    async def cancel_after_mutation(manager: NativeTaskManager, root: Path) -> None:
        evidence["active_check"] = "production_cancel_after_mutation"
        cancel = await manager.start(
            {
                "cli_name": "claude",
                "project_dir": str(root),
                "request_key": "native-fixture-cancel",
                "timeout_seconds": timeout,
                "prompt": f"Run exactly `{fixture_command()} cancel` with Bash without background mode. "
                "This is the synthetic cancellation test. Do not run any other tools or commands.",
            }
        )
        until = time.monotonic() + timeout
        while not (root / "mutation.txt").exists() and time.monotonic() < until:
            if (await manager.status(cancel["task_handle"]))["state"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.05)
        cancelled = await manager.cancel(cancel["task_handle"])
        record(cancelled)
        evidence["attempts"][-1]["partial_effect_present"] = (root / "mutation.txt").exists()
        pid_path = root / "child.pid"
        if pid_path.exists():
            pid = int(pid_path.read_text())
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                evidence["attempts"][-1]["synthetic_descendant_alive_after_cleanup"] = False
            else:
                evidence["attempts"][-1]["synthetic_descendant_alive_after_cleanup"] = True
                os.kill(pid, signal.SIGKILL)
                evidence["attempts"][-1]["synthetic_descendant_cleanup_requested"] = True
        evidence["pool_running_after_cancel"] = manager.runtime.pool.running
        evidence["project_leases_after_cancel"] = len(manager._leases)

    prior_env = {name: os.environ.get(name) for name in (SENTINEL_NAME, "CLINK_DEFAULT_CLI")}
    os.environ[SENTINEL_NAME] = SENTINEL_VALUE
    os.environ["CLINK_DEFAULT_CLI"] = "claude"
    evidence.update(
        {
            "scope": "candidate_production_registry_runtime_pool_task_manager",
            "candidate_admission_override": "harness_only_no_global_policy_changes",
            "candidate_prior_evidence_sha256": candidate.evidence_sha256,
            "native_recipe_sha256": candidate.recipe_sha256,
            "model_invocations_submitted": 0,
            "attempts": [],
        }
    )
    try:
        with tempfile.TemporaryDirectory(prefix="clink-native-fixture-") as directory:
            root = Path(directory).resolve()
            create_fixture(root)
            role = root / "native-role.txt"
            role.write_text(
                "Execute the assigned synthetic task with native tools and permissions. Report observed outcomes."
            )
            manifest = root / "client.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "claude",
                        "adapter": "claude",
                        "command": executable,
                        "working_dir": str(root),
                        "allowed_roots": [str(root)],
                        "timeout_seconds": timeout,
                        "roles": {"default": {"prompt_path": str(role)}},
                        "native_task": {
                            "enabled": True,
                            "inherit_env": [SENTINEL_NAME],
                            "required_env": [SENTINEL_NAME],
                            "roles": {"default": {"prompt_path": str(role)}},
                        },
                    }
                )
            )

            class FixtureRegistry(ClinkRegistry):
                def _config_sources(self) -> Any:
                    yield manifest, True, False

            backend = CLIBackend(FixtureRegistry())
            backend.pool = RecordingPool()
            runtime = CandidateRuntime(backend)
            manager = NativeTaskManager(runtime)
            try:
                probe_context = runtime.prepare("claude", None, None, str(root))
                shell_probe = subprocess.run(
                    ["/bin/zsh", "-c", fixture_command()],
                    cwd=root,
                    env=runtime._environment(probe_context),
                    capture_output=True,
                    check=False,
                )
                evidence["offline_exact_shell_fixture"] = {
                    "exit_status": shell_probe.returncode,
                    "expected_test_failure_observed": b"FAILED (failures=1)" in shell_probe.stderr,
                    "command_not_found": b"command not found" in shell_probe.stderr,
                }
                if not evidence["offline_exact_shell_fixture"]["expected_test_failure_observed"]:
                    evidence["stopped_after"] = "offline_exact_shell_fixture_failed"
                    return
                before = subprocess.run([sys.executable, "fixture.py"], cwd=root, capture_output=True, check=False)
                evidence["fixture_initial_test_failed"] = before.returncode != 0
                evidence["active_check"] = (
                    "production_edit_test_repair" if repair_tests_cancel else "production_edit_test_hook_subagent_mcp"
                )
                start = await manager.start(
                    {
                        "cli_name": "claude",
                        "project_dir": str(root),
                        "request_key": "native-fixture-start",
                        "timeout_seconds": timeout,
                        "prompt": f"Run exactly `{fixture_command()}` to observe the failing test. Read arithmetic.py and fix add "
                        f"using Edit. Run exactly `{fixture_command()}` again. "
                        + (
                            ""
                            if repair_tests_cancel
                            else "Then invoke fixture-specialist as a foreground "
                            "Agent to read its canary. Discover and call native_fixture MCP canary. "
                        )
                        + "Never access unrelated files/integrations or change configuration. Stop after permission denial. "
                        "Remember continuation token " + CONTEXT_TOKEN + ". Report test and both canaries briefly.",
                    }
                )
                handle = start["task_handle"]
                first = await terminal(manager, handle)
                record(first)
                after = subprocess.run([sys.executable, "fixture.py"], cwd=root, capture_output=True, check=False)
                evidence["fixture_final_test_passed"] = after.returncode == 0
                evidence["fixture_changed"] = (
                    root / "arithmetic.py"
                ).read_text() != "def add(a, b):\n    return a - b\n"
                evidence["hooks"] = hook_summary(root) if (root / "hooks.jsonl").exists() else {"event_counts": {}}
                evidence["synthetic_mcp"] = (
                    json.loads((root / "mcp-called.json").read_text())
                    if (root / "mcp-called.json").exists()
                    else {"inherited_sentinel_matches": False}
                )
                if first["state"] != "completed":
                    evidence["stopped_after"] = "production_initial_not_completed"
                    return
                if repair_tests_cancel:
                    await cancel_after_mutation(manager, root)
                    return
                evidence["active_check"] = "production_native_followup"
                await manager.followup(
                    {
                        "task_handle": handle,
                        "request_key": "native-fixture-followup",
                        "timeout_seconds": timeout,
                        "prompt": "Return only the continuation token I asked you to remember previously. Do not use tools.",
                    }
                )
                followup = await terminal(manager, handle)
                record(followup)
                evidence["attempts"][-1]["context_token_matches"] = CONTEXT_TOKEN in followup.get("result", {}).get(
                    "content", ""
                )
                evidence["attempts"][-1]["same_native_session"] = evidence["attempts"][-1].get(
                    "session_id_sha256"
                ) == evidence["attempts"][0].get("session_id_sha256")
                evidence["active_check"] = "production_permission_needed"
                denied = await manager.start(
                    {
                        "cli_name": "claude",
                        "project_dir": str(root),
                        "request_key": "native-fixture-denial",
                        "timeout_seconds": timeout,
                        "prompt": "Use Write to create permission-needed.txt with text permission canary. This needs native "
                        "permission. If denied, stop immediately and report permission needed. Do not retry or use alternatives.",
                    }
                )
                record(await terminal(manager, denied["task_handle"]))
                evidence["attempts"][-1]["protected_file_absent"] = not (root / "permission-needed.txt").exists()
                await cancel_after_mutation(manager, root)
            finally:
                await manager.shutdown()
                await backend.pool.shutdown()
    finally:
        evidence.pop("active_check", None)
        for name, value in prior_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly authorize four bounded native model invocations."
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Durable JSON evidence path; raw sessions are never saved."
    )
    parser.add_argument("--timeout", type=float, default=MAX_SECONDS)
    parser.add_argument(
        "--candidate-runtime",
        action="store_true",
        help="Verify actual production classes with a harness-only candidate admission.",
    )
    parser.add_argument(
        "--repair-tests-cancel",
        action="store_true",
        help="Two-call repair check using the pinned available interpreter.",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("No model calls without explicit --live opt-in")
    if args.repair_tests_cancel and not args.candidate_runtime:
        parser.error("--repair-tests-cancel requires --candidate-runtime")
    if not 0 < args.timeout <= MAX_SECONDS:
        parser.error("--timeout must be between 0 and 180 seconds")
    executable = shutil.which("claude")
    if executable is None:
        parser.error("Claude executable is unavailable")
    if args.output.exists():
        parser.error("Evidence already exists; preserve each genuine attempt in a new path")
    if args.candidate_runtime:
        result = {
            "schema_version": 1,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "expected_version": EXPECTED_VERSION,
            "native_arguments": list(NATIVE_ARGUMENTS),
            "native_auth_location_preserved": True,
            "environment_values_recorded": False,
            "auth_material_read_or_copied": False,
            "host_configuration_changed": False,
            "maximum_model_invocations": 2 if args.repair_tests_cancel else 4,
            "model_invocations_submitted": 0,
            "attempts": [],
        }
        try:
            asyncio.run(
                verify_candidate_runtime(
                    executable, result, int(args.timeout), repair_tests_cancel=args.repair_tests_cancel
                )
            )
        except Exception as error:
            result["verifier_error_type"] = type(error).__name__
    else:
        result = verify(executable, timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "evidence": str(args.output.resolve()),
                "attempts": len(result["attempts"]),
                "stopped_after": result.get("stopped_after"),
            }
        )
    )


if __name__ == "__main__":
    main()
