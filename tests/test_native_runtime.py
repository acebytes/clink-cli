"""Synthetic native runtime coverage, without authentication or provider calls."""

import asyncio
import hashlib
import json
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import clink.native as native
from clink.backend import CLIBackend
from clink.contracts import AdapterPolicy, BackendError
from clink.models import CLIClientConfig, NativeTaskConfig, ResolvedCLIClient, ResolvedCLIRole, ResolvedNativeTask
from clink.native import NativeRuntime
from clink.native_policy import native_recipe_digest
from clink.policy import executable_digest, recipe_digest
from clink.registry import ClinkRegistry, RegistryLoadError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    monkeypatch.setenv("CLINK_DEFAULT_CLI", "claude")
    executable = tmp_path / "fake-claude"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import json,os,pathlib,sys\n"
        "prompt=sys.stdin.read()\n"
        "pathlib.Path('capture.json').write_text(json.dumps({'argv':sys.argv[1:],'prompt':prompt,'env':dict(os.environ)}))\n"
        "print(json.dumps({'type':'system','subtype':'init','session_id':'session-1'}))\n"
        "print(json.dumps({'type':'result','subtype':'success','session_id':'session-1','is_error':False,'result':'done'}))\n"
    )
    executable.chmod(0o755)
    role = ResolvedCLIRole(name="default", prompt_path=tmp_path / "role.txt", prompt_text="Native role")
    client = ResolvedCLIClient(
        name="claude",
        adapter="claude",
        executable=(str(executable),),
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        timeout_seconds=10,
        parser="claude_json",
        roles={"default": role},
        default_model="legacy-sonnet",
        native_task=ResolvedNativeTask(
            enabled=True,
            inherit_env=("SYNTHETIC_MCP_TOKEN",),
            required_env=(),
            roles={"default": role},
        ),
    )
    registry = SimpleNamespace(get_client={"claude": client}.__getitem__, list_clients=lambda: ["claude"])
    evidence = tmp_path / "docs" / "verification" / "native.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"scope":"synthetic_fixture"}')
    monkeypatch.setattr(native, "__file__", str(tmp_path / "clink" / "native.py"))
    policy = AdapterPolicy(
        "claude",
        verified=True,
        version="synthetic-fixture",
        verification_status="synthetic_fixture",
        file_references=True,
        executable_sha256=executable_digest(str(executable)),
        recipe_sha256=native_recipe_digest(),
        evidence="docs/verification/native.json",
        evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(native, "_VERIFIED_NATIVE_POLICIES", {"claude": policy})
    return NativeRuntime(CLIBackend(registry))


def prepared(runtime, **kwargs):
    return runtime.prepare(
        "claude", None, None, str(runtime.backend.registry.get_client("claude").working_dir), **kwargs
    )


def test_native_selection_is_frozen_and_does_not_inherit_advisory_model(runtime):
    context = prepared(runtime)
    assert context.target.effective_model is None
    assert context.target.client.default_model is None
    assert context.target.role.prompt_text == "Native role"
    runtime.backend.registry.get_client("claude").native_task.roles["default"] = ResolvedCLIRole(
        name="default", prompt_path=context.target.working_directory / "role.txt", prompt_text="Changed"
    )
    assert context.target.role.prompt_text == "Native role"
    assert runtime.pool is runtime.backend.pool


def test_prepare_and_discovery_never_launch_or_authenticate(runtime, monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **kw: pytest.fail("launched"))
    prepared(runtime)
    result = runtime.describe()[0]
    assert result["authentication"] == "unknown"
    assert result["available"] is True
    monkeypatch.setattr(native, "_VERIFIED_NATIVE_POLICIES", {})
    assert runtime.describe()[0]["available"] is False
    with pytest.raises(BackendError) as caught:
        prepared(runtime)
    assert caught.value.code == "policy_unverified"


@pytest.mark.asyncio
async def test_native_fake_cli_preserves_configuration_and_env_names_without_diagnostics(runtime, monkeypatch, caplog):
    monkeypatch.setenv("SYNTHETIC_MCP_TOKEN", "private-secret")
    monkeypatch.setenv("UNCONFIGURED_SECRET", "excluded-secret")
    context = prepared(runtime)
    assert "private-secret" not in repr(context)
    started = []
    result = await runtime.run(context, "private task", attempt_id="attempt-1", started=lambda: started.append(True))
    capture = json.loads((context.target.working_directory / "capture.json").read_text())
    assert capture["env"]["SYNTHETIC_MCP_TOKEN"] == "private-secret"
    assert "UNCONFIGURED_SECRET" not in capture["env"]
    assert capture["env"]["CLINK_DELEGATION_DEPTH"] == "1"
    assert capture["argv"] == ["--print", "--output-format", "stream-json", "--verbose", "--permission-prompts", "none"]
    assert "private task" in capture["prompt"]
    assert "ADVISORY EXECUTION POLICY" not in capture["prompt"]
    assert started == [True]
    assert result.state == "completed" and result.session_id == "session-1"
    assert result.metadata["claims_verification"] == "agent_reported_unverified"
    for forbidden in ("private-secret", "private task", "session-1", "capture.json"):
        assert forbidden not in json.dumps(result.metadata)
        assert forbidden not in caplog.text


@pytest.mark.asyncio
async def test_followup_passes_native_resume_without_replaying_prior_prompt(runtime):
    context = prepared(runtime)
    await runtime.run(context, "first private prompt", attempt_id="attempt-1")
    result = await runtime.run(context, "new prompt only", "session-1", attempt_id="attempt-2")
    capture = json.loads((context.target.working_directory / "capture.json").read_text())
    assert capture["argv"][-2:] == ["--resume", "session-1"]
    assert "first private prompt" not in capture["prompt"]
    assert result.state == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["receipt", "binary", "recipe"])
