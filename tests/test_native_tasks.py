"""Native lifecycle identity, bounded state, and cleanup without native model calls."""

import asyncio
import json
from pathlib import Path

import pytest

from clink.contracts import AdapterPolicy, BackendError, BackendTarget, ClientSnapshot, RoleSnapshot
from clink.native_contracts import FrozenNativeContext, NativeRunOutcome
from clink.tasks import NativeTaskManager


class FakeRuntime:
    def __init__(self, project: Path):
        self.project = project
        self.prepared = []
        self.calls = []
        self.profile_id = "frozen-profile"
        self.outcomes = []
        self.entered = asyncio.Event()
        self.release = None
        self.cleanup_release = None
        self.cleaning = asyncio.Event()
        self.cancellations = 0
        self.start_child = True
        self.failure = None

    def prepare(self, cli_name, role, model, project_dir, files=(), images=(), absolute_deadline=None):
        target = BackendTarget(
            ClientSnapshot(cli_name, "claude", ("fake",), self.project, (self.project,)),
            RoleSnapshot(role or "default", "native expertise"),
            model,
            model or "default-model",
            Path(project_dir).resolve(strict=True),
            AdapterPolicy("claude", verified=True),
        )
        context = FrozenNativeContext(
            target,
            self.profile_id,
            tuple(files),
            tuple(images),
            absolute_deadline,
            ("HOME",),
            (),
        )
        self.prepared.append(context)
        return context

    async def run(self, context, prompt, session_id=None, *, attempt_id, started=None):
        self.calls.append((context, prompt, session_id, attempt_id))
        if self.start_child and started:
            started()
        self.entered.set()
        try:
            if self.failure is not None:
                raise self.failure
            if self.release is not None:
                await self.release.wait()
            if self.outcomes:
                return self.outcomes.pop(0)
            return NativeRunOutcome("completed", "agent report", session_id=session_id or attempt_id)
        except asyncio.CancelledError:
            self.cancellations += 1
            self.cleaning.set()
            if self.cleanup_release is not None:
                await self.cleanup_release.wait()
            raise


@pytest.fixture
def runtime(tmp_path):
    return FakeRuntime(tmp_path)


def start_args(runtime, **changes):
    return {
        "cli_name": "claude",
        "project_dir": str(runtime.project),
        "prompt": "private first input",
        "request_key": "start-key",
        **changes,
    }


async def settle(manager):
    if manager._jobs:
        await asyncio.gather(*tuple(manager._jobs.values()))


@pytest.mark.asyncio
async def test_start_returns_promptly_and_lost_response_deduplicates_before_reservation(runtime):
    runtime.release = asyncio.Event()
    manager = NativeTaskManager(runtime)
    first, duplicate = await asyncio.gather(manager.start(start_args(runtime)), manager.start(start_args(runtime)))
    assert first["state"] == "queued"
    assert duplicate["task_handle"] == first["task_handle"]
    assert duplicate["attempt_id"] == first["attempt_id"]
    assert duplicate["deduplicated"] is True
    assert len(runtime.prepared) == 1
    await runtime.entered.wait()
    assert len(runtime.calls) == 1
    assert (await manager.status(first["task_handle"]))["state"] == "running"
    with pytest.raises(BackendError, match="different native input") as conflict:
        await manager.start(start_args(runtime, prompt="other input"))
    assert conflict.value.code == "request_key_conflict"
    runtime.release.set()
    await settle(manager)
    repeated = await manager.start(start_args(runtime))
    assert repeated["state"] == "completed"
    assert repeated["result"]["content"] == "agent report"
    assert len(runtime.calls) == 1
    assert "private first input" not in repr(manager._records)
    assert "private first input" not in repr(manager._keys)


