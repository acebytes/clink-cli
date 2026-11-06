"""Run synthetic Codex lifecycle checks through the real candidate runtime and manager."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import clink.native
import clink.runner
from scripts import verify_native_adapters as verifier


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capabilities_available, policy_denied, model",
    [(True, True, None), (True, False, None), (False, True, None), (True, True, "gpt-6.1-sol")],
    ids=["complete_lifecycle", "ordinary_tool_failure", "unverified_project_trust", "frozen_explicit_model"],
)
async def test_codex_candidate_lifecycle_keeps_permissions_and_unverified_gaps(
    tmp_path, monkeypatch, capabilities_available, policy_denied, model
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
with (root / "fake-request-models.jsonl").open("a") as output:
    output.write(json.dumps(sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None) + "\\n")
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
    prepared_root = None
    prior = None
    if model is not None:
        prepared_root = tmp_path / "test_output/native-adapters/codex-attempt1.fixture"
        prepared_root.parent.mkdir(parents=True)
        files = verifier.create_fixture(prepared_root, "codex")
        directory = tmp_path / "docs/verification"
        directory.mkdir(parents=True)
        prior = directory / "prepared.json"
        prior.write_text(
            json.dumps(
                {
                    "scope": verifier.SCOPE,
                    "adapter": "codex",
                    "submission_ledger": [],
                    "submission_allowance_total": 4,
                    "submissions_consumed_total": 0,
                    "fixture_config_files": files,
                    "fixture_config_sha256": verifier.fixture_digest(prepared_root, files),
                    "project_root_sha256": verifier.digest(str(prepared_root)),
                }
            )
        )

        class Control:
            def __init__(self, *args, **kwargs):
                self.cleanup_complete = False
                self.cleanup_failure = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.cleanup_complete = True

        class Scope:
            def __init__(self, rpc, binding):
                self.rpc, self.owned = rpc, {}
                self.receipt = {
                    "configured_before": {"model": "gpt-6-astra", "model_reasoning_effort": "ultra"},
                    "managed_requirements_observed": True,
                }

            def inventory(self):
                return self.receipt

            def enroll(self):
                self.owned = {("synthetic",): {"trusted": True}}
                self.receipt.update(
                    configured_after=dict(self.receipt["configured_before"]),
                    native_write_attempted=True,
                    native_write_confirmed=True,
                )
                return self.receipt

            def cleanup(self):
                self.owned.clear()
                self.receipt["cleanup_complete"] = True
                return self.receipt

        monkeypatch.setattr(verifier, "NativeRpc", Control)
        monkeypatch.setattr(verifier, "TrustScope", Scope)

        def binding(requested_root):
            assert requested_root == prepared_root
            return SimpleNamespace(root=prepared_root)

        monkeypatch.setattr(verifier, "approved_binding", binding)
        monkeypatch.setattr(verifier, "configuration_gates", lambda *args: sorted(verifier.CONFIG_GATES))
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
    args.model = model
    if model is not None:
        args.trust_codex_fixture = True
        args.fixture_root = prepared_root
        args.prior_evidence = prior
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
    if model is not None:
        root = tmp_path / "test_output/native-adapters/codex-attempt1.fixture"
        assert [json.loads(line) for line in (root / "fake-request-models.jsonl").read_text().splitlines()] == [
            model
        ] * 4
        assert receipt["explicit_request_model"] == model
        assert receipt["configuration_preparation_gates"] == []
        assert [entry["check"] for entry in receipt["submission_ledger"]] == list(verifier.CHECKS)
        assert receipt["submission_ledger"][2]["state"] == "permission_needed"
        assert all(entry["actual_model"] is None for entry in receipt["submission_ledger"])
    encoded = json.dumps(receipt)
    assert "child_fingerprint" not in encoded
    assert "CLINK_NATIVE_POLICY_CANARY" not in encoded
    assert clink.native._VERIFIED_NATIVE_POLICIES == admitted
