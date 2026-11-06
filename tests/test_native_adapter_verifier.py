"""Exercise generic verifier evidence, bounds and real lifecycle using only fake CLIs."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import clink.native
import clink.runner
from scripts import verify_native_adapters as verifier


@pytest.fixture
def repository(tmp_path, monkeypatch):
    (tmp_path / "docs" / "verification").mkdir(parents=True)
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    monkeypatch.setattr(clink.runner, "CLEANUP_SECONDS", 0.05)
    return tmp_path


def arguments(repository, **overrides):
    args = verifier.argument_parser().parse_args(
        [
            "--live",
            "--adapter",
            "grok",
            "--evidence",
            str(repository / "docs/verification/attempt1.json"),
            "--timeout",
            "5",
        ]
    )
    for name, value in overrides.items():
        setattr(args, name, value)
    return args


@pytest.fixture
def synthetic_cli(repository, monkeypatch):
    executable = repository / "synthetic-grok"
    executable.write_text(
        "#!" + sys.executable + "\n" + """
import json
import pathlib
import signal
import subprocess
import sys
import uuid

if "--version" in sys.argv:
    print("grok 1.0.70 (f50904eaee6c)")
    raise SystemExit
root = pathlib.Path.cwd()
with (root / "synthetic-launches.txt").open("a") as output:
    output.write("launch\\n")
content = pathlib.Path(sys.argv[sys.argv.index("--prompt-file") + 1]).read_text()
prompt = json.loads(content.split("<assigned_task_json>\\n", 1)[1].split("\\n</assigned_task_json>", 1)[0])
session = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else str(uuid.uuid4())
text = "Synthetic report, no real provider."
def hook(tool, data, **extra):
    event = {"toolName": tool, "toolInput": data, "hook_event_name": "PreToolUse", **extra}
    return subprocess.run([sys.executable, "scope_hook.py"], input=json.dumps(event),
        text=True, capture_output=True, check=True)
if "to observe failure" in prompt:
    subprocess.run([sys.executable, "fixture.py"], capture_output=True)
    hook("StrReplace", {"path": "arithmetic.py"})
    (root / "arithmetic.py").write_text("def add(a, b):\\n    return a + b\\n")
    subprocess.run([sys.executable, "fixture.py"], capture_output=True)
    if "MCP canary" in prompt:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "canary"}})
        subprocess.run([sys.executable, "mcp_server.py"], input=request + "\\n", text=True, capture_output=True, check=True)
        hook("", {}, hook_event_name="SubagentStart", subagentType="fixture-specialist")
        hook("read_file", {"path": "specialist.txt"}, hook_event_name="PostToolUse",
             subagentType="fixture-specialist", toolResult={"type": "ReadFile",
                "FileContent": {"raw_output": (root / "specialist.txt").read_text()}})
        hook("", {}, hook_event_name="SubagentStop", subagentType="fixture-specialist")
    (root / "synthetic-memory").write_text("NATIVE_FIXTURE_CONTEXT_274816")
elif "continuation token" in prompt:
    text = (root / "synthetic-memory").read_text()
elif "permission-needed.txt" in prompt:
    decision = json.loads(hook("Write", {"path": "permission-needed.txt"}).stdout)
    assert decision["decision"] == "ask"
    print("permission required: Write permission-needed.txt was not run, as a headless run cannot ask for approval", file=sys.stderr)
elif "foreground shell operation" in prompt:
    child = subprocess.Popen([sys.executable, str(root / "fixture.py"), "child"])
    (root / "child.pid").write_text(str(child.pid))
    (root / "mutation.txt").write_text("synthetic partial effect")
    def finish(signum, frame):
        child.wait(timeout=2)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, finish)
    child.wait()
print(json.dumps({"text": text, "stopReason": "end_turn", "sessionId": session,
    "modelUsage": {"synthetic-model": {}}}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setattr(verifier.shutil, "which", lambda name: str(executable))
    return executable


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, 181, float("inf"), float("nan")])
async def test_invalid_deadline_stops_before_discovery(repository, monkeypatch, timeout):
    monkeypatch.setattr(verifier.shutil, "which", lambda *args: pytest.fail("Invalid timeout reached discovery"))
    with pytest.raises(ValueError, match="180 seconds"):
        await verifier.verify(arguments(repository, timeout=timeout))


