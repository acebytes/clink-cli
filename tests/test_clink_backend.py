"""Backend policy, admission bounds and redaction without credentials or network."""

import asyncio
import json
import os
import sys
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import clink.backend as module
from clink.backend import CLIBackend
from clink.contracts import AdapterPolicy, BackendError, CLIRequest
from clink.policy import executable_digest, recipe_digest
from clink.runner import ProcessPool


@pytest.fixture
def backend(tmp_path, monkeypatch):
    executable = tmp_path / "fake-cli"
    executable.write_text(
        "#!" + sys.executable + "\nimport sys\nsys.stdin.read()\n"
        'print(\'{"type":"item.completed","item":{"type":"agent_message","text":"advice"}}\')\n'
        'print(\'{"type":"turn.completed","usage":{"input_tokens":7}}\')\n'
    )
    executable.chmod(0o700)
    role = SimpleNamespace(
        name="default", prompt_text="Role expertise", role_args=(), model="role-model", description=None
    )
    client = SimpleNamespace(
        name="codex",
        adapter="codex",
        runner="codex",
        executable=(str(executable),),
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        config_args=(),
        env={},
        timeout_seconds=30,
        parser="codex_jsonl",
        default_model="client-model",
        roles={"default": role},
        get_role=lambda value: role,
    )
    registry = SimpleNamespace(get_client=lambda name: client, list_clients=lambda: ["codex"])
    monkeypatch.setattr(module, "get_env", lambda key, default=None: default)
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    policy = AdapterPolicy(
        "codex",
        version="fixture",
        verified=True,
        verification_status="test_fixture",
        images=True,
        executable_sha256=executable_digest(str(executable)),
        recipe_sha256=recipe_digest("codex"),
    )
    monkeypatch.setattr(module, "_VERIFIED_POLICIES", {"codex": policy})
    instance = CLIBackend(registry)
    return instance


def test_native_model_precedence_and_frozen_role(backend):
    target = backend.resolve(model="request-model", temperature=0.5)
    assert target.effective_model == "request-model"
    assert target.requested_model == "request-model"
    assert backend.resolve(model="auto").effective_model == "role-model"
    assert backend.resolve().requested_model is None
    backend.registry.get_client("codex").roles["default"].prompt_text = "changed"
    assert target.role_prompt == "Role expertise"
    with pytest.raises(FrozenInstanceError):
        target.role.prompt_text = "changed"
    assert any(code == "unsupported_parameter" for code, _ in target.warnings)


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_discovery_reports_provisional_advisory_availability_without_launch(
    backend, monkeypatch, installed, admitted, platform
):
    policy = backend.resolve().policy
    monkeypatch.setattr(module, "_VERIFIED_POLICIES", {"codex": policy} if admitted else {})
    monkeypatch.setattr(module, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/synthetic/fake-cli" if installed else None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kwargs: pytest.fail("discovery launched"))
    monkeypatch.setattr(module, "executable_digest", lambda *args: pytest.fail("discovery hashed the executable"))
    monkeypatch.setattr(module, "recipe_digest", lambda *args: pytest.fail("discovery checked the recipe"))
    monkeypatch.setattr(backend, "_verify_resources", lambda *args: pytest.fail("discovery checked catalog resources"))
    description = backend.describe()[0]
    assert description["authentication"] == "unknown"
    assert description["installed"] is installed
    assert description["available"] is (installed and admitted and platform == "posix")
    assert description["verification_status"] == ("test_fixture" if admitted else "unverified")
    assert "provisional" in description["launch_validation"]
    assert "requires POSIX" in description["launch_validation"]
    assert "exact executable, recipe and catalog resources rechecked" in description["launch_validation"]
    assert "authentication remains unknown" in description["launch_validation"]


@pytest.mark.asyncio
async def test_fake_cli_normalized_content_and_redaction(backend, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-secret")
    target = backend.resolve()
    assert "OPENAI_API_KEY" not in backend._environment(target)
    assert backend._environment(target)["CLINK_DELEGATION_DEPTH"] == "1"
    result = await backend.run(CLIRequest(prompt="private prompt", target=target))
    assert result.content == "advice"
    assert result.actual_model is None
    assert result.requested_model is None
    serialized = json.dumps(result.to_metadata())
    for forbidden in ("private prompt", "Role expertise", "sentinel-secret", "argv", "raw", "session_id"):
        assert forbidden not in serialized
    assert backend.pool.running == 0


@pytest.mark.asyncio
async def test_policy_unverified_and_recursion_refuse_launch(backend, monkeypatch):
    target = backend.resolve()
    monkeypatch.setattr(module, "_VERIFIED_POLICIES", {})
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kwargs: pytest.fail("refusal launched"))
    monkeypatch.setattr("clink.tasks.get_task_manager", lambda: pytest.fail("consultation selected native execution"))
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="request", target=target))
    assert caught.value.code == "policy_unverified"
    assert "CLI consultation policy" in str(caught.value)
    remedy = caught.value.remedy
    assert "Consultation requires separate version-specific isolation verification" in remedy
    assert "run the opt-in CLI policy verification harness for this installed version" in remedy
    assert "If native execution is intended" in remedy
    assert "listmodels.native_task_clients" in remedy
    assert "clink action=start" in remedy
    assert "cli_name, project_dir, prompt, and request_key" in remedy
    assert "enabled, separately admitted profile" in remedy
    assert "Native tasks can edit files and use configured tools" in remedy
    assert backend.pool.running == 0
    monkeypatch.setenv("CLINK_DELEGATION_DEPTH", "1")
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="request", target=target))
    assert caught.value.code == "recursion_blocked"


