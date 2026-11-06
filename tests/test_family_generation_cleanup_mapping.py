"""Exercise current cleanup mappings and separately pin the historical verifier's mappings."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from clink.contracts import BackendError
from clink.native_generation import NativeGenerationService
from clink.native_generation_families import family_generation_parser_digest, family_generation_recipe_digest
from clink.parsers.agy_native_generation import AgyNativeGenerationParser
from clink.parsers.grok_native_generation import GrokNativeGenerationParser
from clink.runner import ProcessPool

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Production process supervision requires POSIX")

HISTORICAL_COMMIT = "e9feea12fcdaafbdef91f570a924f166563409bc"
HISTORICAL_SOURCE_SHA256 = "60c9431477515168239ff265b01680fd0c3a43f4f5a17a9a1e9c51d25e74272b"
CHECKS = ("text", "action", "permission")
FINAL_TEXT = (
    "Mira owns the Atlas cache migration on Friday. Address exactly two risks: stale reads and delayed rollout. "
    "Prepare rollback steps, then run acceptance checks before completing the migration."
)


@pytest.fixture(
    scope="module",
    params=["current", pytest.param("historical", marks=pytest.mark.private_evidence)],
)
def verifier_source(request):
    root = Path(__file__).resolve().parents[1]
    if request.param == "current":
        from scripts import verify_family_generation

        return verify_family_generation, False
    archive = request.getfixturevalue("private_evidence_root")
    source = (archive / "historical-sources" / HISTORICAL_COMMIT / "scripts/verify_family_generation.py").read_bytes()
    assert hashlib.sha256(source).hexdigest() == HISTORICAL_SOURCE_SHA256
    module = ModuleType("historical_family_generation_cleanup_mapping")
    module.__file__ = str(root / "scripts/verify_family_generation.py")
    exec(compile(source, module.__file__, "exec", dont_inherit=True), module.__dict__)
    return module, True


@pytest.fixture(params=["grok", "agy"])
def runtime(tmp_path, monkeypatch, verifier_source, request):
    """Stub process/fixture boundaries while retaining real execution, parsing, and shutdown."""
    verifier, historical = verifier_source
    family = request.param
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    preflight = tmp_path / "preflight.json"
    preflight.write_text("Synthetic historical verifier preflight.\n")
    (tmp_path / "arithmetic.py").write_text("def add(a, b):\n    return a + b\n")
    binding = {
        "observed_version": "synthetic",
        "executable_sha256": "1" * 64,
        "recipe_sha256": family_generation_recipe_digest(family),
        "parser_sha256": family_generation_parser_digest(family),
    }
    monkeypatch.setattr(NativeGenerationService, "_verify_admission", lambda *_args, **_kwargs: "1" * 64)
    monkeypatch.setattr(verifier.fixtures, "fixture_intact", lambda *_args: True)
    monkeypatch.setattr(verifier.fixtures, "fixture_test", lambda *_args: True)
    monkeypatch.setattr(
        verifier.fixtures, "agy_fixture_observations", lambda *_args: {"permission_hook_observed": True}
    )
    state = SimpleNamespace(
        events=[], pool=None, service=None, failure=None, phase=None, hold=None, group_present=False, private_input=None
    )
    process = Mock(pid=123)
    process.stdin.drain = AsyncMock()
    process.wait = AsyncMock(return_value=0)
    original_candidate = verifier.candidate_service

    def candidate(*args):
        state.pool = args[-1]
        state.service = original_candidate(*args)
        return state.service

    monkeypatch.setattr(verifier, "candidate_service", candidate)

    def assert_private_input_present():
        if family == "grok":
            assert state.private_input is not None
            assert state.private_input.is_file() and not state.private_input.is_symlink()
            assert state.private_input.stat().st_mode & 0o777 == 0o600
            assert state.private_input.parent.stat().st_mode & 0o777 == 0o700

    async def spawn(*command, **_kwargs):
        if family == "grok":
            state.private_input = Path(command[command.index("--prompt-file") + 1])
            assert_private_input_present()
        state.events.append("spawn")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def cleanup(_pool, child):
        assert child is process
        assert_private_input_present()
        state.events.append("cleanup_entered")
        if state.hold is not None:
            await state.hold.wait()
        assert_private_input_present()
        if state.phase == "production_cleanup":
            raise state.failure
        state.events.append("cleanup_returned")

    monkeypatch.setattr(ProcessPool, "_cleanup", cleanup)

    def probe(pid, signum):
        assert (pid, signum) == (process.pid, 0)
        state.events.append("absence_probe")
        if state.phase == "process_group_probe":
            raise state.failure
        if not state.group_present:
            raise ProcessLookupError

    monkeypatch.setattr(verifier.os, "killpg", probe)
    parser_type = GrokNativeGenerationParser if family == "grok" else AgyNativeGenerationParser
    original_parse = parser_type.parse

    def parse(parser, stdout, stderr=""):
        state.events.append("parse")
        return original_parse(parser, stdout, stderr)

    monkeypatch.setattr(parser_type, "parse", parse)
    original_shutdown = ProcessPool.shutdown

    async def shutdown(pool):
        state.events.append("shutdown_entered")
        await original_shutdown(pool)
        state.events.append("shutdown_returned")

    monkeypatch.setattr(ProcessPool, "shutdown", shutdown)

    async def run(check, *, failure=None, phase=None, group_present=False, hold=None):
        state.failure, state.phase, state.group_present, state.hold = failure, phase, group_present, hold
        stderr = b""
        if family == "grok":
            payload = json.dumps(
                {
                    "text": FINAL_TEXT,
                    "sessionId": "66f96fc9-969d-4c0e-ab92-e355fa0e7588",
                    "stopReason": "end_turn",
                }
            ).encode()
            if check == "permission":
                stderr = b"permission required: write_file was not run, as a headless run cannot ask for approval\n"
        else:
            terminal = {"conversation_id": "synthetic-1", "status": "SUCCESS", "response": FINAL_TEXT}
            if check == "permission":
                terminal["denied_actions"] = [{"display_name": "write_file", "action": "write"}]
            payload = "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "init", "conversation_id": "synthetic-1", "init": {}},
                    {"event": "result", "result": terminal},
                )
            ).encode()
        process.stdout.read = AsyncMock(side_effect=[payload, b""])
        process.stderr.read = AsyncMock(side_effect=[stderr, b""])
        observation = await verifier.run_check(
            family, check, sys.executable, tmp_path, binding, preflight, 20, (tmp_path, {})
        )
        assert isinstance(state.service, NativeGenerationService)
        assert isinstance(state.pool, ProcessPool)
        assert observation["identity_bound"] is True
        assert observation["dispatch_count"] == observation["spawn_count"] == 1
        assert observation["pool_running_after_shutdown"] == 0
        assert not state.pool._children and not state.pool._executions
        assert all(value for name, value in observation["observations"].items() if name != "useful_transformation")
        assert observation["private_input_removed"] is True
        if family == "grok":
            # Real private input must outlive supervision even when the finalizer raises:
            # e9 clink/native_generation_families.py:133-194 and native_generation.py:283-350.
            assert state.pool.private_input_modes is True
            assert state.pool.private_input == state.private_input
            assert not state.private_input.exists() and not state.private_input.parent.exists()
            process.stdin.write.assert_called_once_with(b"")
        else:
            assert state.private_input is None
            content = process.stdin.write.call_args.args[0]
            assert len(content.splitlines()) == 1 and json.loads(content)["event"] == "user"
        return observation

    return SimpleNamespace(run=run, state=state, historical=historical)


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("phase", ["production_cleanup", "process_group_probe"])
@pytest.mark.parametrize("failure_type", [RuntimeError, OSError, asyncio.CancelledError])
def test_historical_noncancel_gates_reject_finalizer_exceptions(runtime, check, phase, failure_type):
    observation = asyncio.run(runtime.run(check, failure=failure_type("synthetic failure"), phase=phase))
    # e9 clink/runner.py:249-275 runs the finalizer before execute returns, even after output is ready.
    # e9 clink/native_generation.py:351-356 maps ordinary supervision errors to cleanup_unconfirmed;
    # :400-414 maps cleanup-origin CancelledError to cancelled, never completed/permission_needed.
    cancelled = failure_type is asyncio.CancelledError
    assert observation["terminal_status"] == ("cancelled" if cancelled else "failed")
    assert observation["error_code"] == ("cancelled" if cancelled else "cleanup_unconfirmed")
    assert observation["passed"] is False
    assert observation["output_bytes"] == 0
    assert "parse" not in runtime.state.events
    assert runtime.state.events.count("cleanup_entered") == 1
    assert runtime.state.events.count("absence_probe") == (phase == "process_group_probe")
    assert runtime.state.events.count("cleanup_returned") == (phase == "process_group_probe")
    assert runtime.state.events[-2:] == ["shutdown_entered", "shutdown_returned"]
    # e9 scripts/verify_family_generation.py:563,588-594 leaves the flag true when either await/probe raises.
    # Shutdown bookkeeping cannot repair the already-failed terminal classification.
    # The current observer records uncertain cleanup as false for either exception phase.
    assert observation["process_groups_absent_after_cleanup"] is runtime.historical


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("code", ["cancelled", "timeout", "permission_needed"])
def test_historical_backend_codes_are_synthetic_counterexamples_not_ordinary_cleanup_errors(runtime, check, code):
    observation = asyncio.run(
        runtime.run(check, failure=BackendError(code, "synthetic coded cleanup failure"), phase="production_cleanup")
    )
    # e9 clink/runner.py:126-137 does not construct BackendError. This injection tests the explicit
    # exception-class boundary, not an unmodified production-reachable permission failure.
    # e9 clink/native_generation.py:351-352 preserves BackendError, and :406-414 preserves its code.
    assert observation["terminal_status"] == {"cancelled": "cancelled", "timeout": "timed_out"}.get(code, "failed")
    assert observation["error_code"] == code
    assert observation["passed"] is (runtime.historical and check == "permission" and code == "permission_needed")
    assert observation["process_groups_absent_after_cleanup"] is runtime.historical
    assert "cleanup_returned" not in runtime.state.events
    assert "absence_probe" not in runtime.state.events
    assert "parse" not in runtime.state.events


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("group_present", [False, True])
def test_historical_qualifying_terminal_follows_cleanup_and_probe(runtime, check, group_present):
    observation = asyncio.run(runtime.run(check, group_present=group_present))
    # e9 clink/native_generation.py:357-397 parses/publishes only after execute and its finalizer return.
    # Real permission mappings are clink/parsers/agy_native_generation.py:157-168 and
    # clink/parsers/grok_native_generation.py:112-113 in e9.
    assert observation["terminal_status"] == ("failed" if check == "permission" else "completed")
    assert observation["error_code"] == ("permission_needed" if check == "permission" else None)
    assert observation["passed"] is (not group_present)
    assert observation["process_groups_absent_after_cleanup"] is (not group_present)
    assert runtime.state.events == [
        "spawn",
        "cleanup_entered",
        "cleanup_returned",
        "absence_probe",
        "parse",
        "shutdown_entered",
        "shutdown_returned",
    ]


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("probe_fails", [False, True])
def test_shutdown_waits_for_finalizer_and_cannot_publish_noncancel_output(runtime, check, probe_fails):
    async def exercise():
        release = asyncio.Event()
        task = asyncio.create_task(
            runtime.run(
                check,
                failure=OSError("synthetic probe failure") if probe_fails else None,
                phase="process_group_probe" if probe_fails else None,
                hold=release,
            )
        )
        shutdown = None

        async def wait_for_cleanup():
            while "cleanup_entered" not in runtime.state.events:
                await asyncio.sleep(0)

        try:
            await asyncio.wait_for(wait_for_cleanup(), 2)
            assert not task.done() and "parse" not in runtime.state.events
            shutdown = asyncio.create_task(runtime.state.pool.shutdown())
            await asyncio.sleep(0)
            assert "shutdown_entered" in runtime.state.events
            assert not shutdown.done() and not task.done()
        finally:
            release.set()
            if shutdown is not None:
                await asyncio.wait_for(shutdown, 2)
        observation = await asyncio.wait_for(task, 2)
        # e9 clink/runner.py:263-287 shields finalization; :289-303 shutdown awaits its owner.
        # Missing affirmative evidence through an ordinary probe exception stays a failed result;
        # successful cleanup after shutdown cancellation stays cancelled, never a noncancel gate.
        assert observation["terminal_status"] == ("failed" if probe_fails else "cancelled")
        assert observation["error_code"] == ("cleanup_unconfirmed" if probe_fails else "cancelled")
        assert observation["passed"] is False
        assert "parse" not in runtime.state.events
        assert runtime.state.events.count("cleanup_entered") == 1
        assert runtime.state.events.count("cleanup_returned") == 1
        assert runtime.state.events.count("absence_probe") == 1

    asyncio.run(exercise())