@pytest.mark.asyncio
async def test_explicit_live_optin_is_required(repository, monkeypatch):
    monkeypatch.setattr(verifier.shutil, "which", lambda *args: pytest.fail("Missing opt-in reached discovery"))
    with pytest.raises(ValueError, match="--live"):
        await verifier.verify(arguments(repository, live=False))


@pytest.mark.asyncio
async def test_prepare_only_is_durable_unique_and_never_submits(repository, synthetic_cli, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Prepare-only launched a model process")

    monkeypatch.setattr(verifier.ProcessPool, "execute", forbidden)
    args = arguments(repository, prepare_only=True)
    receipt = await verifier.verify(args)
    root = repository / "test_output/native-adapters/attempt1.fixture"
    assert (root / ".git").is_dir()
    assert (root / ".clink-native-verification-fixture").is_file()
    assert receipt["submissions_consumed_total"] == 0
    assert receipt["native_verified"] is False
    assert receipt["prepared_controls"] == ["native_policy_preserved", "synthetic_scope_prepared"]
    assert receipt["candidate_preflight_sha256"] == verifier.digest(
        args.evidence.with_name("attempt1.preflight.json").read_bytes()
    )
    assert not (root / "synthetic-launches.txt").exists()
    with pytest.raises(ValueError, match="already exists"):
        await verifier.verify(args)


def test_fixture_arithmetic_fails_then_passes_without_provider_work(repository):
    root = repository / "fixture"
    files = verifier.create_fixture(root, "grok")
    assert not verifier.fixture_test(root)
    original_digest = verifier.fixture_digest(root, files)
    (root / "arithmetic.py").write_text("def add(a, b):\n    return a + b\n")
    assert verifier.fixture_test(root)
    # Task edits do not invalidate the immutable launch/configuration binding.
    assert verifier.fixture_digest(root, files) == original_digest
    observations = verifier.fixture_observations(root)
    assert observations["native_test_failure_observed"] is False
    assert observations["native_test_success_observed"] is False


def test_grok_fixture_permission_ask_and_exact_command_scope(repository):
    root = repository / "fixture"
    verifier.create_fixture(root, "grok")

    def hook(tool, data):
        completed = subprocess.run(
            [sys.executable, "scope_hook.py"],
            cwd=root,
            text=True,
            capture_output=True,
            input=json.dumps({"toolName": tool, "toolInput": data}),
            check=True,
        )
        return json.loads(completed.stdout)

    assert hook("Write", {"path": "permission-needed.txt"})["decision"] == "ask"
    assert hook("run_terminal_command", {"command": verifier.fixture_command()}) == {}
    assert hook("run_terminal_command", {"command": verifier.fixture_command() + "; echo unrelated"}) == {}
    assert hook("native_discovery_tool", {}) == {}
    assert not (root / "permission-needed.txt").exists()


def test_grok_fixture_uses_native_task_rule_without_a_permission_mode_override(repository):
    root = repository / "fixture"
    verifier.create_fixture(root, "grok")
    configuration = (root / ".grok/config.toml").read_text()
    allow_line = next(line for line in configuration.splitlines() if line.startswith("allow = "))
    rules = json.loads(allow_line.removeprefix("allow = "))
    assert "task" in rules
    assert "*" not in rules
    assert not any(rule.startswith(("Agent(", "Task(", "spawn_subagent(")) for rule in rules)
    assert "permission_mode" not in configuration
    assert "bypassPermissions" not in configuration
    assert 'ask = ["Write(./permission-needed.txt)", "Edit(./permission-needed.txt)"]' in configuration


@pytest.mark.parametrize("path_field", ["target_file", "path", "file_path"])
def test_subagent_canary_requires_matching_child_and_exact_read_event(repository, path_field):
    root = repository / "fixture"
    verifier.create_fixture(root, "grok")

    def event(kind, *, child="fixture-specialist", path="specialist.txt"):
        request = {
            "hook_event_name": kind,
            "subagentType": child,
            "toolName": "read_file",
            "toolInput": {path_field: path},
            "toolResult": {
                "type": "ReadFile",
                "FileContent": {"raw_output": "PRIVATE_TOOL_RESULT " + verifier.SPECIALIST_TOKEN},
            },
            "session_id": "PRIVATE_SESSION_ID",
        }
        subprocess.run(
            [sys.executable, "scope_hook.py"],
            cwd=root,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=True,
        )

    event("SubagentStart")
    event("SubagentStop")
    event("PostToolUse", path="arithmetic.py")
    event("PostToolUse", child="unrelated-agent")
    observed = verifier.fixture_observations(root)
    assert observed["fixture_subagent_started"] is observed["fixture_subagent_stopped"] is True
    assert observed["fixture_subagent_canary_matches"] is False
    event("PostToolUse")
    assert verifier.fixture_observations(root)["fixture_subagent_canary_matches"] is True
    encoded = (root / "hooks.jsonl").read_text()
    for private in ("PRIVATE_TOOL_RESULT", "PRIVATE_SESSION_ID", verifier.SPECIALIST_TOKEN):
        assert private not in encoded


@pytest.mark.parametrize("alias", [False, True])
def test_grok_child_read_requires_successful_native_result_and_supports_command_aliases(repository, alias):
    root = repository / "fixture"
    verifier.create_fixture(root, "grok")
    request = {
        "hook_event_name": "PostToolUse",
        "subagentType": "fixture-specialist",
        "tool_name" if alias else "toolName": "read_file",
        "tool_input" if alias else "toolInput": {"target_file": str(root / "specialist.txt")},
    }
    result_key = "tool_response" if alias else "toolResult"
    for result, expected in (
        ({"type": "ReadFile", "FileNotFound": verifier.SPECIALIST_TOKEN}, False),
        ({"text": verifier.SPECIALIST_TOKEN}, False),
        ({"type": "ReadFile", "FileContent": {"content": verifier.SPECIALIST_TOKEN}}, True),
    ):
        request[result_key] = result
        subprocess.run(
            [sys.executable, "scope_hook.py"],
            cwd=root,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=True,
        )
        assert verifier.fixture_observations(root)["fixture_subagent_canary_matches"] is expected


def test_preflight_rejects_wrong_release_and_exports_no_raw_version_output(repository, monkeypatch):
    binary = repository / "binary"
    binary.write_bytes(b"synthetic")
    monkeypatch.setattr(
        verifier.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, b"grok 9.9.9", b"PRIVATE")
    )
    with pytest.raises(ValueError, match="researched release"):
        verifier.preflight(str(binary), "grok")