@pytest.mark.asyncio
async def test_followup_uses_only_new_input_and_private_session_with_distinct_attempt(runtime):
    manager = NativeTaskManager(runtime)
    original = start_args(runtime, absolute_file_paths=[str(runtime.project / "one.txt")], timeout_seconds=20)
    first = await manager.start(original, admitted_at=100)
    await settle(manager)
    follow = {
        "action": "followup",
        "task_handle": first["task_handle"],
        "prompt": "private new input",
        "request_key": "follow-key",
        "timeout_seconds": 30,
    }
    second = await manager.followup(follow, admitted_at=200)
    assert second["attempt_id"] != first["attempt_id"]
    assert second["state"] == "queued"
    old_delivery = await manager.start(original)
    assert old_delivery["attempt_id"] == first["attempt_id"]
    assert old_delivery["state"] == "completed"
    assert old_delivery["result_unavailable"] is True
    assert "result" not in old_delivery
    await settle(manager)
    context, prompt, native_id, _ = runtime.calls[-1]
    assert prompt == "private new input"
    assert context.files == ()
    assert context.deadline == 230
    assert runtime.calls[0][0].deadline == 120
    assert native_id == first["attempt_id"]
    public = await manager.status(first["task_handle"])
    assert public["execution_does_not_verify_task_fulfillment"] is True
    assert public["result"]["agent_reported_claims"] == "unverified"
    assert native_id not in json.dumps({key: value for key, value in public.items() if key != "attempt_id"})
    assert "session_id" not in json.dumps(public)
    redelivery = await manager.followup(follow)
    assert redelivery["attempt_id"] == second["attempt_id"]
    assert len(runtime.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"cli_name": "other"},
        {"role": "reviewer"},
        {"model": "other-model"},
        {"profile_id": "new-profile"},
    ],
)
async def test_followup_rejects_changed_identity(runtime, changed):
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await settle(manager)
    if "profile_id" in changed:
        runtime.profile_id = changed.pop("profile_id")
    with pytest.raises(BackendError) as caught:
        await manager.followup(
            {"task_handle": first["task_handle"], "prompt": "next", "request_key": "follow", **changed}
        )
    assert caught.value.code == "session_conflict"
    assert len(runtime.calls) == 1
    assert len(manager._keys) == 1


@pytest.mark.asyncio
async def test_unknown_handles_never_launch_or_recreate_and_missing_session_refuses_followup(runtime):
    manager = NativeTaskManager(runtime)
    handle = "nt_" + "0" * 32
    for action in (manager.status, manager.cancel):
        response = await action(handle)
        assert response["state"] == "unavailable"
        assert response["execution_outcome"] == "unknown"
    unknown_follow = await manager.followup({"task_handle": handle, "prompt": "new", "request_key": "unknown"})
    assert unknown_follow["execution_outcome"] == "unknown"
    assert not runtime.calls
    assert not runtime.prepared
    runtime.outcomes = [NativeRunOutcome("completed", "report", session_id=None)]
    first = await manager.start(start_args(runtime))
    await settle(manager)
    with pytest.raises(BackendError) as caught:
        await manager.followup({"task_handle": first["task_handle"], "prompt": "new", "request_key": "next"})
    assert caught.value.code == "session_unavailable"
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_project_ownership_rejects_canonical_overlap_but_allows_siblings(runtime, tmp_path):
    runtime.release = asyncio.Event()
    left = tmp_path / "left"
    nested = left / "nested"
    right = tmp_path / "right"
    nested.mkdir(parents=True)
    right.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(left, target_is_directory=True)
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime, project_dir=str(left)))
    for project in (nested, tmp_path, link):
        with pytest.raises(BackendError) as caught:
            await manager.start(start_args(runtime, project_dir=str(project), request_key="overlap"))
        assert caught.value.code == "project_busy"
    sibling = await manager.start(start_args(runtime, project_dir=str(right), request_key="sibling"))
    assert sibling["task_handle"] != first["task_handle"]
    assert len(manager._keys) == 2
    await manager.shutdown()
    assert not manager._leases


@pytest.mark.asyncio
async def test_concurrent_followups_admit_only_one_input(runtime):
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await settle(manager)
    runtime.release = asyncio.Event()
    args = {"task_handle": first["task_handle"], "prompt": "new", "request_key": "follow"}
    results = await asyncio.gather(
        manager.followup(args), manager.followup({**args, "request_key": "other"}), return_exceptions=True
    )
    assert sum(isinstance(value, BackendError) and value.code == "task_busy" for value in results) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancel_before_coroutine_starts_releases_lease_and_is_idempotent(runtime):
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    cancelled = await manager.cancel(first["task_handle"])
    assert cancelled["state"] == "cancelled"
    assert cancelled["possible_partial_effects"] is False
    assert runtime.calls == []
    assert not manager._leases
    assert await manager.cancel(first["task_handle"]) == cancelled
    second = await manager.start(start_args(runtime, request_key="new"))
    assert second["task_handle"] != first["task_handle"]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_running_cancel_waits_for_cleanup_and_repeated_cancel_never_interrupts(runtime):
    runtime.release = asyncio.Event()
    runtime.cleanup_release = asyncio.Event()
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await runtime.entered.wait()
    cancel_one = asyncio.create_task(manager.cancel(first["task_handle"]))
    await runtime.cleaning.wait()
    cleaning = await manager.status(first["task_handle"])
    assert cleaning["state"] == "running"
    assert cleaning["cancellation_requested"] is True
    assert cleaning["possible_partial_effects"] is True
    assert not cancel_one.done()
    with pytest.raises(BackendError) as caught:
        await manager.start(start_args(runtime, request_key="busy"))
    assert caught.value.code == "project_busy"
    cancel_two = asyncio.create_task(manager.cancel(first["task_handle"]))
    await asyncio.sleep(0)
    cancel_one.cancel()
    await asyncio.sleep(0)
    assert runtime.cancellations == 1
    assert manager._leases
    runtime.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await cancel_one
    terminal = await cancel_two
    assert terminal["state"] == "cancelled"
    assert terminal["possible_partial_effects"] is True
    assert not manager._leases
    assert not manager._jobs