async def test_binary_or_receipt_changes_block_launch(runtime, changed, monkeypatch):
    context = prepared(runtime)
    if changed == "receipt":
        evidence = context.target.working_directory / "docs" / "verification" / "native.json"
        evidence.write_text("changed")
    elif changed == "binary":
        executable = context.target.working_directory / "fake-claude"
        executable.write_text(executable.read_text() + "\n")
    else:
        monkeypatch.setattr(native, "native_recipe_digest", lambda adapter: "changed")
    with pytest.raises(BackendError) as caught:
        await runtime.run(context, "task", attempt_id="attempt-1")
    assert caught.value.code == "policy_unverified"
    assert not (context.target.working_directory / "capture.json").exists()


def test_context_roots_images_required_environment_and_known_depth(runtime, monkeypatch, tmp_path):
    with pytest.raises(BackendError) as caught:
        runtime.prepare("claude", None, None, str(tmp_path.parent))
    assert caught.value.code == "path_not_allowed"
    with pytest.raises(BackendError) as caught:
        prepared(runtime, images=(str(tmp_path / "image.png"),))
    assert caught.value.code == "unsupported_capability"
    client = runtime.backend.registry.get_client("claude")
    object.__setattr__(client.native_task, "required_env", ("SYNTHETIC_MCP_TOKEN",))
    monkeypatch.delenv("SYNTHETIC_MCP_TOKEN", raising=False)
    with pytest.raises(BackendError) as caught:
        prepared(runtime)
    assert caught.value.code == "missing_environment"
    assert caught.value.metadata == {"missing_environment_names": ["SYNTHETIC_MCP_TOKEN"]}
    monkeypatch.setenv("CLINK_DELEGATION_DEPTH", "1")
    with pytest.raises(BackendError) as caught:
        prepared(runtime)
    assert caught.value.code == "recursion_blocked"


@pytest.mark.parametrize(
    "profile",
    [
        {"inherit_env": ["TOKEN=value"]},
        {"inherit_env": ["CLINK_DELEGATION_DEPTH"]},
        {"required_env": ["TOKEN"]},
        {"inherit_env": ["TOKEN", "TOKEN"]},
    ],
)
def test_native_environment_configuration_accepts_names_only(profile):
    with pytest.raises(ValidationError):
        NativeTaskConfig.model_validate(profile)


