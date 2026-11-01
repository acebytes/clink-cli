"""Run synthetic Codex lifecycle checks through the real candidate runtime and manager."""

import json
import subprocess
import sys

import pytest

import clink.native
import clink.runner
from scripts import verify_native_adapters as verifier


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capabilities_available, policy_denied",
    [(True, True), (True, False), (False, True)],
    ids=["complete_lifecycle", "ordinary_tool_failure", "unverified_project_trust"],
)
async def test_codex_candidate_lifecycle_keeps_permissions_and_unverified_gaps(
    tmp_path, monkeypatch, capabilities_available, policy_denied
):
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    monkeypatch.setattr(clink.native, "__file__", str(tmp_path / "clink/native.py"))
    monkeypatch.setattr(clink.runner, "CLEANUP_SECONDS", 0.05)
    admitted = dict(clink.native._VERIFIED_NATIVE_POLICIES)
    executable = tmp_path / "fake-codex"
    executable.write_text("#!" + sys.executable + "\n" + """
import json
import pathlib
import signal
import subprocess
import sys
import uuid

if "--version" in sys.argv:
    print("codex-cli 0.166.0")
    raise SystemExit
root = pathlib.Path.cwd()
content = sys.stdin.read()
prompt = json.loads(content.split("<assigned_task_json>\\n", 1)[1].split("\\n</assigned_task_json>", 1)[0])
session = sys.argv[-2] if "resume" in sys.argv else str(uuid.uuid4())
capabilities = @CAPABILITIES@
def emit(event):
    print(json.dumps(event), flush=True)
def hook(event):
    if capabilities:
        completed = subprocess.run([sys.executable, "codex_hook.py"], input=json.dumps({"cwd": str(root), **event}),
            text=True, capture_output=True, check=True)
        assert json.loads(completed.stdout) == {}
hook({"hook_event_name": "SessionStart", "source": "resume" if "resume" in sys.argv else "startup"})
emit({"type": "thread.started", "thread_id": session})
emit({"type": "turn.started"})
text = "Synthetic Codex result."
if "to observe failure" in prompt:
    assert "configured fixture-specialist agent type" in prompt
    assert "spawn_subagent (Task)" not in prompt and "invoke_subagent" not in prompt
    subprocess.run([sys.executable, "fixture.py"], capture_output=True)
    (root / "arithmetic.py").write_text("def add(a, b):\\n    return a + b\\n")
    subprocess.run([sys.executable, "fixture.py"], capture_output=True)
    if capabilities:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "canary"}})
        subprocess.run([sys.executable, "codex_mcp_server.py"], input=request + "\\n", text=True, capture_output=True, check=True)
        child = str(uuid.uuid4())
        hook({"hook_event_name": "SubagentStart", "agent_id": child, "agent_type": "fixture-specialist"})
        hook({"hook_event_name": "SubagentStop", "agent_id": child, "agent_type": "fixture-specialist",
              "last_assistant_message": (root / "codex-specialist.txt").read_text().strip()})
    (root / "fake-memory").write_text("NATIVE_FIXTURE_CONTEXT_274816")
elif "continuation token" in prompt:
    text = (root / "fake-memory").read_text()
elif "/usr/bin/printf CLINK_NATIVE_POLICY_CANARY" in prompt:
    command = "/usr/bin/printf CLINK_NATIVE_POLICY_CANARY"
    hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}})
    denied = @POLICY_DENIED@
    output = f"`{command}` rejected: policy forbids commands starting with `{command}`" if denied else "ordinary setup failure"
    emit({"type": "item.completed", "item": {"id": "permission-canary", "type": "command_execution",
          "command": command, "aggregated_output": output, "status": "declined" if denied else "failed", "exit_code": -1}})
elif "foreground shell operation" in prompt:
    child = subprocess.Popen([sys.executable, str(root / "fixture.py"), "child"])
    (root / "child.pid").write_text(str(child.pid))
    (root / "mutation.txt").write_text("known synthetic partial effect")
    def finish(signum, frame):
        child.wait(timeout=2)
        raise SystemExit
    signal.signal(signal.SIGTERM, finish)
    child.wait()
emit({"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": text}})
emit({"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}})
""".replace("@CAPABILITIES@", repr(capabilities_available)).replace("@POLICY_DENIED@", repr(policy_denied)))
    executable.chmod(0o700)
    monkeypatch.setattr(verifier.shutil, "which", lambda name: str(executable))
    real_run = verifier.subprocess.run

    def process_metadata(command, **kwargs):
        if command[0] == "/bin/ps":
            if "comm=" in command:
                return subprocess.CompletedProcess(command, 0, sys.executable + "\n", "")
            path = tmp_path / "test_output/native-adapters/codex-attempt1.fixture/fixture.py"
            return subprocess.CompletedProcess(
                command, 0, f"Sun Nov 1 01:00:00 2026 {sys.executable} {path} child\n", ""
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(verifier.subprocess, "run", process_metadata)
    args = verifier.argument_parser().parse_args(
        [
            "--live",
            "--adapter",
            "codex",
            "--evidence",
            str(tmp_path / "docs/verification/codex-attempt1.json"),
            "--timeout",
            "5",
        ]
    )
    receipt = await verifier.verify(args)
    if not capabilities_available:
        assert receipt["native_verified"] is False
        assert receipt["submissions_in_this_invocation"] == 1
        assert "codex_project_trust_unverified" in receipt["gates"]
        assert "codex_project_hook_trust_unverified" in receipt["gates"]
        assert "codex_named_subagent_unverified" in receipt["gates"]
    elif not policy_denied:
        assert receipt["native_verified"] is False
        assert receipt["stopped_after"] == "permission_gate_failed"
        assert receipt["submissions_in_this_invocation"] == 3
        assert receipt["submission_ledger"][2]["observations"]["permission_policy_declined_observed"] is False
    else:
        assert receipt.get("stopped_after") is None, receipt
        assert receipt["native_verified"] is True
        assert receipt["submissions_in_this_invocation"] == 4
        assert receipt["submission_ledger"][2]["state"] == "permission_needed"
        assert receipt["submission_ledger"][2]["observations"]["permission_policy_declined_observed"] is True
    assert receipt["pool_running_after_shutdown"] == receipt["project_leases_after_shutdown"] == 0
    encoded = json.dumps(receipt)
    assert "child_fingerprint" not in encoded
    assert "CLINK_NATIVE_POLICY_CANARY" not in encoded
    assert clink.native._VERIFIED_NATIVE_POLICIES == admitted