@pytest.mark.asyncio
async def test_queue_cancel_has_no_partial_effects_and_shutdown_awaits_all_owners(runtime, tmp_path):
    runtime.start_child = False
    runtime.release = asyncio.Event()
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await runtime.entered.wait()
    assert (await manager.status(first["task_handle"]))["state"] == "queued"
    cancelled = await manager.cancel(first["task_handle"])
    assert cancelled["possible_partial_effects"] is False
    sibling = tmp_path / "other"
    sibling.mkdir()
    left = tmp_path / "left"
    left.mkdir()
    await manager.start(start_args(runtime, project_dir=str(left), request_key="new"))
    await manager.start(start_args(runtime, project_dir=str(sibling), request_key="other"))
    await manager.shutdown()
    assert not manager._jobs
    assert not manager._leases
    with pytest.raises(BackendError) as caught:
        await manager.start(start_args(runtime, request_key="after-shutdown"))
    assert caught.value.code == "cancelled"


@pytest.mark.asyncio
async def test_polling_does_not_extend_terminal_ttl_and_active_work_never_expires(runtime):
    now = [100.0]
    manager = NativeTaskManager(runtime, clock=lambda: now[0])
    args = start_args(runtime)
    first = await manager.start(args)
    await settle(manager)
    now[0] += 10799
    assert (await manager.status(first["task_handle"]))["state"] == "completed"
    now[0] += 1
    assert (await manager.status(first["task_handle"]))["state"] == "unavailable"
    assert not manager._keys
    assert not manager._session_owners
    runtime.release = asyncio.Event()
    fresh = await manager.start(args)
    assert fresh["task_handle"] != first["task_handle"]
    now[0] += 100000
    assert (await manager.status(fresh["task_handle"]))["state"] == "queued"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_task_attempt_and_byte_capacity_reject_before_submission(runtime):
    manager = NativeTaskManager(runtime, max_tasks=1, max_attempts=2)
    first = await manager.start(start_args(runtime))
    await settle(manager)
    with pytest.raises(BackendError) as caught:
        await manager.start(start_args(runtime, request_key="another"))
    assert caught.value.code == "task_capacity"
    next_args = {"task_handle": first["task_handle"], "request_key": "follow", "prompt": "next"}
    await manager.followup(next_args)
    await settle(manager)
    with pytest.raises(BackendError) as caught:
        await manager.followup({**next_args, "request_key": "too-many"})
    assert caught.value.code == "task_capacity"
    assert len(runtime.calls) == 2
    # Redelivery still works when the task has consumed its full attempt allowance.
    assert (await manager.start(start_args(runtime)))["attempt_id"] == first["attempt_id"]
    tiny = NativeTaskManager(runtime, max_retained_bytes=100)
    with pytest.raises(BackendError) as caught:
        await tiny.start(start_args(runtime))
    assert caught.value.code == "task_capacity"
    assert not tiny._records
    assert not tiny._keys
    assert not tiny._jobs
    assert not tiny._leases