def test_preflight_rejects_binary_update_during_version_probe(repository, monkeypatch):
    binary = repository / "binary"
    binary.write_bytes(b"first synthetic version")

    def update_during_probe(*args, **kwargs):
        binary.write_bytes(b"second synthetic version")
        return subprocess.CompletedProcess([], 0, b"grok 1.0.70 (f50904eaee6c)", b"")

    monkeypatch.setattr(verifier.subprocess, "run", update_during_probe)
    with pytest.raises(ValueError, match="changed during"):
        verifier.preflight(str(binary), "grok")


def test_snapshot_summary_never_exports_session_prompt_result_argv_or_environment():
    private = "PRIVATE_NATIVE_MATERIAL"
    snapshot = {
        "state": "completed",
        "task_handle": private,
        "attempt_id": private,
        "session_id": private,
        "prompt": private,
        "argv": [private],
        "environment": {"PRIVATE": private},
        "result": {"content": private, "metadata": {"actual_model": "native-model", "raw": private, "stdout": private}},
    }
    summary = verifier.snapshot_summary(snapshot)
    assert private not in json.dumps(summary)
    assert summary["actual_model"] == "native-model"
    assert summary["actual_model_origin"] == "native_terminal_metadata"


@pytest.mark.asyncio
@pytest.mark.parametrize("transient_identity_misses", [0, 2])
async def test_four_check_lifecycle_uses_real_runtime_manager_pool_and_keeps_policy_unmodified(
    repository, synthetic_cli, monkeypatch, transient_identity_misses
):
    admitted_before = dict(clink.native._VERIFIED_NATIVE_POLICIES)
    native_runtime_project = Path(clink.native.__file__).resolve().parents[1]
    # Admission path checking anchors to the runtime module's project, not the verifier's test project.
    monkeypatch.setattr(clink.native, "__file__", str(repository / "clink/native.py"))
    events = []

    @contextmanager
    def synthetic_trust(root, grok_home):
        assert root == root.resolve()
        receipt = {"fixture_trust_prepared": True, "fixture_trust_removed": False}
        events.append("prepared")
        try:
            yield receipt
        finally:
            receipt["fixture_trust_removed"] = True
            events.append("removed")

    monkeypatch.setattr(verifier, "grok_fixture_trust", synthetic_trust)
    run = verifier.subprocess.run
    identity_probes = []

    def synthetic_process_metadata(command, **kwargs):
        if command[0] == "/bin/ps":
            if command[-1] == "comm=":
                assert command[2] == str(os.getpid())
                return subprocess.CompletedProcess(command, 0, sys.executable + "\n", "")
            identity_probes.append(True)
            if len(identity_probes) <= transient_identity_misses:
                return subprocess.CompletedProcess(command, 0, "", "")
            root = repository / "test_output/native-adapters/attempt1.fixture"
            assert (root / "child-ready.txt").is_file()
            text = "Sun Nov 1 01:00:00 2026 " + sys.executable + " " + str(root / "fixture.py") + " child\n"
            return subprocess.CompletedProcess(command, 0, text, "")
        return run(command, **kwargs)

    monkeypatch.setattr(verifier.subprocess, "run", synthetic_process_metadata)
    args = arguments(repository, trust_grok_fixture=True)
    receipt = await verifier.verify(args)
    assert events == ["prepared", "removed"]
    assert receipt.get("stopped_after") is None, receipt
    assert receipt["native_verified"] is True
    assert len(identity_probes) == transient_identity_misses + 1
    assert receipt["gates"] == []
    assert "detached or remote work not proven" in receipt["cleanup_scope"]
    assert receipt["submissions_consumed_total"] == receipt["submissions_in_this_invocation"] == 4
    assert [entry["check"] for entry in receipt["submission_ledger"]] == list(verifier.CHECKS)
    assert all(entry["spawn_observed"] is True and entry["passed"] is True for entry in receipt["submission_ledger"])
    assert receipt["pool_running_after_shutdown"] == receipt["project_leases_after_shutdown"] == 0
    assert receipt["fixture_trust"] == {"fixture_trust_prepared": True, "fixture_trust_removed": True}
    encoded = json.dumps(receipt)
    for private in (
        verifier.CONTEXT_TOKEN,
        "<assigned_task_json>",
        "synthetic-memory",
        "--prompt-file",
        str(os.environ.get("HOME")),
    ):
        assert private not in encoded
    assert clink.native._VERIFIED_NATIVE_POLICIES == admitted_before
    assert native_runtime_project != repository


