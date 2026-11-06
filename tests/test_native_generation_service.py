"""Native generation authority, admission, final-only output and shared cleanup using synthetic CLIs."""

import asyncio
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import clink.native_generation as module
from clink.generation_contracts import OUTPUT_BYTES, canonical_json, result_exit_code, sha256
from clink.models import ResolvedGenerationNative
from clink.native_generation import NativeGenerationService, compose_native_prompt
from clink.native_generation_contracts import parse_native_request
from clink.native_generation_policy import (
    NativeGenerationAdmission,
    native_generation_arguments,
    native_generation_recipe,
    native_generation_recipe_digest,
)
from clink.native_policy import native_recipe_digest
from clink.parsers.base import ParsedCLIResponse
from clink.parsers.codex_native_generation import parser_contract_digest
from clink.policy import executable_digest, recipe_digest
from clink.runner import ProcessPool


def request(project, **changes):
    return parse_native_request(
        canonical_json(
            {
                "schema_version": 2,
                "execution_mode": "native",
                "project_dir": str(project),
                "request_id": "66f96fc9-969d-4c0e-ab92-e355fa0e7588",
                "cli": "codex",
                "model": "gpt-6.1-sol",
                "instructions": "PRIVATE INSTRUCTIONS {braces}\r\n",
                "input": "  PRIVATE SOURCE café 🦉\n",
                "timeout_seconds": 10,
                **changes,
            }
        )
    )


def events():
    return [
        {"type": "thread.started", "thread_id": "66f96fc9-969d-4c0e-ab92-e355fa0e7588"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "message0", "type": "agent_message", "text": "PRIVATE COMMENTARY"}},
        {
            "type": "item.completed",
            "item": {"id": "message1", "type": "agent_message", "text": "DIFFERENT STREAM TEXT"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 2,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 3,
                "reasoning_output_tokens": 0,
            },
        },
    ]


@pytest.fixture
def native_factory(tmp_path, monkeypatch):
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    instances = []

    def make(*, text="  native final café 🦉\n", records=None, body=None, returncode=0, stderr="", pool=None):
        executable = tmp_path / f"synthetic-native-{len(instances)}"
        paths = tmp_path / f"private-resource-{len(instances)}"
        evidence = tmp_path / f"candidate-evidence-{len(instances)}.json"
        evidence.write_text('{"synthetic":true}')
        code = (
            body
            if body is not None
            else (
                "import sys\nfrom pathlib import Path\nsys.stdin.buffer.read()\n"
                + "final=Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
                + f"Path({str(paths)!r}).write_text(str(final))\n"
                + (f"final.write_bytes({text.encode()!r})\n" if text is not None else "")
                + "\n".join(
                    f"print({json.dumps(event, ensure_ascii=False)!r})"
                    for event in (events() if records is None else records)
                )
                + f"\nsys.stderr.write({stderr!r})\nsys.exit({returncode})\n"
            )
        )
        executable.write_text("#!" + sys.executable + "\n" + code)
        executable.chmod(0o700)
        client = SimpleNamespace(
            adapter="codex",
            executable=(str(executable),),
            config_args=(),
            env={},
            working_dir=tmp_path / "deliberately-unused",
            allowed_roots=(tmp_path,),
            allowed_roots_explicit=True,
            configuration_scope="explicit",
            generation_native=None,
        )
        registry = SimpleNamespace(project_dir=None, get_client=lambda _: client)
        admission = NativeGenerationAdmission(
            version="synthetic",
            verified=True,
            executable_sha256=executable_digest(str(executable)),
            recipe_sha256=native_generation_recipe_digest(),
            parser_sha256=parser_contract_digest(),
            evidence_path=str(evidence),
            evidence_sha256=sha256(evidence.read_bytes()),
        )
        service = NativeGenerationService(registry, pool, admission)
        instances.append(service)
        return service, client, executable, evidence, paths

    return make