@pytest.mark.asyncio
async def test_latest_result_and_metadata_are_bounded_detached_and_agent_reported(runtime):
    runtime.outcomes = [
        NativeRunOutcome(
            "completed",
            "\U0001f600" * 20001,
            {
                "stdout_bytes": 7,
                "raw": "private raw input",
                "session_id": "private-session",
                "actual_model": "x" * 10000,
            },
            "private-session",
        ),
        NativeRunOutcome("completed", "already capped", {"truncated": True}),
    ]
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    reserved = manager.retained_bytes
    await settle(manager)
    result = await manager.status(first["task_handle"])
    assert len(result["result"]["content"]) == 20000
    assert result["result"]["truncated"] is True
    assert result["result"]["metadata"]["stdout_bytes"] == 7
    assert len(result["result"]["metadata"]["actual_model"]) == 512
    assert "private-session" not in json.dumps(result)
    assert "private raw input" not in json.dumps(result)
    assert manager.retained_bytes <= reserved
    result["result"]["metadata"]["stdout_bytes"] = 999
    assert (await manager.status(first["task_handle"]))["result"]["metadata"]["stdout_bytes"] == 7
    await manager.followup({"task_handle": first["task_handle"], "request_key": "new", "prompt": "next"})
    await settle(manager)
    assert (await manager.status(first["task_handle"]))["result"]["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_raw_exceptions_are_redacted_and_ambiguous_started_failure_is_unknown(runtime, started, caplog):
    runtime.start_child = started
    runtime.failure = RuntimeError("private first input secret-token")
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await settle(manager)
    result = await manager.status(first["task_handle"])
    assert result["state"] == ("uncertain" if started else "failed")
    assert result["execution_outcome"] == ("unknown" if started else "failed")
    assert result["possible_partial_effects"] is started
    assert "private first input" not in json.dumps(result) + caplog.text + repr(manager._records)
    assert "secret-token" not in json.dumps(result) + caplog.text + repr(manager._records)


@pytest.mark.asyncio
async def test_runtime_terminal_error_code_and_permission_counts_are_preserved(runtime):
    runtime.outcomes = [
        NativeRunOutcome(
            "permission_needed",
            "permission required",
            {"error_code": "permission_needed", "permission_denial_count": 2, "tool_error_count": 1},
        )
    ]
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await settle(manager)
    result = await manager.status(first["task_handle"])
    assert result["error"]["code"] == "permission_needed"
    assert result["result"]["metadata"]["permission_denial_count"] == 2
    assert result["result"]["metadata"]["tool_error_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["missing_environment", "path_not_allowed", "unknown_private_code"])
async def test_prepare_failure_is_redacted_without_retaining_request(runtime, code):
    def fail(*args, **kwargs):
        raise BackendError(
            code,
            "private first input secret-token /private/secret-project",
            "private remedy secret-token /private/secret-project",
            metadata={"secret": "value", "project_dir": "/private/secret-project"},
        )

    runtime.prepare = fail
    manager = NativeTaskManager(runtime)
    with pytest.raises(BackendError) as caught:
        await manager.start(start_args(runtime))
    assert caught.value.code == ("native_unavailable" if code == "unknown_private_code" else code)
    serialized = json.dumps(caught.value.to_dict())
    for private in ("private first input", "private remedy", "secret-token", "/private/secret-project"):
        assert private not in serialized
    if code == "path_not_allowed":
        assert "project_dir and context paths" in str(caught.value)
        assert "allowed_roots" in str(caught.value)
        assert "listmodels" in caught.value.remedy
        assert "operator-owned CLI manifest" in caught.value.remedy
        assert "restart the server" in caught.value.remedy
    else:
        assert str(caught.value) == "Native task admission could not be established."
        assert caught.value.remedy == ""
    assert not caught.value.metadata
    assert not manager._keys
    assert not manager._records
    assert not manager._jobs
    assert not manager._leases


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"cli_name": None},
        {"prompt": ""},
        {"prompt": "x" * (1024 * 1024 + 1)},
        {"request_key": ""},
        {"request_key": "x" * 129},
        {"continuation_id": "pal-state"},
        {"timeout_seconds": True},
        {"timeout_seconds": 1801},
        {"absolute_file_paths": None},
        {"files": ["a"], "absolute_file_paths": ["b"]},
    ],
)
async def test_direct_api_validates_before_preparation_or_submission(runtime, changes):
    manager = NativeTaskManager(runtime)
    with pytest.raises(BackendError):
        await manager.start(start_args(runtime, **changes))
    assert not runtime.prepared
    assert not manager._keys


@pytest.mark.asyncio
async def test_same_native_session_cannot_belong_to_two_tasks(runtime, tmp_path):
    runtime.outcomes = [NativeRunOutcome("completed", "first", session_id="same-native-id")]
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await settle(manager)
    sibling = tmp_path / "other"
    sibling.mkdir()
    runtime.outcomes = [NativeRunOutcome("completed", "second", session_id="same-native-id")]
    second = await manager.start(start_args(runtime, project_dir=str(sibling), request_key="other"))
    await settle(manager)
    result = await manager.status(second["task_handle"])
    assert result["state"] == "uncertain"
    assert result["error"]["code"] == "session_conflict"
    assert result["followup_available"] is False
    assert (await manager.status(first["task_handle"]))["followup_available"] is True