@pytest.mark.asyncio
async def test_deadline_and_required_prompt_limit(backend):
    target = backend.resolve()
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="request", target=target, admitted_at=time.monotonic() - 2000))
    assert caught.value.code == "timeout"
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="x" * 1024 * 1024, target=target))
    assert caught.value.code == "input_limit"


def test_cwd_and_context_symlink_boundaries(backend, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    target = backend.resolve()
    with pytest.raises(BackendError) as caught:
        backend.resolve(working_directory=outside)
    assert caught.value.code == "path_not_allowed"
    (outside / "secret.txt").write_text("unrelated")
    link = tmp_path / "link.txt"
    link.symlink_to(outside / "secret.txt")
    with pytest.raises(BackendError) as caught:
        backend._context(CLIRequest(prompt="request", target=target, files=(str(link),)))
    assert caught.value.code == "path_not_allowed"
    with pytest.raises(BackendError):
        backend._context(CLIRequest(prompt="request", target=target, images=("data:image/png;base64,AAA",)))


@pytest.mark.asyncio
async def test_nonzero_never_recovers_valid_terminal_output(backend):
    target = backend.resolve()
    executable = Path(target.client.executable[0])
    executable.write_text(
        "#!" + sys.executable + "\nimport sys\n"
        'print(\'{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}\')\n'
        'print(\'{"type":"turn.completed"}\')\nsys.exit(4)\n'
    )
    policy = replace(target.policy, executable_sha256=executable_digest(str(executable)))
    module._VERIFIED_POLICIES["codex"] = policy
    target = replace(target, policy=policy)
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="request", target=target))
    assert caught.value.code == "nonzero_exit"
    assert caught.value.metadata["exit_status"] == 4
    assert backend.pool.running == 0


@pytest.mark.asyncio
async def test_forged_environment_and_search_fail_closed(backend):
    target = backend.resolve()
    target = replace(target, client=replace(target.client, env=(("ANTHROPIC_API_KEY", "secret"),)))
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="request", target=target))
    assert caught.value.code == "invalid_config"
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="request", target=backend.resolve(), require_search=True))
    assert caught.value.code == "unsupported_capability"


@pytest.mark.asyncio
async def test_runner_output_limit_releases_permit(tmp_path, monkeypatch):
    import clink.runner as runner

    monkeypatch.setattr(runner, "STREAM_BYTES", 128)
    pool = ProcessPool(1)
    with pytest.raises(BackendError) as caught:
        await pool.execute(
            (sys.executable, "-c", "print('x'*500)"),
            content=b"",
            cwd=str(tmp_path),
            env={},
            deadline=time.monotonic() + 5,
            request_id="overflow",
        )
    assert caught.value.code == "output_limit"
    assert pool.running == 0
    assert not pool._children