@pytest.mark.asyncio
async def test_followup_alone_cannot_recreate_a_session(repository, synthetic_cli):
    with pytest.raises(ValueError, match="same process"):
        await verifier.verify(arguments(repository, checks="followup"))


@pytest.mark.asyncio
@pytest.mark.parametrize("tampered_file", ["fixture.py", "scope_hook.py"])
async def test_native_fixture_tampering_stops_before_independent_script_execution(
    repository, synthetic_cli, monkeypatch, tampered_file
):
    monkeypatch.setattr(clink.native, "__file__", str(repository / "clink/native.py"))
    source = synthetic_cli.read_text()
    source = source.replace(
        'print(json.dumps({"text": text,',
        f'(root / {tampered_file!r}).write_text("raise SystemExit(0)\\n")\nprint(json.dumps({{"text": text,',
    )
    synthetic_cli.write_text(source)
    calls = []
    fixture_test = verifier.fixture_test

    def checked_test(root):
        calls.append(True)
        return fixture_test(root)

    monkeypatch.setattr(verifier, "fixture_test", checked_test)
    receipt = await verifier.verify(arguments(repository, checks="edit"))
    assert receipt["submissions_in_this_invocation"] == 1
    assert receipt["stopped_after"] == "fixture_configuration_changed"
    assert receipt["native_verified"] is False
    assert receipt["submission_ledger"][0]["passed"] is False
    assert receipt["fixture_configuration_intact_after_shutdown"] is False
    assert calls == [True]


