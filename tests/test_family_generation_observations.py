"""Exercise verifier observations with synthetic failures and local child processes only."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from clink.runner import ProcessPool
from scripts import verify_family_generation as verifier

PRIVATE = "PRIVATE_NATIVE_TEXT_PATH_PROMPT_CREDENTIAL"


@pytest.fixture
def cleanup(monkeypatch):
    production = AsyncMock()
    probe = Mock(side_effect=ProcessLookupError)
    monkeypatch.setattr(ProcessPool, "_cleanup", production)
    monkeypatch.setattr(verifier.os, "killpg", probe)
    return production, probe, SimpleNamespace(pid=123)


def test_initial_and_started_observations_require_affirmative_cleanup(monkeypatch, cleanup):
    pool = verifier.ObservedPool()
    assert pool.cleanup_confirmed is False
    _, _, process = cleanup

    async def execute(_pool, _command, *, on_started):
        on_started()
        assert pool.cleanup_confirmed is False
        await pool._cleanup(process)
        assert pool.cleanup_confirmed is True
        on_started()
        assert pool.cleanup_confirmed is False

    monkeypatch.setattr(ProcessPool, "execute", execute)
    asyncio.run(pool.execute(("synthetic",)))


@pytest.mark.parametrize("failure_phase", ["production_cleanup", "process_group_probe"])
@pytest.mark.parametrize("failure_type", [RuntimeError, PermissionError, asyncio.CancelledError])
def test_cleanup_exception_is_preserved_and_uncertainty_survives_later_callbacks(cleanup, failure_phase, failure_type):
    production, probe, process = cleanup
    error = failure_type(PRIVATE)
    if failure_phase == "production_cleanup":
        production.side_effect = error
    else:
        probe.side_effect = error
    pool = verifier.ObservedPool()
    with pytest.raises(failure_type) as caught:
        asyncio.run(pool._cleanup(process))
    assert caught.value is error
    assert pool.cleanup_confirmed is False
    production.side_effect = None
    probe.side_effect = ProcessLookupError
    asyncio.run(pool._cleanup(process))
    assert pool.cleanup_confirmed is False
    assert pool.exception_observation == (
        None
        if failure_type is asyncio.CancelledError
        else {"exception_class": failure_type.__name__, "execution_phase": failure_phase}
    )
    assert PRIVATE not in json.dumps(pool.exception_observation)


def test_group_still_present_is_sticky_even_after_a_later_absence_probe(cleanup):
    _, probe, process = cleanup
    probe.side_effect = None
    pool = verifier.ObservedPool()
    asyncio.run(pool._cleanup(process))
    assert pool.cleanup_confirmed is False
    probe.side_effect = ProcessLookupError
    asyncio.run(pool._cleanup(process))
    assert pool.cleanup_confirmed is False
    assert pool.exception_observation is None


def test_successful_repeated_cleanup_does_not_imply_success_after_a_later_failure(cleanup):
    production, _, process = cleanup
    pool = verifier.ObservedPool()
    asyncio.run(pool._cleanup(process))
    asyncio.run(pool._cleanup(process))
    assert pool.cleanup_confirmed is True
    production.side_effect = ValueError(PRIVATE)
    with pytest.raises(ValueError):
        asyncio.run(pool._cleanup(process))
    assert pool.cleanup_confirmed is False


@pytest.mark.parametrize("when", ["before_spawn", "before_cleanup", "during_cleanup", "after_cleanup"])
def test_execute_retains_only_first_bounded_class_and_phase_and_rethrows(monkeypatch, cleanup, when):
    production, _, process = cleanup
    error = RuntimeError(PRIVATE)
    pool = verifier.ObservedPool()

    async def execute(_pool, _command, *, on_started):
        if when == "before_spawn":
            raise error
        on_started()
        if when == "before_cleanup":
            raise error
        if when == "during_cleanup":
            production.side_effect = error
        await pool._cleanup(process)
        raise error

    monkeypatch.setattr(ProcessPool, "execute", execute)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(pool.execute(("synthetic",)))
    assert caught.value is error
    assert pool.cleanup_confirmed is (when == "after_cleanup")
    assert pool.exception_observation == {
        "exception_class": "RuntimeError",
        "execution_phase": "production_cleanup" if when == "during_cleanup" else "pool_execute",
    }
    assert PRIVATE not in json.dumps(pool.exception_observation)


def test_verifier_input_observation_failures_are_captured_without_changing_dispatch_rules():
    pool = verifier.ObservedPool()
    with pytest.raises(IndexError):
        asyncio.run(pool.execute(("synthetic", "--prompt-file")))
    assert pool.dispatches == 1
    assert pool.spawns == 0
    assert pool.cleanup_confirmed is False
    assert pool.exception_observation == {"exception_class": "IndexError", "execution_phase": "pool_execute"}
    with pytest.raises(RuntimeError, match="one top-level"):
        asyncio.run(pool.execute(("synthetic",)))
    assert pool.dispatches == 1
    assert pool.exception_observation["exception_class"] == "IndexError"


@pytest.mark.parametrize("capture_failure", [RuntimeError, asyncio.CancelledError])
def test_failed_diagnostic_capture_cannot_mask_or_retry_the_original_exception(monkeypatch, cleanup, capture_failure):
    production, _, process = cleanup
    original = ValueError(PRIVATE)
    production.side_effect = original
    diagnostic = Mock(side_effect=capture_failure(PRIVATE))
    monkeypatch.setattr(verifier, "exception_diagnostic", diagnostic)
    pool = verifier.ObservedPool()

    async def execute(_pool, _command, *, on_started):
        on_started()
        await pool._cleanup(process)

    monkeypatch.setattr(ProcessPool, "execute", execute)
    with pytest.raises(ValueError) as caught:
        asyncio.run(pool.execute(("synthetic",)))
    assert caught.value is original
    assert pool.cleanup_confirmed is False
    assert pool.exception_observation is None
    assert pool.exception_diagnostic_capture_failed is True
    diagnostic.assert_called_once_with(original)


def test_custom_exception_class_names_are_unknown_identifiers(monkeypatch):
    error = type(PRIVATE, (Exception,), {})(PRIVATE)

    async def execute(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(ProcessPool, "execute", execute)
    pool = verifier.ObservedPool()
    with pytest.raises(Exception) as caught:
        asyncio.run(pool.execute(("synthetic",)))
    assert caught.value is error
    assert pool.exception_observation == {"exception_class": "unknown", "execution_phase": "pool_execute"}
    assert PRIVATE not in json.dumps(pool.exception_observation)


def test_run_check_exports_safe_pool_metadata_without_crediting_a_failed_gate(tmp_path, monkeypatch, cleanup):
    production, _, process = cleanup
    production.side_effect = RuntimeError(PRIVATE)
    pool = verifier.ObservedPool()
    monkeypatch.setattr(verifier, "ObservedPool", lambda: pool)
    monkeypatch.setattr(verifier.fixtures, "fixture_intact", lambda *_: True)

    async def execute(_pool, _command, *, on_started):
        on_started()
        await pool._cleanup(process)

    async def run(_request):
        try:
            await pool.execute(("synthetic",))
        except RuntimeError:
            return {"status": "failed", "error": {"code": "cleanup_unconfirmed"}}

    monkeypatch.setattr(ProcessPool, "execute", execute)
    monkeypatch.setattr(verifier, "candidate_service", lambda *_: SimpleNamespace(run=run))
    observation = asyncio.run(
        verifier.run_check("agy", "text", "synthetic", tmp_path, {}, tmp_path / "preflight", 1, (tmp_path, {}))
    )
    assert observation["passed"] is False
    assert observation["process_groups_absent_after_cleanup"] is False
    assert observation["pool_exception_observation"] == {
        "exception_class": "RuntimeError",
        "execution_phase": "production_cleanup",
    }
    assert observation["pool_exception_diagnostic_capture_failed"] is False
    assert PRIVATE not in json.dumps(observation)


@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize(
    "diagnostic_mode", ["off", "on", "capture_failure", "publication_failure", "publication_collision"]
)
def test_cleanup_origin_exception_cannot_pass_real_service_cancel_gate(
    tmp_path, monkeypatch, failure_type, diagnostic_mode
):
    from clink.native_generation import NativeGenerationService
    from clink.native_generation_families import family_generation_parser_digest, family_generation_recipe_digest

    sidecar_path = tmp_path / "cleanup-diagnostics.json"
    diagnostics = []
    actual_request_ids = []
    actual_results = []
    previous_sidecar = None
    if diagnostic_mode != "off":
        constructor = verifier.CleanupDiagnostics
        if diagnostic_mode == "publication_collision":
            previous = constructor("35f12c46-a815-4379-b5b8-f66373f60194", "1" * 64, identity_reader=lambda _: None)
            previous_sidecar = verifier.canonical_json(previous.sidecar({"status": "cancelled", "output": None}))
            sidecar_path.write_bytes(previous_sidecar)
            sidecar_path.chmod(0o444)

        def create_diagnostics(run_id, executable_sha256):
            reader = (
                Mock(side_effect=PermissionError(PRIVATE)) if diagnostic_mode == "capture_failure" else lambda _: None
            )
            item = constructor(run_id, executable_sha256, identity_reader=reader)
            diagnostics.append(item)
            return item

        monkeypatch.setattr(verifier, "CleanupDiagnostics", create_diagnostics)
        if diagnostic_mode == "publication_failure":
            monkeypatch.setattr(verifier, "persist", Mock(side_effect=PermissionError(PRIVATE)))
    original_candidate = verifier.candidate_service

    def candidate(*args):
        service = original_candidate(*args)
        original_run = service.run

        async def run(request):
            actual_request_ids.append(request.request_id)
            result = await original_run(request)
            actual_results.append(result)
            return result

        service.run = run
        return service

    monkeypatch.setattr(verifier, "candidate_service", candidate)
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    preflight = tmp_path / "preflight.json"
    preflight.write_text("Synthetic verifier preflight.\n")
    binding = {
        "observed_version": "synthetic",
        "executable_sha256": "1" * 64,
        "recipe_sha256": family_generation_recipe_digest("agy"),
        "parser_sha256": family_generation_parser_digest("agy"),
    }
    monkeypatch.setattr(NativeGenerationService, "_verify_admission", lambda *_args, **_kwargs: "1" * 64)
    state = {"started": False}

    async def wait():
        await asyncio.Event().wait()

    process = Mock(pid=123)
    process.wait = wait
    process.stdin.drain = AsyncMock()
    process.stdout.read = process.stderr.read = AsyncMock(return_value=b"")

    async def spawn(*_args, **_kwargs):
        state["started"] = True
        return process

    monkeypatch.setattr(verifier.asyncio, "create_subprocess_exec", spawn)
    production_cleanup = AsyncMock(side_effect=failure_type(PRIVATE))
    monkeypatch.setattr(ProcessPool, "_cleanup", production_cleanup)
    probe = Mock(side_effect=AssertionError("The failed cleanup must not reach the absence probe"))
    monkeypatch.setattr(verifier.os, "killpg", probe)
    exists = type(tmp_path).exists

    def marker_exists(path):
        if path in {tmp_path / "mutation.txt", tmp_path / "child-ready.txt"}:
            return state["started"]
        return exists(path)

    monkeypatch.setattr(type(tmp_path), "exists", marker_exists)
    monkeypatch.setattr(verifier.fixtures, "fixture_child_identity", lambda _: (123, "synthetic"))
    monkeypatch.setattr(verifier.fixtures, "descendant_status", lambda _: (True, False))
    monkeypatch.setattr(verifier.fixtures, "fixture_intact", lambda *_: True)
    observation = asyncio.run(
        verifier.run_check(
            "agy",
            "cancel",
            sys.executable,
            tmp_path,
            binding,
            preflight,
            20,
            (tmp_path, {}),
            **({"diagnostic_sidecar": sidecar_path} if diagnostic_mode != "off" else {}),
        )
    )
    assert observation["passed"] is False
    assert observation["identity_bound"] is True
    assert observation["dispatch_count"] == observation["spawn_count"] == 1
    assert all(observation["observations"].values())
    assert observation["process_groups_absent_after_cleanup"] is False
    assert observation["pool_running_after_shutdown"] == 0
    assert observation["terminal_status"] == ("cancelled" if failure_type is asyncio.CancelledError else "failed")
    assert observation["error_code"] == (
        "cancelled" if failure_type is asyncio.CancelledError else "cleanup_unconfirmed"
    )
    assert observation["pool_exception_observation"] == (
        None
        if failure_type is asyncio.CancelledError
        else {"exception_class": "RuntimeError", "execution_phase": "production_cleanup"}
    )
    assert observation["output_bytes"] == 0 and observation["output_sha256"] is None
    production_cleanup.assert_awaited_once_with(process)
    probe.assert_not_called()
    assert PRIVATE not in json.dumps(observation)
    assert len(actual_request_ids) == len(actual_results) == 1
    if diagnostic_mode == "off":
        assert "diagnostic_publication" not in observation
    else:
        published = diagnostic_mode in {"on", "capture_failure"}
        assert observation["diagnostic_publication"] == {
            "published": published,
            "run_id": actual_request_ids[0],
            "result_sha256": verifier.digest(verifier.canonical_json(actual_results[0])),
            "sidecar_sha256": verifier.digest(sidecar_path.read_bytes()) if published else None,
        }
    if diagnostic_mode in {"on", "capture_failure"}:
        sidecar = json.loads(sidecar_path.read_text())
        assert verifier.valid_cleanup_diagnostic_sidecar(sidecar)
        assert sidecar["result_sha256"] == verifier.digest(verifier.canonical_json(actual_results[0]))
        assert sidecar["run_id"] == diagnostics[0].run_id
        assert sidecar["capture_failed"] is (diagnostic_mode == "capture_failure")
        assert PRIVATE not in json.dumps(sidecar)
        assert sidecar_path.stat().st_mode & 0o777 == 0o444
    elif diagnostic_mode == "publication_collision":
        assert sidecar_path.read_bytes() == previous_sidecar
        previous = json.loads(previous_sidecar)
        assert verifier.valid_cleanup_diagnostic_sidecar(previous)
        assert previous["run_id"] != actual_request_ids[0]
        assert previous["result_sha256"] != observation["diagnostic_publication"]["result_sha256"]
        assert diagnostics[0].capture_failed is True
    else:
        assert not sidecar_path.exists()
        if diagnostic_mode == "publication_failure":
            assert diagnostics[0].capture_failed is True


@pytest.mark.skipif(os.name != "posix", reason="Production process supervision requires POSIX")
def test_repeated_caller_cancellation_still_waits_for_production_cleanup(tmp_path, monkeypatch):
    async def exercise():
        pool = verifier.ObservedPool()
        started = asyncio.Event()
        cleaning = asyncio.Event()
        finish_cleanup = asyncio.Event()
        production_cleanup = ProcessPool._cleanup
        processes = []

        async def cleanup(_pool, process):
            processes.append(process)
            cleaning.set()
            await finish_cleanup.wait()
            await production_cleanup(_pool, process)

        monkeypatch.setattr(ProcessPool, "_cleanup", cleanup)
        task = asyncio.create_task(
            pool.execute(
                (sys.executable, "-c", "import time; time.sleep(60)"),
                content=b"",
                cwd=str(tmp_path),
                env={},
                deadline=time.monotonic() + 30,
                request_id="synthetic-observer-cancellation",
                on_started=started.set,
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 5)
            assert pool.cleanup_confirmed is False
            task.cancel()
            await asyncio.wait_for(cleaning.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert pool.cleanup_confirmed is False and not task.done()
        finally:
            finish_cleanup.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await pool.shutdown()
        assert task.cancelled()
        assert pool.cleanup_confirmed is True
        assert pool.exception_observation is None
        assert pool.running == 0 and not pool._children
        with pytest.raises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)

    asyncio.run(exercise())


@pytest.mark.parametrize("capture_failure", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("original_type", [PermissionError, asyncio.CancelledError])
def test_optional_diagnostics_cannot_replace_cleanup_exception(monkeypatch, capture_failure, original_type):
    process = Mock(pid=123)
    original = original_type(PRIVATE)
    cleanup = AsyncMock(side_effect=original)
    monkeypatch.setattr(ProcessPool, "_cleanup", cleanup)
    diagnostics = verifier.CleanupDiagnostics("cd4ff848-7c21-4da6-9b35-69f592f617de", "1" * 64)
    monkeypatch.setattr(diagnostics, "cleanup_entry", Mock(side_effect=capture_failure(PRIVATE)))
    monkeypatch.setattr(diagnostics, "observe_exception", Mock(side_effect=capture_failure(PRIVATE)))
    pool = verifier.DiagnosticObservedPool(diagnostics)
    with pytest.raises(original_type) as caught:
        asyncio.run(pool._cleanup(process))
    assert caught.value is original
    assert diagnostics.capture_failed is True
    assert pool.cleanup_confirmed is False and pool._cleanup_uncertain is True
    assert pool.exception_observation == (
        None
        if original_type is asyncio.CancelledError
        else {"exception_class": "PermissionError", "execution_phase": "production_cleanup"}
    )
    cleanup.assert_awaited_once_with(process)
    assert PRIVATE not in json.dumps(diagnostics.sidecar({"status": "failed", "output": None}))


def test_optional_diagnostic_start_capture_failure_preserves_callback_and_original_failure(monkeypatch):
    process = Mock(pid=123)
    original = RuntimeError(PRIVATE)
    diagnostics = verifier.CleanupDiagnostics("cd4ff848-7c21-4da6-9b35-69f592f617de", "1" * 64)
    monkeypatch.setattr(diagnostics, "observe_leader", Mock(side_effect=asyncio.CancelledError(PRIVATE)))
    pool = verifier.DiagnosticObservedPool(diagnostics)
    callback = Mock()

    async def execute(_pool, _command, *, on_started):
        pool._children.add(process)
        on_started()
        raise original

    monkeypatch.setattr(ProcessPool, "execute", execute)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(pool.execute(("synthetic",), on_started=callback))
    assert caught.value is original
    assert diagnostics.capture_failed is True
    assert pool.dispatches == pool.spawns == 1
    callback.assert_called_once_with()
    assert pool.exception_observation == {"exception_class": "RuntimeError", "execution_phase": "pool_execute"}
