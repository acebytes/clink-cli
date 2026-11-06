"""Separate admission, single-attempt authority and bounded lifecycle using synthetic executables."""

import asyncio
import hashlib
import json
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

import clink.generation as module
from clink.generation import GenerationService
from clink.generation_contracts import OUTPUT_BYTES, canonical_json, parse_request, result_exit_code
from clink.generation_policy import (
    CATALOG_SHA256,
    RUNTIME_BLOCKERS,
    GenerationAdmission,
    generation_arguments,
    generation_catalog_path,
    generation_recipe,
    generation_recipe_digest,
)
from clink.parsers.codex_generation import parser_contract_digest
from clink.policy import executable_digest, recipe_digest
from clink.runner import ProcessPool

REQUEST = {
    "schema_version": 1,
    "request_id": "66f96fc9-969d-4c0e-ab92-e355fa0e7588",
    "cli": "codex",
    "model": "gpt-6.1-sol",
    "instructions": "Improve {braces}.\r\n",
    "input": "  café \\path\n",
    "timeout_seconds": 10,
}


def request(**changes):
    return parse_request(canonical_json({**REQUEST, **changes}))


def events(text="  transformed café\n", model=None):
    terminal = {"type": "turn.completed", "usage": {"input_tokens": 2, "cached_input_tokens": 0, "output_tokens": 3}}
    if model is not None:
        terminal["model"] = model
    return [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}},
        terminal,
    ]


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    instances = []

    def create(*, body=None, records=None, returncode=0, stderr="", pool=None, allow_synthetic_runtime=True):
        if allow_synthetic_runtime:
            # Exercise data/lifecycle contracts with a synthetic executable only.
            # This local test patch supplies no production capability/admission proof.
            monkeypatch.setattr(GenerationService, "_require_runtime_ceiling", staticmethod(lambda: None))
        executable = tmp_path / f"fake-codex-{len(instances)}"
        code = (
            body
            if body is not None
            else (
                "import json,sys\nsys.stdin.read()\n"
                + "\n".join(
                    "print(" + repr(json.dumps(event, ensure_ascii=False)) + ")" for event in (records or events())
                )
                + "\nsys.stderr.write("
                + repr(stderr)
                + ")\nsys.exit("
                + str(returncode)
                + ")\n"
            )
        )
        executable.write_text("#!" + sys.executable + "\n" + code)
        executable.chmod(0o700)
        evidence = tmp_path / f"evidence-{len(instances)}.json"
        evidence.write_text('{"synthetic":true}\n')
        client = SimpleNamespace(
            adapter="codex",
            executable=(str(executable),),
            config_args=(),
            env={},
            working_dir=tmp_path,
            allowed_roots=(tmp_path,),
            roles={"default": "never use this role"},
            default_model="never-use-default-model",
        )
        registry = SimpleNamespace(get_client=lambda name: client)
        admission = GenerationAdmission(
            version="synthetic",
            verified=True,
            executable_sha256=executable_digest(str(executable)),
            recipe_sha256=generation_recipe_digest(),
            catalog_sha256=CATALOG_SHA256,
            parser_sha256=parser_contract_digest(),
            evidence_path=str(evidence),
            evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        )
        service = GenerationService(registry=registry, pool=pool, admission=admission)
        instances.append(service)
        return service, client, executable, evidence

    return create


@pytest.mark.asyncio
async def test_synthetic_exact_result_and_identity_without_legacy_output_cap(service_factory, caplog):
    text = "  é" + "🦉" * 6000 + "\n"
    service, _, _, _ = service_factory(records=events(text))
    result = await service.run(request())
    assert result["status"] == "completed" and result["output"] == text
    assert result["execution"]["actual_model"] is None
    assert result["execution"]["admission"] == "verified"
    assert result["execution"]["requested_model"] == result["execution"]["effective_model"] == "gpt-6.1-sol"
    assert result["request_sha256"] == request().request_sha256
    assert result["truncated"] is False and result["error"] is None
    assert service.pool.running == 0 and not service.pool._children
    for sentinel in (REQUEST["instructions"], REQUEST["input"], text, "never use this role"):
        assert sentinel not in caplog.text


