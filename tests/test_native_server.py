"""Offline MCP boundary tests for explicit native jobs and advisory consultation."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio

import clink.backend
import clink.tasks
import server
from clink.contracts import BackendError
from clink.native_contracts import FrozenNativeContext, NativeRunOutcome
from clink.tasks import NativeTaskManager
from tests.cli_helpers import context_target
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError

HANDLE = "nt_" + "a" * 32


def payload(result):
    return json.loads(result[0].text)


def start_arguments(root, **overrides):
    return {
        "action": "start",
        "cli_name": "claude",
        "project_dir": str(root),
        "prompt": "Perform the synthetic task.",
        "request_key": "synthetic-start",
        **overrides,
    }


class BlockingNativeRuntime:
    """Retain only synthetic inputs while the test controls terminal completion."""

    def __init__(self, root):
        self.root = root
        self.preparations = []
        self.runs = []
        self.entered = asyncio.Queue()
        self.outcome = NativeRunOutcome("completed", "Synthetic work report.", session_id="private-native-session")

    def prepare(self, cli_name, role, model, project_dir, files=(), images=(), absolute_deadline=None):
        self.preparations.append(
            {
                "cli_name": cli_name,
                "role": role,
                "model": model,
                "project_dir": project_dir,
                "files": files,
                "images": images,
                "deadline": absolute_deadline,
            }
        )
        target = context_target(self.root)
        target = replace(
            target,
            client=replace(target.client, name=cli_name),
            role=replace(target.role, name=role or "default"),
            requested_model=model,
            effective_model=model,
        )
        return FrozenNativeContext(target, "synthetic-native-profile", files, images, absolute_deadline)

    async def run(self, context, prompt, session_id=None, *, attempt_id, started=None):
        run = {
            "context": context,
            "prompt": prompt,
            "session_id": session_id,
            "attempt_id": attempt_id,
            "release": asyncio.Event(),
            "cancelled": False,
        }
        self.runs.append(run)
        if started is not None:
            started()
        self.entered.put_nowait(run)
        try:
            await run["release"].wait()
        except asyncio.CancelledError:
            run["cancelled"] = True
            raise
        return self.outcome


@pytest_asyncio.fixture
async def native_manager(monkeypatch, tmp_path):
    runtime = BlockingNativeRuntime(tmp_path)
    manager = NativeTaskManager(runtime)
    monkeypatch.setattr(clink.tasks, "get_task_manager", lambda: manager)
    try:
        yield manager, runtime
    finally:
        await manager.shutdown()


def forbid_pal_history(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Native lifecycle actions must bypass PAL history and consultation execution.")

    monkeypatch.setattr("utils.call_state.conversation_call", forbidden)
    monkeypatch.setattr(server, "_replay_arguments", forbidden)
    monkeypatch.setattr(CLinkTool, "execute", forbidden)
    monkeypatch.setattr(clink.backend, "get_backend", forbidden)


@pytest.mark.asyncio
async def test_native_schema_discovery_describes_mutation_and_optional_prompt():
    tool = next(item for item in await server.handle_list_tools() if item.name == "clink")
    properties = tool.inputSchema["properties"]
    assert properties["action"]["enum"] == ["consult", "start", "status", "followup", "cancel"]
    assert properties["action"]["default"] == "consult"
    assert not tool.inputSchema.get("required")
    assert {"project_dir", "task_handle", "request_key", "timeout_seconds", "continuation_id"} <= properties.keys()
    assert tool.inputSchema["additionalProperties"] is False
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_native_missing_environment_reports_names_without_values(native_manager, monkeypatch):
    _, runtime = native_manager

    def missing(*args, **kwargs):
        raise BackendError(
            "missing_environment",
            "synthetic private diagnostic",
            metadata={
                "missing_environment_names": ["FIXTURE_TOKEN", "BAD=value", "not a name", "X" * 129],
                "credential_value": "private-value",
            },
        )

    monkeypatch.setattr(runtime, "prepare", missing)
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("clink", start_arguments(runtime.root))
    data = json.loads(caught.value.payload)
    assert data["error"]["code"] == "missing_environment"
    assert data["error"]["missing_environment_names"] == ["FIXTURE_TOKEN"]
    assert "private" not in caught.value.payload
    assert "BAD=value" not in caught.value.payload
    assert runtime.runs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "start", "prompt": "Task", "project_dir": "/synthetic", "request_key": "key"},
        {"action": "start", "prompt": "Task", "cli_name": "claude", "request_key": "key"},
        {"action": "start", "prompt": "Task", "cli_name": "claude", "project_dir": "/synthetic"},
        {"action": "start", "prompt": " ", "cli_name": "claude", "project_dir": "/synthetic", "request_key": "key"},
        {"action": "followup", "prompt": "Next", "task_handle": HANDLE},
        {"action": "followup", "request_key": "key", "task_handle": HANDLE},
        {"action": "followup", "prompt": "Next", "request_key": "key"},
        {"action": "status"},
        {"action": "cancel"},
        {"action": "unknown", "task_handle": HANDLE},
        {"prompt": "Advisory", "project_dir": "/synthetic"},
        {"prompt": "Advisory", "task_handle": HANDLE},
        {"prompt": "Advisory", "request_key": "key"},
    ],
)
async def test_invalid_action_arguments_fail_before_manager_or_history(monkeypatch, arguments):
    forbid_pal_history(monkeypatch)
    monkeypatch.setattr(clink.tasks, "get_task_manager", lambda: pytest.fail("Invalid input reached task admission."))
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("clink", arguments)
    assert json.loads(caught.value.payload)["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "followup", "status", "cancel"])
@pytest.mark.parametrize("continuation_id", [None, "", "b960577c-8081-4daf-afdf-167811c714f4"])
async def test_native_actions_reject_any_explicit_pal_continuation(monkeypatch, tmp_path, action, continuation_id):
    forbid_pal_history(monkeypatch)
    monkeypatch.setattr(
        clink.tasks, "get_task_manager", lambda: pytest.fail("PAL continuation reached native admission.")
    )
    arguments = start_arguments(tmp_path)
    if action == "followup":
        arguments = {"action": action, "task_handle": HANDLE, "prompt": "Next", "request_key": "next"}
    elif action in {"status", "cancel"}:
        arguments = {"action": action, "task_handle": HANDLE}
    arguments["continuation_id"] = continuation_id
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("clink", arguments)
    assert json.loads(caught.value.payload)["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "cancel"])
@pytest.mark.parametrize(
    "extra", [{"prompt": "Extra"}, {"cli_name": "claude"}, {"request_key": "key"}, {"timeout_seconds": 10}]
)
async def test_poll_and_cancel_accept_only_handle_and_action(monkeypatch, action, extra):
    monkeypatch.setattr(clink.tasks, "get_task_manager", lambda: pytest.fail("Extra fields reached native admission."))
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("clink", {"action": action, "task_handle": HANDLE, **extra})
    assert json.loads(caught.value.payload)["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_start_returns_while_work_is_active_and_status_cancel_need_no_prompt(native_manager, monkeypatch):
    manager, runtime = native_manager
    forbid_pal_history(monkeypatch)
    first = payload(await asyncio.wait_for(server.handle_call_tool("clink", start_arguments(runtime.root)), 1))
    run = await asyncio.wait_for(runtime.entered.get(), 1)
    handle = first["task_handle"]
    assert first["state"] in {"queued", "running"}
    assert first["attempt_id"] != handle
    assert not run["release"].is_set()
    assert handle in manager._jobs
    running = payload(await server.handle_call_tool("clink", {"action": "status", "task_handle": handle}))
    assert running["state"] == "running"
    assert running["attempt_id"] == first["attempt_id"]
    assert "continuation_id" not in running
    redelivered = payload(await server.handle_call_tool("clink", start_arguments(runtime.root)))
    assert redelivered["task_handle"] == handle
    assert redelivered["attempt_id"] == first["attempt_id"]
    assert redelivered["deduplicated"] is True
    assert len(runtime.preparations) == 1
    cancelled = payload(await server.handle_call_tool("clink", {"action": "cancel", "task_handle": handle}))
    assert cancelled["state"] == "cancelled"
    assert cancelled["possible_partial_effects"] is True
    assert run["cancelled"] is True
    assert handle not in manager._jobs
    assert len(runtime.runs) == 1


@pytest.mark.asyncio
async def test_followup_passes_only_new_input_and_private_native_session(native_manager, monkeypatch):
    manager, runtime = native_manager
    forbid_pal_history(monkeypatch)
    first = payload(await server.handle_call_tool("clink", start_arguments(runtime.root)))
    first_run = await asyncio.wait_for(runtime.entered.get(), 1)
    first_job = manager._jobs[first["task_handle"]]
    first_run["release"].set()
    await asyncio.wait_for(first_job, 1)
    completed = payload(
        await server.handle_call_tool("clink", {"action": "status", "task_handle": first["task_handle"]})
    )
    assert completed["state"] == "completed"
    assert completed["result"]["agent_reported_claims"] == "unverified"
    assert "private-native-session" not in json.dumps(completed)
    followup = payload(
        await server.handle_call_tool(
            "clink",
            {
                "action": "followup",
                "task_handle": first["task_handle"],
                "prompt": "Apply this new synthetic input.",
                "request_key": "synthetic-followup",
            },
        )
    )
    second_run = await asyncio.wait_for(runtime.entered.get(), 1)
    assert followup["task_handle"] == first["task_handle"]
    assert followup["attempt_id"] != first["attempt_id"]
    assert second_run["prompt"] == "Apply this new synthetic input."
    assert second_run["session_id"] == "private-native-session"
    assert first_run["session_id"] is None
    assert not {"continuation_id", "history_turns", "_replay_history"}.intersection(second_run)


@pytest.mark.asyncio
async def test_public_context_and_timeout_reach_native_admission(native_manager, monkeypatch):
    _, runtime = native_manager
    forbid_pal_history(monkeypatch)
    source = runtime.root / "synthetic.txt"
    source.write_text("Synthetic context only.", encoding="utf-8")
    first = payload(
        await server.handle_call_tool(
            "clink", start_arguments(runtime.root, absolute_file_paths=[str(source)], timeout_seconds=30)
        )
    )
    run = await asyncio.wait_for(runtime.entered.get(), 1)
    assert runtime.preparations[0]["files"] == (str(source),)
    assert run["context"].files == (str(source),)
    assert run["context"].deadline == pytest.approx(first["admitted_at"] + 30)


@pytest.mark.asyncio
async def test_native_status_caps_result_and_excludes_raw_output_identity(native_manager):
    from config import MCP_RESPONSE_SIZE_LIMIT

    manager, runtime = native_manager
    runtime.outcome = NativeRunOutcome(
        "completed",
        "Synthetic result. " * 2000,
        metadata={
            "actual_model": "synthetic-model",
            "stdout": "private-raw-output",
            "session_id": "private-native-session",
        },
        session_id="private-native-session",
    )
    first = payload(await server.handle_call_tool("clink", start_arguments(runtime.root)))
    run = await asyncio.wait_for(runtime.entered.get(), 1)
    job = manager._jobs[first["task_handle"]]
    run["release"].set()
    await asyncio.wait_for(job, 1)
    response = await server.handle_call_tool("clink", {"action": "status", "task_handle": first["task_handle"]})
    data = payload(response)
    assert data["state"] == "completed"
    assert len(data["result"]["content"]) == 20000
    assert data["result"]["truncated"] is True
    assert data["result"]["metadata"] == {"actual_model": "synthetic-model"}
    assert "private-raw-output" not in response[0].text
    assert "private-native-session" not in response[0].text
    assert (
        len(json.dumps([item.model_dump() for item in response], ensure_ascii=False).encode("utf-8"))
        < MCP_RESPONSE_SIZE_LIMIT
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["status", "cancel"])
async def test_unknown_handle_never_launches_or_creates_history(native_manager, monkeypatch, action):
    _, runtime = native_manager
    forbid_pal_history(monkeypatch)
    unavailable = payload(await server.handle_call_tool("clink", {"action": action, "task_handle": HANDLE}))
    assert unavailable["state"] == "unavailable"
    assert unavailable["execution_outcome"] == "unknown"
    assert unavailable["result_unavailable"] is True
    assert runtime.preparations == runtime.runs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_consult_retains_pal_history_and_backend_execution(cli_backend, monkeypatch, explicit):
    monkeypatch.setattr(clink.tasks, "get_task_manager", lambda: pytest.fail("Consultation entered native lifecycle."))
    arguments = {"prompt": "Remember a synthetic advisory observation."}
    if explicit:
        arguments["action"] = "consult"
    first = payload(await server.handle_call_tool("clink", arguments))
    continuation = first.get("continuation_id") or first["continuation_offer"]["continuation_id"]
    await server.handle_call_tool(
        "clink", {"prompt": "Review the prior observation.", "continuation_id": continuation, "action": "consult"}
    )
    assert len(cli_backend.requests) == 2
    assert "synthetic advisory observation" in "\n".join(cli_backend.requests[1].history_turns)
    from utils.conversation_memory import get_thread

    assert get_thread(continuation).successful_turns == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "status", "followup", "cancel"])
@pytest.mark.parametrize("failure", [False, True])
async def test_all_native_responses_and_failures_obey_complete_mcp_limit(monkeypatch, tmp_path, action, failure):
    from config import MCP_RESPONSE_SIZE_LIMIT

    async def oversized(*args, **kwargs):
        if failure:
            raise BackendError("synthetic", "秘密" * MCP_RESPONSE_SIZE_LIMIT)
        return {"state": "completed", "result": {"content": "秘密" * MCP_RESPONSE_SIZE_LIMIT}}

    manager = SimpleNamespace(start=oversized, followup=oversized, status=oversized, cancel=oversized)
    monkeypatch.setattr(clink.tasks, "get_task_manager", lambda: manager)
    if action == "start":
        arguments = start_arguments(tmp_path)
    elif action == "followup":
        arguments = {"action": action, "task_handle": HANDLE, "prompt": "Next", "request_key": "next"}
    else:
        arguments = {"action": action, "task_handle": HANDLE}
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("clink", arguments)
    assert json.loads(caught.value.payload)["error"]["code"] == "output_limit"
    assert len(caught.value.payload.encode("utf-8")) < MCP_RESPONSE_SIZE_LIMIT


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "transport", "native_cleanup"])
async def test_server_shutdown_finishes_native_cleanup_before_closing_pool(monkeypatch, failure):
    events = []

    @asynccontextmanager
    async def stdio():
        yield "synthetic-reader", "synthetic-writer"

    async def run(*args):
        events.append("transport")
        if failure == "transport":
            raise RuntimeError("synthetic transport failure")

    async def shutdown_manager():
        events.append("native_cleanup_started")
        await asyncio.sleep(0)
        events.append("native_cleanup_finished")
        if failure == "native_cleanup":
            raise RuntimeError("synthetic cleanup failure")

    async def close_backend():
        assert events[-1] == "native_cleanup_finished"
        events.append("pool_closed")

    monkeypatch.setattr(server, "stdio_server", stdio)
    monkeypatch.setattr(server.server, "run", run)
    monkeypatch.setattr(clink.tasks, "shutdown_task_manager", shutdown_manager)
    monkeypatch.setattr(clink.backend, "get_backend", lambda: SimpleNamespace(close=close_backend))
    if failure:
        with pytest.raises(RuntimeError, match="synthetic"):
            await server.main()
    else:
        await server.main()
    assert events == ["transport", "native_cleanup_started", "native_cleanup_finished", "pool_closed"]
