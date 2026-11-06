"""Server boundary regression tests with a synthetic, non-launching backend."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

import clink.backend
import server
from clink.contracts import AdapterPolicy, BackendError, BackendTarget, ClientSnapshot, CLIResult, RoleSnapshot
from config import MCP_RESPONSE_SIZE_LIMIT
from tools.shared.exceptions import ToolExecutionError
from utils.conversation_memory import get_thread


class FakeBackend:
    def __init__(self, root):
        self.root = root
        self.requests = []
        self.active = 0
        self.max_active = 0

    def resolve(self, cli_name=None, role=None, model=None, working_directory=None, **unused):
        client = ClientSnapshot(cli_name or "codex", "codex", (sys.executable,), self.root, (self.root,))
        return BackendTarget(
            client,
            RoleSnapshot(role or "default", "subordinate role"),
            model,
            model,
            Path(working_directory or self.root),
            AdapterPolicy("codex", verified=True),
        )

    def validate_files(self, target, files, images=(), tool_name="clink"):
        return tuple(files)

    async def run(self, request):
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return CLIResult(
            "Synthetic advisory answer",
            "success",
            request.request_id,
            request.target.cli_name,
            request.target.role.name,
            request.target.requested_model,
            "native-observed",
            0.01,
        )


@pytest.fixture
def fake_backend(monkeypatch, tmp_path):
    instance = FakeBackend(tmp_path)
    monkeypatch.setattr(clink.backend, "get_backend", lambda: instance)
    return instance


def payload(result):
    return json.loads(result[0].text)


def continuation(result):
    data = payload(result)
    return data.get("continuation_id") or data["continuation_offer"]["continuation_id"]


@pytest.mark.asyncio
async def test_all_schemas_and_local_tools_without_provider_imports():
    schemas = await server.handle_list_tools()
    assert len(schemas) == 18
    assert not [name for name in sys.modules if name.startswith("providers.")]
    for schema in schemas:
        assert "model" not in schema.inputSchema.get("required", [])
    assert payload(await server.handle_call_tool("version", {}))["name"] == "clink-mcp-server"
    assert payload(await server.handle_call_tool("challenge", {"prompt": "A claim"}))["status"] == "challenge_accepted"


@pytest.mark.asyncio
async def test_cross_tool_cross_cli_replay_and_native_model(fake_backend):
    first = await server.handle_call_tool(
        "chat",
        {
            "prompt": "Remember the proposed blue API.",
            "working_directory_absolute_path": str(fake_backend.root),
            "model": "literal:model-id",
        },
    )
    thread_id = continuation(first)
    await server.handle_call_tool(
        "clink",
        {"prompt": "Review that API", "continuation_id": thread_id, "cli_name": "different-client", "role": "reviewer"},
    )
    assert fake_backend.requests[0].target.effective_model == "literal:model-id"
    followup = fake_backend.requests[1]
    assert followup.target.cli_name == "different-client"
    assert "blue API" in "\n".join(followup.history_turns)
    assert followup.deadline == followup.admitted_at + 1800
    thread = get_thread(thread_id)
    assert thread.successful_turns == 4
    assert {turn.tool_name for turn in thread.turns} == {"chat", "clink"}


@pytest.mark.asyncio
async def test_same_continuation_serialized_and_new_calls_independent(fake_backend):
    first = await server.handle_call_tool("clink", {"prompt": "Seed"})
    thread_id = continuation(first)
    await asyncio.gather(
        *(server.handle_call_tool("clink", {"prompt": f"followup {i}", "continuation_id": thread_id}) for i in range(2))
    )
    assert fake_backend.max_active == 1
    assert "followup 0" in "\n".join(fake_backend.requests[-1].history_turns)
    await asyncio.gather(*(server.handle_call_tool("clink", {"prompt": f"fresh {i}"}) for i in range(2)))
    assert fake_backend.max_active == 2
    assert "Seed" not in fake_backend.requests[-1].prompt


@pytest.mark.asyncio
async def test_missing_thread_and_private_arguments_fail_before_launch(fake_backend):
    for arguments, code in [
        ({"prompt": "x", "continuation_id": "b960577c-8081-4daf-afdf-167811c714f4"}, "continuation_unavailable"),
        ({"prompt": "x", "_deadline": 99999999}, "invalid_request"),
    ]:
        with pytest.raises(ToolExecutionError) as caught:
            await server.handle_call_tool("clink", arguments)
        assert json.loads(caught.value.payload)["error"]["code"] == code
    assert fake_backend.requests == []


@pytest.mark.asyncio
async def test_validation_and_caught_errors_obey_complete_response_limit(monkeypatch, fake_backend):
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("clink", {"prompt": "synthetic", "absolute_file_paths": [{}] * 10000})
    assert json.loads(caught.value.payload)["error"]["code"] == "invalid_request"
    assert len(caught.value.payload.encode("utf-8")) < 1024
    assert fake_backend.requests == []

    async def oversized_failure(*args):
        raise BackendError("synthetic", "x" * MCP_RESPONSE_SIZE_LIMIT)

    monkeypatch.setattr(server, "_execute_call", oversized_failure)
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("clink", {"prompt": "synthetic"})
    assert json.loads(caught.value.payload)["error"]["code"] == "output_limit"
    assert len(caught.value.payload.encode("utf-8")) < MCP_RESPONSE_SIZE_LIMIT


@pytest.mark.asyncio
async def test_thinkdeep_opt_out_duplicate_and_conflict(fake_backend):
    args = {
        "step": "Inspect an issue",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "findings": "Evidence",
        "confidence": "high",
        "use_assistant_model": False,
    }
    first = await server.handle_call_tool("thinkdeep", args)
    thread_id = continuation(first)
    second = await server.handle_call_tool("thinkdeep", {**args, "continuation_id": thread_id})
    assert second == first
    assert fake_backend.requests == []
    assert get_thread(thread_id).successful_turns == 2
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("thinkdeep", {**args, "continuation_id": thread_id, "findings": "Changed"})
    assert json.loads(caught.value.payload)["error"]["code"] == "workflow_conflict"