@pytest.mark.asyncio
async def test_production_remains_fail_closed_without_registry_or_launch(monkeypatch):
    monkeypatch.setattr(
        module, "ClinkRegistry", lambda: pytest.fail("unverified generation read operator configuration")
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *args, **kw: pytest.fail("unverified generation launched")
    )
    result = await GenerationService().run(request())
    assert result["error"]["code"] == "policy_unverified"
    assert result["execution"]["admission"] == "unverified"
    assert result["output"] is None and result_exit_code(result) == 1


@pytest.mark.asyncio
async def test_verified_fake_admission_alone_cannot_bypass_trusted_recipe_blocker(service_factory, monkeypatch):
    service, _, _, _ = service_factory(allow_synthetic_runtime=False)
    assert service.admission.verified is True
    monkeypatch.setattr(
        service.registry, "get_client", lambda _: pytest.fail("blocked recipe read client configuration")
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kw: pytest.fail("blocked recipe launched"))
    result = await service.run(request())
    assert result["status"] == "failed" and result["error"]["code"] == "policy_unverified"
    assert "ordinary_mcp_runtime_ceiling_unavailable" in result["error"]["message"]
    assert result["execution"]["admission"] == "unverified" and result["output"] is None
    assert generation_recipe()["runtime_blockers"] == list(RUNTIME_BLOCKERS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["executable_sha256", "recipe_sha256", "catalog_sha256", "parser_sha256", "evidence_sha256"]
)
async def test_separate_binding_mismatch_refuses_every_launch(service_factory, monkeypatch, field):
    service, _, _, _ = service_factory()
    service.admission = replace(service.admission, **{field: "0" * 64})
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kw: pytest.fail("mismatch launched"))
    result = await service.run(request())
    assert result["error"]["code"] == "policy_unverified" and result["output"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["executable", "evidence", "admission"])
async def test_queued_binding_rechecked_immediately_before_launch(service_factory, monkeypatch, resource):
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 10)
    service, _, executable, evidence = service_factory(pool=pool)
    owner = asyncio.create_task(service.run(request()))
    while not pool._waiters:
        await asyncio.sleep(0)
    if resource == "executable":
        executable.write_text(executable.read_text() + "\n# changed\n")
    elif resource == "evidence":
        evidence.write_text('{"changed":true}')
    else:
        service.admission = replace(service.admission, version="changed")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *args, **kw: pytest.fail("queued mismatch launched"))
    pool._release()
    result = await owner
    assert result["error"]["code"] == "policy_unverified"
    assert result["execution"]["admission"] == "unverified"
    assert pool.running == 0 and not pool._waiters


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
async def test_replaced_frozen_root_blocks_generation(service_factory, tmp_path, monkeypatch, queued):
    pool = ProcessPool(1)
    service, client, _, _ = service_factory(pool=pool)
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    client.allowed_roots = (approved,)
    client.working_dir = approved
    if queued:
        await pool._acquire(time.monotonic() + 10)
        task = asyncio.create_task(service.run(request()))
        for _ in range(100):
            if pool._waiters or task.done():
                break
            await asyncio.sleep(0.001)
        assert pool._waiters
    approved.rmdir()
    approved.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **kw: pytest.fail("redirected root launched"))
    if queued:
        pool._release()
        result = await task
    else:
        result = await service.run(request())

    assert result["status"] == "failed" and result["error"]["code"] == "path_not_allowed"
    assert result["execution"]["admission"] == "unverified"
    assert pool.running == 0 and not pool._waiters and not pool._children


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"model": "auto"}, {"model": "invented-model"}])
async def test_model_must_be_explicit_catalog_entry(service_factory, monkeypatch, change):
    service, _, _, _ = service_factory()
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *args, **kw: pytest.fail("unsupported model launched")
    )
    candidate = request() if change["model"] == "auto" else request(**change)
    if change["model"] == "auto":
        candidate = replace(candidate, model="auto")
    result = await service.run(candidate)
    assert result["error"]["code"] == "unsupported_model"