@pytest.mark.asyncio
async def test_final_file_is_the_only_answer_and_native_identity_is_honest(native_factory, tmp_path, caplog):
    expected = "  exact final café🦉\r\n" + "x" * 25000
    service, _, _, _, paths = native_factory(text=expected)
    result = await service.run(request(tmp_path))
    assert result["status"] == "completed" and result["output"] == expected
    identity = result["execution"]
    assert identity["profile"] == "generation_native_v1" and identity["execution_mode"] == "native"
    assert identity["project_dir"] == str(tmp_path) and identity["side_effects_possible"] is True
    assert identity["permission_observability"] == "limited" and identity["fulfillment_verified"] is False
    assert identity["actual_model"] is None and identity["catalog_sha256"] is None
    assert identity["admission"] == "verified"
    assert not Path(paths.read_text()).parent.exists()
    assert not service.pool._children and service.pool.running == 0
    assert "PRIVATE" not in caplog.text and expected not in caplog.text


@pytest.mark.asyncio
async def test_explicit_unverified_native_admission_refuses_before_registry_or_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ClinkRegistry", lambda: pytest.fail("unverified read config"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("unverified launched"))
    result = await NativeGenerationService(admission=NativeGenerationAdmission()).run(request(tmp_path))
    assert result["error"]["code"] == "policy_unverified" and result["output"] is None
    assert result["execution"]["side_effects_possible"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"execution_mode": "advisory"}, {"cli": "claude"}, {"model": "auto"}])
async def test_direct_service_request_cannot_default_or_change_authority(native_factory, tmp_path, monkeypatch, change):
    service, _, _, _, _ = native_factory()
    candidate = replace(request(tmp_path), **change)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("implicit native authority launched")
    )
    result = await service.run(candidate)
    assert result["error"]["code"] == "invalid_request" and result["execution"]["side_effects_possible"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["executable_sha256", "recipe_sha256", "parser_sha256", "evidence_sha256"])
async def test_exact_native_admission_bindings_are_required(native_factory, tmp_path, monkeypatch, field):
    service, _, _, _, _ = native_factory()
    service.admission = replace(service.admission, **{field: "0" * 64})
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("mismatch launched"))
    result = await service.run(request(tmp_path))
    assert result["error"]["code"] == "policy_unverified"
    assert result["execution"]["side_effects_possible"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,explicit", [("bundled", False), ("bundled", True), ("project", True), ("unknown", True), ("explicit", False)]
)
async def test_independent_explicit_roots_provenance_required(native_factory, tmp_path, monkeypatch, scope, explicit):
    service, client, _, _, _ = native_factory()
    client.configuration_scope = scope
    client.allowed_roots_explicit = explicit
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("unapproved roots launched"))
    result = await service.run(request(tmp_path))
    assert result["error"]["code"] == "path_not_allowed"


@pytest.mark.asyncio
async def test_request_project_does_not_bind_clink_project_configuration(native_factory, tmp_path, monkeypatch):
    service, _, _, _, _ = native_factory()
    service.registry.project_dir = tmp_path
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("bound project launched"))
    result = await service.run(request(tmp_path))
    assert result["error"]["code"] == "invalid_config"


@pytest.mark.asyncio
async def test_replaced_frozen_root_cannot_authorize_a_different_directory(native_factory, tmp_path, monkeypatch):
    service, client, _, _, _ = native_factory()
    approved = tmp_path / "approved"
    approved.mkdir()
    unapproved = tmp_path / "unapproved"
    unapproved.mkdir()
    client.allowed_roots = (approved.resolve(strict=True),)
    candidate = request(unapproved)
    approved.rmdir()
    approved.symlink_to(unapproved, target_is_directory=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("redirected root launched"))

    result = await service.run(candidate)

    assert result["status"] == "failed" and result["error"]["code"] == "path_not_allowed"
    assert result["execution"]["side_effects_possible"] is False
    assert result["output"] is None and pool_clean(service.pool)