@pytest.mark.asyncio
async def test_nonzero_success_envelope_stays_failed_and_session_mismatch_is_uncertain(runtime):
    context = prepared(runtime)
    result = await runtime.run(context, "task", "other-session", attempt_id="mismatch")
    assert result.state == "uncertain"
    assert result.session_id is None
    assert result.metadata["error_code"] == "native_session_mismatch"

    async def fake_output(*args, **kwargs):
        return SimpleNamespace(
            stdout=json.dumps(
                {"type": "result", "subtype": "success", "session_id": "session-1", "result": "done", "is_error": False}
            ),
            stderr="",
            returncode=2,
            stdout_bytes=120,
            stderr_bytes=0,
            queue_seconds=0,
        )

    runtime.pool.execute = fake_output
    result = await runtime.run(context, "task", attempt_id="nonzero")
    assert result.state == "failed"
    assert result.metadata["error_code"] == "nonzero_exit"


@pytest.mark.asyncio
async def test_queued_environment_values_resolve_only_at_actual_prelaunch(runtime, monkeypatch):
    context = prepared(runtime)
    monkeypatch.setenv("SYNTHETIC_MCP_TOKEN", "old")
    await runtime.pool._acquire(time.monotonic() + 5)
    await runtime.pool._acquire(time.monotonic() + 5)
    task = asyncio.create_task(runtime.run(context, "task", attempt_id="queued"))
    for _ in range(100):
        if runtime.pool._waiters:
            break
        await asyncio.sleep(0.001)
    assert runtime.pool._waiters
    monkeypatch.setenv("SYNTHETIC_MCP_TOKEN", "new")
    runtime.pool._release()
    await task
    runtime.pool._release()
    capture = json.loads((context.target.working_directory / "capture.json").read_text())
    assert capture["env"]["SYNTHETIC_MCP_TOKEN"] == "new"


def test_codex_recipe_is_unchanged():
    assert recipe_digest("codex") == "4bf9a7fe0491e12965d5c36dd7ccc9d197dd271cdf57df378c68fc1b9e0dae16"


@pytest.mark.asyncio
async def test_shorter_configured_timeout_caps_long_attempt_deadline(runtime, monkeypatch):
    context = prepared(runtime, absolute_deadline=time.monotonic() + 1800)
    executable = context.target.working_directory / "fake-claude"
    executable.write_text(executable.read_text() + "\nimport time\ntime.sleep(60)\n")
    policy = replace(context.target.policy, executable_sha256=executable_digest(str(executable)))
    monkeypatch.setattr(native, "_VERIFIED_NATIVE_POLICIES", {"claude": policy})
    context = replace(
        context,
        target=replace(context.target, policy=policy, client=replace(context.target.client, timeout_seconds=1)),
    )
    started_at = time.monotonic()
    with pytest.raises(BackendError) as caught:
        await asyncio.wait_for(runtime.run(context, "task", attempt_id="short-configured-timeout"), 3)
    assert caught.value.code == "timeout"
    assert time.monotonic() - started_at < 3
    assert runtime.pool.running == 0
    assert not runtime.pool._children


@pytest.mark.parametrize(
    "profile,adapter,accepted",
    [
        ({"enabled": True, "roles": {"default": {"prompt_path": "native.txt"}}}, "claude", True),
        ({"enabled": True, "roles": {}}, "claude", False),
        ({"enabled": True, "roles": {"default": {}}}, "claude", False),
        (
            {"enabled": True, "roles": {"default": {"prompt_path": "native.txt", "role_args": ["--verbose"]}}},
            "claude",
            False,
        ),
        ({"enabled": False, "roles": {}}, "codex", False),
    ],
)
def test_registry_native_profiles_require_explicit_native_role_prompts(tmp_path, profile, adapter, accepted):
    (tmp_path / "native.txt").write_text("Native role expertise")
    raw = CLIClientConfig.model_validate(
        {
            "name": adapter,
            "adapter": adapter,
            "command": adapter,
            "working_dir": str(tmp_path),
            "native_task": profile,
        }
    )
    registry = ClinkRegistry.__new__(ClinkRegistry)
    registry._startup_cwd = tmp_path
    if accepted:
        client = registry._resolve_config(raw, source_path=tmp_path / "client.json")
        assert client.native_task.enabled
        assert client.native_task.roles["default"].prompt_text == "Native role expertise"
        assert client.roles["default"].prompt_text != "Native role expertise"
    else:
        with pytest.raises(RegistryLoadError):
            registry._resolve_config(raw, source_path=tmp_path / "client.json")