def test_forged_fixture_pid_never_authorizes_signalling(repository, monkeypatch):
    root = repository / "fixture"
    root.mkdir()
    (root / "child.pid").write_text(str(os.getpid()))
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "Sun Nov 1 01:00:00 2026 /bin/unrelated\n", ""),
    )
    identity = verifier.fixture_child_identity(root)
    assert identity is None
    monkeypatch.setattr(verifier.os, "kill", lambda *args: pytest.fail("Unowned PID must never be signalled"))
    assert verifier.descendant_status(identity) == (False, False)


def test_child_identity_accepts_only_the_verifiers_own_kernel_interpreter(repository, monkeypatch):
    root = repository / "fixture"
    root.mkdir()
    (root / "child.pid").write_text("12345")
    framework = repository / "framework" / "Python"
    framework.parent.mkdir()
    framework.write_bytes(b"synthetic framework executable")
    selected = framework

    def metadata(command, **kwargs):
        if command[-1] == "comm=":
            assert command[2] == str(os.getpid())
            return subprocess.CompletedProcess(command, 0, str(framework) + "\n", "")
        assert command[2] == "12345"
        text = f"Sun Nov 1 01:00:00 2026 {selected} {root / 'fixture.py'} child\n"
        return subprocess.CompletedProcess(command, 0, text, "")

    monkeypatch.setattr(verifier.subprocess, "run", metadata)
    assert verifier.fixture_child_identity(root) is not None
    selected = repository / "unrelated-python"
    selected.write_bytes(b"unrelated executable")
    assert verifier.fixture_child_identity(root) is None


@pytest.mark.asyncio
async def test_prior_spent_budget_survives_recipe_change_and_requires_explicit_replacement(repository, synthetic_cli):
    args = arguments(repository, prepare_only=True)
    receipt = await verifier.verify(args)
    receipt["submission_ledger"] = [{"ordinal": 1, "check": "edit", "spawn_observed": True, "passed": False}]
    receipt["submissions_consumed_total"] = 1
    receipt["native_recipe_sha256"] = "previous-incompatible-recipe"
    verifier.persist(args.evidence, receipt)
    next_args = arguments(
        repository,
        evidence=args.evidence.with_name("attempt2.json"),
        prior_evidence=args.evidence,
        checks="edit",
        prepare_only=True,
    )
    with pytest.raises(ValueError, match="--replace-check"):
        await verifier.verify(next_args)
    next_args.replace_check = ["edit"]
    next_receipt = await verifier.verify(next_args)
    assert next_receipt["submissions_consumed_total"] == 1
    assert next_receipt["submission_allowance_total"] == 4
    assert next_receipt["prior_evidence_sha256"] == verifier.digest(args.evidence.read_bytes())


@pytest.mark.asyncio
async def test_pool_ceiling_journals_each_failed_launch_without_retry(repository):
    path = repository / "docs/verification/pool.json"
    evidence = {"submission_ledger": [], "submission_allowance_total": 4}
    verifier.persist(path, evidence, exclusive=True)
    pool = verifier.VerificationPool(evidence, path)
    try:
        for _ in range(4):
            with pytest.raises(verifier.BackendError) as caught:
                await pool.execute(
                    (str(repository / "missing-cli"),),
                    content=b"",
                    cwd=str(repository),
                    env={},
                    deadline=asyncio.get_running_loop().time() + 5,
                    request_id="synthetic",
                )
            assert caught.value.code == "cli_missing"
        with pytest.raises(verifier.BackendError, match="allowance"):
            await pool.execute((sys.executable, "-c", "pass"))
        assert len(verifier.read_json(path)["submission_ledger"]) == 4
        assert pool.running == 0
    finally:
        await pool.shutdown()