@pytest.mark.asyncio
@pytest.mark.parametrize("text,status", [("final answer", "completed"), ("x" * (OUTPUT_BYTES + 1), "incomplete")])
async def test_terminal_duration_includes_private_final_output_cleanup(
    native_factory, tmp_path, monkeypatch, text, status
):
    import clink.generation_contracts as contracts

    service, _, _, _, paths = native_factory(text=text)
    candidate = request(tmp_path)
    clock = [candidate.started_at]
    original = module.prepare_native_final_output

    @contextmanager
    def delayed_cleanup():
        try:
            with original() as final:
                yield final
        finally:
            clock[0] += 2.5

    monkeypatch.setattr(module, "prepare_native_final_output", delayed_cleanup)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(contracts, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    result = await service.run(candidate)

    assert result["status"] == status
    assert result["duration_ms"] == 2500
    assert not Path(paths.read_text()).parent.exists() and pool_clean(service.pool)


@pytest.mark.asyncio
async def test_native_model_and_authority_flags_are_preserved_without_catalog_injection(
    native_factory, tmp_path, monkeypatch
):
    service, client, _, _, _ = native_factory()
    captured = []
    spawn = asyncio.create_subprocess_exec

    async def observe(*args, **kwargs):
        captured.append((args, kwargs))
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    result = await service.run(request(tmp_path, model="native-account-model"))
    assert result["status"] == "completed" and len(captured) == 1
    argv, kwargs = captured[0]
    output_path = argv[argv.index("--output-last-message") + 1]
    assert argv == (
        client.executable[0],
        *native_generation_arguments(output_path),
        "--cd",
        str(tmp_path),
        "--model",
        "native-account-model",
        "-",
    )
    assert kwargs["cwd"] == str(tmp_path)
    assert not set(argv) & {
        "--no-daemon",
        "--ephemeral",
        "--disable",
        "-c",
        "--sandbox",
        "--ask-for-approval",
        "--ignore-rules",
        "--ignore-user-config",
        "--skip-git-repo-check",
    }
    assert "PRIVATE" not in " ".join(argv)


@pytest.mark.asyncio
async def test_environment_names_are_opt_in_and_values_are_never_logged(native_factory, tmp_path, monkeypatch, caplog):
    service, client, _, _, _ = native_factory()
    client.generation_native = ResolvedGenerationNative(
        inherit_env=("INTEGRATION_SECRET",), required_env=("INTEGRATION_SECRET",)
    )
    monkeypatch.setenv("INTEGRATION_SECRET", "private-integration-value")
    monkeypatch.setenv("UNRELATED_SECRET", "unrelated-value")
    observed = []
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        observed.append(kwargs["env"])
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    result = await service.run(request(tmp_path))
    assert result["status"] == "completed"
    assert observed[0]["INTEGRATION_SECRET"] == "private-integration-value"
    assert "UNRELATED_SECRET" not in observed[0] and "private-integration-value" not in caplog.text


@pytest.mark.asyncio
async def test_missing_required_environment_fails_before_launch(native_factory, tmp_path, monkeypatch):
    service, client, _, _, _ = native_factory()
    client.generation_native = ResolvedGenerationNative(
        inherit_env=("MISSING_NATIVE_ENV",), required_env=("MISSING_NATIVE_ENV",)
    )
    monkeypatch.delenv("MISSING_NATIVE_ENV", raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("missing env launched"))
    result = await service.run(request(tmp_path))
    assert result["error"]["code"] == "missing_environment" and result["execution"]["side_effects_possible"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["binary", "evidence", "admission", "project"])
async def test_queued_static_and_project_identities_are_rechecked(native_factory, tmp_path, monkeypatch, change):
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 10)
    project = tmp_path / "project"
    project.mkdir()
    service, _, executable, evidence, _ = native_factory(pool=pool)
    owner = asyncio.create_task(service.run(request(project)))
    while not pool._waiters:
        await asyncio.sleep(0)
    if change == "binary":
        executable.write_text(executable.read_text() + "\n# swapped\n")
    elif change == "evidence":
        evidence.write_text("changed")
    elif change == "admission":
        service.admission = replace(service.admission, version="changed")
    else:
        project.rename(tmp_path / "old-project")
        project.mkdir()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: pytest.fail("queued mismatch launched"))
    pool._release()
    result = await owner
    assert result["status"] == "failed" and result["output"] is None
    assert result["execution"]["side_effects_possible"] is False
    assert pool.running == 0 and not pool._waiters


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing_final", "no_terminal", "nonzero", "invalid_utf8"])
async def test_exit_or_json_alone_cannot_establish_final_completion(native_factory, tmp_path, kind):
    if kind == "missing_final":
        service, _, _, _, _ = native_factory(text=None)
    elif kind == "no_terminal":
        service, _, _, _, _ = native_factory(records=events()[:-1])
    elif kind == "nonzero":
        service, _, _, _, _ = native_factory(returncode=1)
    else:
        service, _, _, _, _ = native_factory(
            body="import sys\nfrom pathlib import Path\nsys.stdin.read()\nPath(sys.argv[sys.argv.index('--output-last-message')+1]).write_bytes(b'\\xff')\n"
            + "\n".join(f"print({json.dumps(e)!r})" for e in events())
        )
    result = await service.run(request(tmp_path))
    assert result["status"] == "failed" and result["output"] is None and result_exit_code(result) == 1
    assert result["execution"]["side_effects_possible"] is True