@pytest.mark.asyncio
async def test_queue_full_timeout_cancel_and_fifo_permit(tmp_path, monkeypatch):
    import clink.runner as runner

    monkeypatch.setattr(runner, "QUEUE_LIMIT", 1)
    monkeypatch.setattr(runner, "QUEUE_SECONDS", 0.03)
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 5)
    waiter = asyncio.create_task(pool._acquire(time.monotonic() + 5))
    await asyncio.sleep(0)
    with pytest.raises(BackendError) as caught:
        await pool._acquire(time.monotonic() + 5)
    assert caught.value.code == "queue_full"
    with pytest.raises(BackendError) as caught:
        await waiter
    assert caught.value.code == "queue_timeout"
    assert not pool._waiters
    waiter = asyncio.create_task(pool._acquire(time.monotonic() + 5))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not pool._waiters
    pool._release()
    assert pool.running == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
async def test_cancellation_kills_descendant_and_drains(tmp_path, monkeypatch):
    import clink.runner as runner

    monkeypatch.setattr(runner, "CLEANUP_SECONDS", 0.1)
    started, escaped = tmp_path / "started", tmp_path / "escaped"
    child = (
        "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("
        + repr(str(started))
        + ").write_text('started'); time.sleep(.7); pathlib.Path("
        + repr(str(escaped))
        + ").write_text('unsafe')"
    )
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(10)"
    pool = ProcessPool(1)
    task = asyncio.create_task(
        pool.execute(
            (sys.executable, "-c", parent),
            content=b"",
            cwd=str(tmp_path),
            env={},
            deadline=time.monotonic() + 5,
            request_id="cancel",
        )
    )
    # The descendant must install its TERM handler before cancellation tests escalation.
    for _ in range(50):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.8)
    assert not escaped.exists()
    assert pool.running == 0
    assert not pool._children


@pytest.mark.asyncio
async def test_shutdown_rejects_granted_waiter_before_spawn(tmp_path, monkeypatch):
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 5)
    calls = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        calls.append(args)
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    queued = asyncio.create_task(
        pool.execute(
            (sys.executable, "-c", "pass"),
            content=b"",
            cwd=str(tmp_path),
            env={},
            deadline=time.monotonic() + 5,
            request_id="granted-shutdown",
        )
    )
    await asyncio.sleep(0)
    assert pool._waiters
    pool._release()
    await pool.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert calls == []
    assert pool.running == 0
    assert not pool._executions


@pytest.mark.asyncio
async def test_shutdown_waits_for_in_progress_spawn_cleanup(tmp_path, monkeypatch):
    pool = ProcessPool(1)
    spawn_entered = asyncio.Event()
    release_spawn = asyncio.Event()
    original_spawn = asyncio.create_subprocess_exec

    async def delayed_spawn(*args, **kwargs):
        spawn_entered.set()
        await release_spawn.wait()
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    active = asyncio.create_task(
        pool.execute(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            content=b"",
            cwd=str(tmp_path),
            env={},
            deadline=time.monotonic() + 5,
            request_id="spawning-shutdown",
        )
    )
    await spawn_entered.wait()
    shutdown = asyncio.create_task(pool.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    release_spawn.set()
    await shutdown
    with pytest.raises(asyncio.CancelledError):
        await active
    assert pool.running == 0
    assert not pool._executions
    assert not pool._children


def test_inline_file_once_and_directory_capability(backend, tmp_path):
    source = tmp_path / "source.py"
    source.write_text("unique_inline_source_sentinel")
    target = backend.resolve()
    request = CLIRequest(prompt="Analyze the declared file", target=target, files=(str(source),))
    files, images = backend._context(request)
    assembled = backend._compose(request, files, images).decode()
    assert assembled.count("unique_inline_source_sentinel") == 1
    assert backend.describe()[0]["file_references"] is False
    assert any(code == "native_filesystem_access_disabled" for code, _ in target.warnings)
    with pytest.raises(BackendError) as caught:
        backend.validate_files(target, (str(tmp_path),))
    assert caught.value.code == "unsupported_capability"


def test_required_inline_file_never_silently_truncated(backend, tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("x" * 1024 * 1024)
    request = CLIRequest(prompt="Analyze", target=backend.resolve(), files=(str(source),))
    files, images = backend._context(request)
    with pytest.raises(BackendError) as caught:
        backend._compose(request, files, images)
    assert caught.value.code == "input_limit"


@pytest.mark.asyncio
async def test_executable_or_recipe_change_revokes_admission(backend):
    target = backend.resolve()
    executable = Path(target.client.executable[0])
    executable.write_text(executable.read_text() + "\n# changed fixture binary\n")
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="request", target=target))
    assert caught.value.code == "policy_unverified"


