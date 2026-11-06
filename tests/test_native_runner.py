"""Synthetic process tests for native running-state callbacks and permit cleanup."""

import asyncio
import os
import sys
import time

import pytest

import clink.runner as runner
from clink.contracts import BackendError
from clink.runner import ProcessPool

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native process supervision requires POSIX process groups.")


def execution_arguments(root, **overrides):
    return {
        "content": b"synthetic input",
        "cwd": str(root),
        "env": {},
        "deadline": time.monotonic() + 5,
        "request_id": "synthetic-native-runner",
        **overrides,
    }


@pytest.fixture(autouse=True)
def bounded_cleanup(monkeypatch):
    monkeypatch.setattr(runner, "CLEANUP_SECONDS", 0.1)


@pytest.mark.asyncio
async def test_on_started_runs_once_after_real_spawn_and_before_input(tmp_path, monkeypatch):
    pool = ProcessPool(1)
    children = []
    callbacks = []
    spawn = asyncio.create_subprocess_exec
    write = pool._write

    async def record_spawn(*args, **kwargs):
        child = await spawn(*args, **kwargs)
        children.append(child)
        assert callbacks == []
        return child

    def started():
        assert len(children) == 1
        assert children[0] in pool._children
        assert children[0].returncode is None
        assert pool.running == 1
        callbacks.append(children[0].pid)

    async def write_after_callback(stream, content):
        assert callbacks == [children[0].pid]
        await write(stream, content)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    monkeypatch.setattr(pool, "_write", write_after_callback)
    output = await pool.execute(
        (sys.executable, "-c", "import sys; print(sys.stdin.buffer.read().decode())"),
        **execution_arguments(tmp_path, on_started=started),
    )
    assert output.stdout == "synthetic input\n"
    assert callbacks == [children[0].pid]
    assert pool.running == 0
    assert not pool._children
    assert not pool._executions


@pytest.mark.asyncio
async def test_queue_admission_does_not_report_started_before_a_process_exists(tmp_path, monkeypatch):
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 5)
    callbacks = []
    spawned = []
    spawn = asyncio.create_subprocess_exec

    async def record_spawn(*args, **kwargs):
        child = await spawn(*args, **kwargs)
        spawned.append(child)
        return child

    def started():
        assert len(spawned) == 1
        callbacks.append(spawned[0].pid)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    queued = asyncio.create_task(
        pool.execute((sys.executable, "-c", "pass"), **execution_arguments(tmp_path, on_started=started))
    )
    try:
        await asyncio.sleep(0)
        assert len(pool._waiters) == 1
        assert callbacks == spawned == []
    finally:
        pool._release()
    await asyncio.wait_for(queued, 2)
    assert callbacks == [spawned[0].pid]
    assert pool.running == 0


@pytest.mark.asyncio
async def test_callback_failure_cleans_child_and_releases_slot_for_next_request(tmp_path, monkeypatch):
    pool = ProcessPool(1)
    children = []
    spawn = asyncio.create_subprocess_exec

    async def record_spawn(*args, **kwargs):
        child = await spawn(*args, **kwargs)
        children.append(child)
        return child

    def fail_started():
        assert children[0] in pool._children
        raise RuntimeError("synthetic callback failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    with pytest.raises(RuntimeError, match="synthetic callback failure"):
        await pool.execute(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            **execution_arguments(tmp_path, on_started=fail_started),
        )
    assert children[0].returncode is not None
    assert pool.running == 0
    assert not pool._children
    assert not pool._executions
    output = await pool.execute((sys.executable, "-c", "print('next')"), **execution_arguments(tmp_path))
    assert output.stdout == "next\n"
    assert pool.running == 0


@pytest.mark.asyncio
async def test_cancelled_queued_request_never_calls_callback_or_spawns(tmp_path, monkeypatch):
    pool = ProcessPool(1)
    await pool._acquire(time.monotonic() + 5)
    callbacks = []

    async def forbidden_spawn(*args, **kwargs):
        pytest.fail("A cancelled queued request reached process creation.")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    queued = asyncio.create_task(
        pool.execute(
            (sys.executable, "-c", "pass"),
            **execution_arguments(tmp_path, on_started=lambda: callbacks.append("started")),
        )
    )
    try:
        await asyncio.sleep(0)
        assert pool._waiters
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert not pool._waiters
        assert callbacks == []
        assert pool.running == 1
        assert not pool._children
        assert not pool._executions
    finally:
        pool._release()
    assert pool.running == 0


@pytest.mark.asyncio
async def test_spawn_failure_never_reports_started_and_releases_permit(tmp_path):
    pool = ProcessPool(1)
    callbacks = []
    with pytest.raises(BackendError) as caught:
        await pool.execute(
            (str(tmp_path / "synthetic-missing-cli"),),
            **execution_arguments(tmp_path, on_started=lambda: callbacks.append("started")),
        )
    assert caught.value.code == "cli_missing"
    assert callbacks == []
    assert pool.running == 0
    assert not pool._children


@pytest.mark.asyncio
async def test_cancellation_during_spawn_reports_started_once_then_cleans_child(tmp_path, monkeypatch):
    pool = ProcessPool(1)
    spawned = asyncio.Event()
    release_spawn = asyncio.Event()
    children = []
    callbacks = []
    spawn = asyncio.create_subprocess_exec

    async def delayed_return(*args, **kwargs):
        child = await spawn(*args, **kwargs)
        children.append(child)
        spawned.set()
        await release_spawn.wait()
        return child

    def started():
        assert children[0] in pool._children
        callbacks.append(children[0].pid)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_return)
    active = asyncio.create_task(
        pool.execute(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            **execution_arguments(tmp_path, on_started=started),
        )
    )
    try:
        await asyncio.wait_for(spawned.wait(), 1)
        active.cancel()
        await asyncio.sleep(0)
        assert not active.done()
        assert callbacks == []
        release_spawn.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(active, 2)
        assert callbacks == [children[0].pid]
        assert children[0].returncode is not None
        assert pool.running == 0
        assert not pool._children
        assert not pool._executions
    finally:
        release_spawn.set()
        await pool.shutdown()