@pytest.mark.asyncio
async def test_no_stream_message_is_required_for_supported_final_file(native_factory, tmp_path):
    records = [event for event in events() if not event["type"].startswith("item.")]
    service, _, _, _, _ = native_factory(records=records, text="Native Plan fallback or dropped message")
    result = await service.run(request(tmp_path))
    assert result["status"] == "completed" and result["output"] == "Native Plan fallback or dropped message"


@pytest.mark.asyncio
async def test_recognized_native_refusal_is_not_success_even_with_final_file(native_factory, tmp_path):
    records = events()
    records.insert(
        2,
        {
            "type": "item.completed",
            "item": {
                "id": "command",
                "type": "command_execution",
                "command": "touch private",
                "aggregated_output": "approval required by policy, but AskForApproval is set to Never",
                "exit_code": -1,
                "status": "declined",
            },
        },
    )
    service, _, _, _, _ = native_factory(records=records)
    result = await service.run(request(tmp_path))
    assert result["status"] == "failed" and result["error"]["code"] == "permission_needed" and result["output"] is None


@pytest.mark.asyncio
async def test_recoverable_tool_failure_is_visible_without_returning_tool_body(native_factory, tmp_path):
    records = events()
    records.insert(
        2,
        {
            "type": "item.completed",
            "item": {
                "id": "command",
                "type": "command_execution",
                "command": "private command",
                "aggregated_output": "private tool body",
                "exit_code": 1,
                "status": "failed",
            },
        },
    )
    service, _, _, _, _ = native_factory(records=records)
    result = await service.run(request(tmp_path))
    assert result["status"] == "completed" and result["warnings"][0]["code"] == "native_tool_errors"
    assert "private tool body" not in json.dumps(result) and "private command" not in json.dumps(result)


@pytest.mark.asyncio
async def test_output_budget_keeps_valid_unicode_prefix_and_native_effect_flag(native_factory, tmp_path):
    text = "x" * (OUTPUT_BYTES - 1) + "🦉tail"
    service, _, _, _, _ = native_factory(text=text)
    result = await service.run(request(tmp_path))
    assert result["status"] == "incomplete" and result["truncated"] is True and result_exit_code(result) == 3
    assert result["output"] == "x" * (OUTPUT_BYTES - 1) and result["execution"]["side_effects_possible"] is True


@pytest.mark.asyncio
async def test_reported_model_mismatch_cannot_publish_final_text(native_factory, tmp_path, monkeypatch):
    service, _, _, _, _ = native_factory()
    monkeypatch.setattr(
        module.CodexNativeGenerationParser, "parse", lambda *_: ParsedCLIResponse("", {"actual_model": "another-model"})
    )
    result = await service.run(request(tmp_path))
    assert result["error"]["code"] == "unsupported_model" and result["output"] is None
    assert (
        result["execution"]["actual_model"] == "another-model" and result["execution"]["side_effects_possible"] is True
    )