def test_replay_trims_old_complete_turns_after_required_json_escaping(backend):
    required = '"' * 515000
    request = CLIRequest(
        prompt=required,
        target=backend.resolve(),
        history_turns=("old-sentinel" + "a" * 20000, "new-sentinel"),
        history_omitted_turns=2,
    )
    metadata = {}
    assembled = backend._compose(request, (), (), metadata).decode()
    assert "old-sentinel" not in assembled
    assert "new-sentinel" in assembled
    assert json.dumps(required) in assembled
    assert len(assembled.encode()) <= 1024 * 1024
    assert metadata["history_omitted_turns"] == 3
    assert metadata["history_retained_turns"] == 1


@pytest.mark.asyncio
async def test_queued_consensus_change_fails_before_subprocess(backend, tmp_path, monkeypatch):
    from utils.consensus_manifest import build_manifest

    source = tmp_path / "frozen.txt"
    source.write_text("initial")
    target = backend.resolve()
    manifest = build_manifest([str(source)], (target.client.allowed_roots,))
    await backend.pool._acquire(time.monotonic() + 5)
    # Saturate the actual pool (default two slots).
    await backend.pool._acquire(time.monotonic() + 5)
    calls = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        calls.append(args)
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        backend.run(
            CLIRequest(
                prompt="Analyze",
                target=target,
                files=(str(source),),
                tool_name="consensus",
                context_manifest_json=json.dumps(manifest),
            )
        )
    )
    for _ in range(50):
        if backend.pool._waiters:
            break
        await asyncio.sleep(0.01)
    assert backend.pool._waiters
    source.write_text("changed")
    backend.pool._release()
    with pytest.raises(BackendError) as caught:
        await task
    backend.pool._release()
    assert caught.value.code == "consensus_invalidated"
    assert calls == []
    assert backend.pool.running == 0


@pytest.mark.asyncio
async def test_successful_queue_acquire_never_releases_again_when_clock_crosses_deadline(monkeypatch):
    import clink.runner as runner

    clock = iter((0.0, 0.9, 0.95, 1.1))
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    pool = ProcessPool(1)
    pool.running = 1
    waiter = asyncio.create_task(pool._acquire(1.0))
    await asyncio.sleep(0)
    pool._release()
    assert await waiter == 0.95
    assert pool.running == 1
    pool._release()
    assert pool.running == 0


def test_inline_context_rejects_leaf_parent_symlink_and_fifo_replacement(backend, tmp_path):
    from clink.files import read_context

    target = backend.resolve()
    directory = tmp_path / "dir"
    directory.mkdir()
    source = directory / "source.txt"
    source.write_text("admitted")
    request = CLIRequest(prompt="Analyze", target=target, files=(str(source),))
    files, images = backend._context(request)
    outside = tmp_path.parent / (tmp_path.name + "-protected")
    outside.mkdir()
    (outside / "source.txt").write_text("outside-root-secret")
    source.unlink()
    source.symlink_to(outside / "source.txt")
    with pytest.raises(BackendError) as caught:
        backend._compose(request, files, images)
    assert caught.value.code == "path_not_allowed"
    source.unlink()
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BackendError):
        read_context(Path(files[0]), target.client.allowed_roots, 1024)
    directory.unlink()
    directory.mkdir()
    os.mkfifo(source)
    with pytest.raises(BackendError):
        read_context(source, target.client.allowed_roots, 1024)


def test_images_use_private_bounded_copies_and_cleanup_after_error(backend, tmp_path):
    source = tmp_path / "source.png"
    content = b"\x89PNG\r\n\x1a\nsynthetic fixture bytes"
    source.write_bytes(content)
    request = CLIRequest(prompt="Describe", target=backend.resolve(), images=(str(source),))
    _, images = backend._context(request)
    staged = None
    with pytest.raises(RuntimeError):
        with backend._image_snapshots(request, images, None) as snapshots:
            staged = Path(snapshots[0])
            assert staged != source
            assert staged.read_bytes() == content
            assert staged.stat().st_mode & 0o777 == 0o600
            assert staged.parent.stat().st_mode & 0o777 == 0o700
            source.write_bytes(b"changed original")
            assert staged.read_bytes() == content
            raise RuntimeError("synthetic cancellation/error")
    assert staged is not None and not staged.exists()
    assert source.exists()