@pytest.mark.asyncio
async def test_cancel_response_keeps_attempt_identity_when_followup_wins_return_race(runtime, monkeypatch):
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime))
    await settle(manager)
    runtime.release = asyncio.Event()
    runtime.entered.clear()
    second = await manager.followup({"task_handle": first["task_handle"], "request_key": "second", "prompt": "second"})
    await runtime.entered.wait()
    cleanup_complete = asyncio.Event()
    return_response = asyncio.Event()
    original_cleanup = manager._finish_cleanup

    async def pause_after_cleanup(cleanup):
        await original_cleanup(cleanup)
        cleanup_complete.set()
        await return_response.wait()

    monkeypatch.setattr(manager, "_finish_cleanup", pause_after_cleanup)
    cancelling = asyncio.create_task(manager.cancel(first["task_handle"]))
    await cleanup_complete.wait()
    third = await manager.followup({"task_handle": first["task_handle"], "request_key": "third", "prompt": "third"})
    return_response.set()
    cancelled = await cancelling
    assert cancelled["attempt_id"] == second["attempt_id"]
    assert cancelled["attempt_id"] != third["attempt_id"]
    assert cancelled["state"] == "cancelled"
    assert cancelled["result_unavailable"] is True
    monkeypatch.setattr(manager, "_finish_cleanup", original_cleanup)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_numeric_timeout_coercion_and_required_environment_names_redaction(runtime):
    manager = NativeTaskManager(runtime)
    first = await manager.start(start_args(runtime, timeout_seconds=30.0))
    duplicate = await manager.start(start_args(runtime, timeout_seconds=30))
    assert duplicate["attempt_id"] == first["attempt_id"]
    await settle(manager)

    def missing(*args, **kwargs):
        raise BackendError(
            "missing_environment",
            "secret value",
            metadata={"missing_environment_names": ["API_TOKEN", "bad name=secret", "9invalid"], "secret": "value"},
        )

    runtime.prepare = missing
    with pytest.raises(BackendError) as caught:
        await manager.start(start_args(runtime, request_key="missing"))
    assert caught.value.metadata == {"missing_environment_names": ["API_TOKEN"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rename", "delete", "symlink_retarget"])
async def test_start_redelivery_uses_submitted_identity_after_project_changes(runtime, tmp_path, change):
    original_project = tmp_path / "project"
    original_project.mkdir()
    submitted = original_project
    if change == "symlink_retarget":
        submitted = tmp_path / "alias"
        submitted.symlink_to(original_project, target_is_directory=True)
    args = start_args(runtime, project_dir=str(submitted))
    manager = NativeTaskManager(runtime)
    first = await manager.start(args)
    await settle(manager)
    if change == "rename":
        original_project.rename(tmp_path / "renamed")
    elif change == "delete":
        original_project.rmdir()
    else:
        other = tmp_path / "other"
        other.mkdir()
        submitted.unlink()
        submitted.symlink_to(other, target_is_directory=True)
    redelivery = await manager.start(args)
    assert redelivery["task_handle"] == first["task_handle"]
    assert redelivery["attempt_id"] == first["attempt_id"]
    assert redelivery["deduplicated"] is True
    assert len(runtime.prepared) == 1
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_json_control_characters_reserve_worst_case_before_concurrent_completions(runtime, tmp_path):
    too_small = NativeTaskManager(runtime, max_retained_bytes=100000)
    with pytest.raises(BackendError) as caught:
        await too_small.start(start_args(runtime))
    assert caught.value.code == "task_capacity"
    assert not too_small._jobs
    runtime.release = asyncio.Event()
    runtime.outcomes = [
        NativeRunOutcome("completed", "\x00" * 20000, session_id="a" * 512),
        NativeRunOutcome("completed", "\x01" * 20000, session_id="b" * 512),
    ]
    manager = NativeTaskManager(runtime, max_retained_bytes=280000)
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    first = await manager.start(start_args(runtime, project_dir=str(left)))
    second = await manager.start(start_args(runtime, project_dir=str(right), request_key="second"))
    reserved = manager.retained_bytes
    assert reserved <= manager.max_retained_bytes
    runtime.release.set()
    await settle(manager)
    assert manager.retained_bytes <= reserved
    assert manager.retained_bytes <= manager.max_retained_bytes
    assert len((await manager.status(first["task_handle"]))["result"]["content"]) == 20000
    assert len((await manager.status(second["task_handle"]))["result"]["content"]) == 20000