@pytest.mark.asyncio
async def test_launch_uses_only_frozen_generation_args_environment_and_exact_input(service_factory, monkeypatch):
    service, client, _, _ = service_factory()
    observed = []
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        observed.append((args, kwargs))
        return await spawn(*args, **kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "credential-sentinel")
    monkeypatch.setenv("ARBITRARY_ENV", "unsafe-sentinel")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    result = await service.run(request())
    assert result["status"] == "completed" and len(observed) == 1
    argv, kwargs = observed[0]
    assert argv == (client.executable[0], *generation_arguments(rendered=True), "--model", REQUEST["model"], "-")
    assert "OPENAI_API_KEY" not in kwargs["env"] and "ARBITRARY_ENV" not in kwargs["env"]
    assert kwargs["env"]["CLINK_DELEGATION_DEPTH"] == "1"
    assert not any(REQUEST["input"] in arg or REQUEST["instructions"] in arg for arg in argv)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attribute,value",
    [
        ("adapter", "claude"),
        ("config_args", ("--search",)),
        ("executable", ("codex", "--search")),
        ("env", {"OPENAI_API_KEY": "sentinel"}),
    ],
)
async def test_operator_arguments_and_unapproved_env_cannot_weaken_profile(
    service_factory, monkeypatch, attribute, value
):
    service, client, _, _ = service_factory()
    setattr(client, attribute, value)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *args, **kw: pytest.fail("unsafe configuration launched")
    )
    result = await service.run(request())
    assert result["error"]["code"] == "invalid_config"


@pytest.mark.asyncio
async def test_reported_model_mismatch_fails_without_substitution(service_factory):
    service, _, _, _ = service_factory(records=events(model="gpt-6-luna"))
    result = await service.run(request())
    assert result["error"]["code"] == "unsupported_model" and result["output"] is None
    assert result["execution"]["actual_model"] == "gpt-6-luna"


@pytest.mark.asyncio
async def test_byte_truncation_keeps_unicode_prefix_and_never_claims_success(service_factory):
    text = "x" * (OUTPUT_BYTES - 1) + "😀rest"
    service, _, _, _ = service_factory(records=events(text))
    result = await service.run(request())
    assert result["status"] == "incomplete" and result["truncated"] is True
    assert result["output"] == "x" * (OUTPUT_BYTES - 1)
    assert result["error"]["code"] == "output_limit" and result_exit_code(result) == 3
    assert len(canonical_json(result)) < 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stderr,code",
    [
        ("not logged in credential-sentinel", "authentication_required"),
        ("rate limit exceeded\nRetry-After: 12\n", "rate_limited"),
        ("arbitrary private failure", "nonzero_exit"),
    ],
)
async def test_nonzero_never_returns_stdout_or_retries(service_factory, stderr, code):
    service, _, _, _ = service_factory(returncode=1, stderr=stderr)
    result = await service.run(request())
    assert result["error"]["code"] == code and result["output"] is None
    assert "private failure" not in json.dumps(result) and "credential-sentinel" not in json.dumps(result)
    if code == "rate_limited":
        assert result["error"]["retry_after_seconds"] == 12


@pytest.mark.asyncio
async def test_unexpected_tool_discards_all_operational_stdout(service_factory):
    records = events()
    records.insert(2, {"type": "item.completed", "item": {"type": "command_execution", "command": "private command"}})
    service, _, _, _ = service_factory(records=records)
    result = await service.run(request())
    assert result["error"]["code"] == "unexpected_tool_event" and result["output"] is None
    assert "private command" not in json.dumps(result)


@pytest.mark.asyncio
async def test_cancel_queued_owner_never_spawns_and_releases_queue(service_factory, monkeypatch):
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 10)
    service, _, _, _ = service_factory(pool=pool)
    owner = asyncio.create_task(service.run(request()))
    while not pool._waiters:
        await asyncio.sleep(0)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *args, **kw: pytest.fail("cancelled queue owner launched")
    )
    owner.cancel()
    result = await owner
    assert result["status"] == "cancelled" and result_exit_code(result) == 130
    assert not pool._waiters and pool.running == 1
    pool._release()
    assert pool.running == 0