def test_reviewed_admission_keeps_pinned_runtime_policy_and_catalog():
    import hashlib

    policy = module._VERIFIED_POLICIES["codex"]
    assert policy.verified and policy.verification_status == "verified"
    assert policy.recipe_sha256 == recipe_digest("codex")
    assert policy.version == "codex-cli 0.166.0"
    assert policy.images and policy.native_search
    for filename, checksum in policy.resource_files:
        resource = Path(__file__).resolve().parents[1] / filename
        assert hashlib.sha256(resource.read_bytes()).hexdigest() == checksum


@pytest.mark.private_evidence
def test_reviewed_admission_matches_pinned_evidence_and_production_recipe(private_evidence_root):
    import hashlib

    policy = module._VERIFIED_POLICIES["codex"]
    record = private_evidence_root / policy.evidence
    data = record.read_bytes()
    evidence = json.loads(data)
    assert hashlib.sha256(data).hexdigest() == policy.evidence_sha256
    assert evidence["admission"]["status"] == "verified"
    assert evidence["observed_version"] == policy.version
    assert evidence["executable_sha256"] == policy.executable_sha256
    assert evidence["policy_recipe_sha256"] == policy.recipe_sha256 == recipe_digest("codex")
    assert sorted(evidence["policy_recipe"]["environment_allowlist"]) == sorted(module.ENV_ALLOWLIST)
    assert evidence["capabilities"]["images"] == policy.images
    assert evidence["capabilities"]["native_web_search"] == policy.native_search


def test_observed_native_warning_errors_are_not_successful_answers():
    from clink.parsers import ParserError, get_parser

    fixture = Path(__file__).parent / "fixtures" / "clink" / "codex-0.166.0-error-warnings.jsonl"
    with pytest.raises(ParserError) as caught:
        get_parser("codex_jsonl").parse(fixture.read_text(), "")
    assert caught.value.code == "parse_error"


def test_catalog_exact_models_and_captured_default_without_alias_fallback(backend, monkeypatch):
    policy = replace(
        backend.resolve().policy,
        supported_models=("role-model", "client-model", "pinned-native"),
        native_default_model="pinned-native",
    )
    monkeypatch.setattr(module, "_VERIFIED_POLICIES", {"codex": policy})
    assert backend.resolve().effective_model == "role-model"
    client = backend.registry.get_client("codex")
    client.roles["default"].model = None
    assert backend.resolve().effective_model == "client-model"
    client.default_model = None
    target = backend.resolve(model="auto")
    assert target.requested_model is None
    assert target.effective_model == "pinned-native"
    assert target.default_origin == "pinned_native_catalog"
    for unknown in ("unknown", "namespace/pinned-native", "pinned-native-extra"):
        with pytest.raises(BackendError) as caught:
            backend.resolve(model=unknown)
        assert caught.value.code == "unsupported_capability"


def test_catalog_resource_hash_guard_is_independent_of_working_directory(tmp_path, monkeypatch):
    import hashlib

    bundle = tmp_path / "clink"
    resources = bundle / "resources"
    resources.mkdir(parents=True)
    resource = resources / "catalog.json"
    resource.write_text('{"models":[]}')
    policy = AdapterPolicy(
        "codex", resource_files=(("clink/resources/catalog.json", hashlib.sha256(resource.read_bytes()).hexdigest()),)
    )
    monkeypatch.setattr(module, "__file__", str(bundle / "backend.py"))
    module.CLIBackend._verify_resources(policy)
    resource.write_text('{"models":["changed"]}')
    with pytest.raises(BackendError) as caught:
        module.CLIBackend._verify_resources(policy)
    assert caught.value.code == "policy_unverified"


@pytest.mark.asyncio
async def test_invalid_utf8_output_is_not_repaired_into_success(tmp_path):
    pool = ProcessPool(1)
    with pytest.raises(BackendError) as caught:
        await pool.execute(
            (sys.executable, "-c", 'import sys; sys.stdout.buffer.write(b\'{\\"text\\":\\"\\xff\\"}\')'),
            content=b"",
            cwd=str(tmp_path),
            env={},
            deadline=time.monotonic() + 5,
            request_id="invalid-utf8",
        )
    assert caught.value.code == "parse_error"
    assert pool.running == 0
    assert not pool._children