@pytest.mark.asyncio
async def test_queue_cancellation_retains_known_no_launch_and_removes_private_file(native_factory, tmp_path):
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 10)
    service, _, _, _, _ = native_factory(pool=pool)
    owner = asyncio.create_task(service.run(request(tmp_path)))
    while not pool._waiters:
        await asyncio.sleep(0)
    owner.cancel()
    result = await owner
    assert result["status"] == "cancelled" and result["execution"]["side_effects_possible"] is False
    assert not pool._waiters and pool.running == 1
    pool._release()


@pytest.mark.asyncio
async def test_running_cancellation_awaits_pool_and_removes_final_file(native_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("clink.runner.CLEANUP_SECONDS", 0.1)
    marker = tmp_path / "started"
    service, _, _, _, _ = native_factory(
        body=f"import signal,sys,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nsys.stdin.read()\nPath({str(marker)!r}).write_text(sys.argv[sys.argv.index('--output-last-message')+1])\ntime.sleep(30)\n"
    )
    owner = asyncio.create_task(service.run(request(tmp_path)))
    while not marker.exists() or not marker.read_text():
        await asyncio.sleep(0.001)
    path = Path(marker.read_text())
    owner.cancel()
    result = await owner
    assert result["status"] == "cancelled" and result["execution"]["side_effects_possible"] is True, result
    assert not path.parent.exists() and pool_clean(service.pool)


@pytest.mark.asyncio
async def test_final_resource_cleanup_failure_is_explicit_and_never_publishes_success(
    native_factory, tmp_path, monkeypatch
):
    import clink.native_generation_files as files

    service, _, _, _, paths = native_factory()
    original_rmdir = files.os.rmdir

    def fail_owned_removal(path, *args, **kwargs):
        if Path(path).name.startswith("clink-native-generation-"):
            raise PermissionError("synthetic cleanup rejection")
        return original_rmdir(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(files.os, "rmdir", fail_owned_removal)
        result = await service.run(request(tmp_path))
    private_parent = Path(paths.read_text()).parent
    try:
        assert result["status"] == "failed" and result["error"]["code"] == "cleanup_unconfirmed"
        assert result["output"] is None and result["execution"]["side_effects_possible"] is True
        assert "synthetic cleanup rejection" not in json.dumps(result)
    finally:
        private_parent.rmdir()


@pytest.mark.asyncio
async def test_deadline_failure_after_launch_retains_possible_effects_and_no_partial_output(
    native_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("clink.runner.CLEANUP_SECONDS", 0.1)
    service, _, _, _, _ = native_factory(
        body="import signal,sys,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nsys.stdin.read()\nprint('PRIVATE OPERATIONAL STDOUT',flush=True)\ntime.sleep(30)\n"
    )
    candidate = request(tmp_path, timeout_seconds=1)
    candidate = replace(candidate, started_at=time.monotonic() - 0.8)
    result = await service.run(candidate)
    assert result["status"] == "timed_out" and result["error"]["code"] == "timeout"
    assert result["output"] is None and result["execution"]["side_effects_possible"] is True
    assert pool_clean(service.pool)


def pool_clean(pool):
    return not pool._children and not pool._waiters and pool.running == 0


def test_native_prompt_and_recipes_keep_profiles_independent(tmp_path):
    candidate = request(tmp_path)
    prompt = compose_native_prompt(candidate)
    assert json.loads(prompt.split(b"\n\n", 1)[1]) == {"instructions": candidate.instructions, "input": candidate.input}
    assert native_generation_recipe()["catalog"] == "native; no projected catalog"
    assert recipe_digest("codex") == "4bf9a7fe0491e12965d5c36dd7ccc9d197dd271cdf57df378c68fc1b9e0dae16"
    assert native_recipe_digest("codex") == "623312bcc2e1f45f831edc1eb4a77598d25460ff0f9a4ce52b0c6c3ec1fbb1ed"