@pytest.mark.asyncio
async def test_running_cancellation_awaits_shared_pool_cleanup(service_factory, monkeypatch):
    monkeypatch.setattr("clink.runner.CLEANUP_SECONDS", 0.1)
    service, _, _, _ = service_factory(
        body="import sys,time\nsys.stdin.read()\nprint('private unfinished output',flush=True)\ntime.sleep(30)\n"
    )
    owner = asyncio.create_task(service.run(request()))
    while not service.pool._children:
        await asyncio.sleep(0)
    children = tuple(service.pool._children)
    owner.cancel()
    result = await owner
    status = result.get("status")
    error = result.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    known_codes = {
        "cancelled",
        "cleanup_unconfirmed",
        "internal_error",
        "timeout",
        "invalid_config",
        "cli_missing",
        "policy_unverified",
        "path_not_allowed",
        "protocol_error",
        "nonzero_exit",
    }
    diagnostics = json.dumps(
        {
            "status": (
                status
                if isinstance(status, str) and status in {"cancelled", "failed", "timed_out", "incomplete", "completed"}
                else "unknown"
            ),
            "error_code": (
                code if isinstance(code, str) and code in known_codes else "absent" if error is None else "unknown"
            ),
            "output_is_none": result.get("output") is None,
            "captured_children": len(children),
            "all_captured_children_returned": all(child.returncode is not None for child in children),
            "child_return_codes": [
                child.returncode if type(child.returncode) is int and -(2**31) <= child.returncode < 2**31 else None
                for child in children[:8]
            ],
            "child_return_codes_truncated": len(children) > 8,
            "active_pool_children": len(service.pool._children),
            "running_slots": service.pool.running,
        },
        sort_keys=True,
    )
    assert result["status"] == "cancelled" and result["output"] is None, diagnostics
    assert all(child.returncode is not None for child in children), diagnostics
    assert not service.pool._children and service.pool.running == 0, diagnostics


@pytest.mark.asyncio
async def test_timeout_discards_operational_stdout_and_awaits_cleanup(service_factory, monkeypatch):
    monkeypatch.setattr("clink.runner.CLEANUP_SECONDS", 0.1)
    service, _, _, _ = service_factory(
        body="import sys,time\nsys.stdin.read()\nprint('private unfinished output',flush=True)\ntime.sleep(30)\n"
    )
    supervision_errors = []
    execute = service.pool.execute

    async def observed_execute(*args, **kwargs):
        try:
            return await execute(*args, **kwargs)
        except Exception as error:
            supervision_errors.append((type(error).__name__, str(error)[:512]))
            raise

    monkeypatch.setattr(service.pool, "execute", observed_execute)
    candidate = request(timeout_seconds=1)
    candidate = replace(candidate, started_at=time.monotonic() - 0.9)
    result = await service.run(candidate)
    assert result["status"] == "timed_out" and result_exit_code(result) == 124, (result, supervision_errors)
    assert result["output"] is None and not service.pool._children and service.pool.running == 0


@pytest.mark.asyncio
async def test_unknown_supervision_failure_reports_cleanup_uncertainty(service_factory, monkeypatch):
    service, _, _, _ = service_factory()

    async def failure(*args, **kwargs):
        raise OSError("private failure details")

    monkeypatch.setattr(service.pool, "execute", failure)
    result = await service.run(request())
    assert result["error"]["code"] == "cleanup_unconfirmed"
    assert result["output"] is None and "private failure" not in json.dumps(result)


def test_generation_projection_preserves_advisory_and_native_recipes():
    assert recipe_digest("codex") == "4bf9a7fe0491e12965d5c36dd7ccc9d197dd271cdf57df378c68fc1b9e0dae16"
    original = json.loads(generation_catalog_path().with_name("codex-0.166.0-models-direct.json").read_bytes())
    generated = json.loads(generation_catalog_path().read_bytes())
    allowed = {
        "tool_mode": "direct",
        "shell_type": "disabled",
        "apply_patch_tool_type": None,
        "experimental_supported_tools": [],
        "supports_search_tool": False,
    }
    for before, after in zip(original["models"], generated["models"], strict=True):
        assert after == {**before, **allowed}
    assert hashlib.sha256(generation_catalog_path().read_bytes()).hexdigest() == CATALOG_SHA256
