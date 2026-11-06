"""Offline server/state integration for blinded CLI consensus."""

import pytest
from mcp.types import TextContent

from clink.contracts import BackendError
from tests.test_consensus import FakeBackend, arguments, payload
from tools.shared.exceptions import ToolExecutionError
from utils.call_state import conversation_call
from utils.conversation_memory import get_thread


@pytest.fixture
def backend(tmp_path, monkeypatch):
    fake = FakeBackend(tmp_path)
    monkeypatch.setattr("clink.backend.get_backend", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_server_keeps_cross_tool_history_private_and_restores_sequential_consensus(backend):
    import server

    async with conversation_call("chat", {"prompt": "HISTORY_PRIVATE_CALLER"}) as call:
        call.commit(
            [TextContent(type="text", text="HISTORY_PRIVATE_ASSISTANT")],
            user_content="HISTORY_PRIVATE_CALLER",
            assistant_content="HISTORY_PRIVATE_ASSISTANT",
        )
        continuation_id = call.continuation_id
    first_args = arguments(continuation_id=continuation_id)
    first = payload(await server.handle_call_tool("consensus", first_args))
    assert first["model_response"]["status"] == "success"
    assert first["current_model_index"] == 1
    next_args = arguments(step="PRIVATE_CALLER_SYNTHESIS_NOTES", step_number=2, continuation_id=continuation_id)
    next_args.pop("models")
    final = payload(await server.handle_call_tool("consensus", next_args))
    assert final["consensus_status"] == "complete"
    assert len(backend.requests) == 2
    for request in backend.requests:
        assert request.prompt == first_args["step"]
        assert "HISTORY_PRIVATE" not in request.prompt + request.system_prompt
        assert "PRIVATE_CALLER_SYNTHESIS" not in request.prompt + request.system_prompt
        assert request.history_turns == ()


@pytest.mark.asyncio
async def test_server_exact_repeat_validates_context_without_relaunch(backend):
    import server

    context = backend.root / "context.py"
    context.write_text("first")
    first_args = arguments(execution_mode="parallel", relevant_files=[str(context)])
    first = payload(await server.handle_call_tool("consensus", first_args))
    replay_args = {**first_args, "continuation_id": first["continuation_id"]}
    replay = payload(await server.handle_call_tool("consensus", replay_args))
    assert replay == first
    assert len(backend.requests) == 2
    context.write_text("changed")
    with pytest.raises(ToolExecutionError) as error:
        await server.handle_call_tool("consensus", replay_args)
    import json

    invalid = json.loads(str(error.value))
    assert invalid["error"]["code"] == "consensus_invalidated"
    assert len(backend.requests) == 2
    state = get_thread(first["continuation_id"]).workflow_states["consensus"]
    assert state["invalidated"]
    assert all(outcome["valid"] is False for outcome in state["outcomes"])


@pytest.mark.asyncio
async def test_server_partial_outcomes_are_terminal_failed_attempt_without_successful_turn(backend):
    import server

    async def handler(request):
        if request.target.cli_name == "second":
            raise BackendError("rate_limited", "Rate limited.")
        return backend.result(request)

    backend.handler = handler
    result = payload(await server.handle_call_tool("consensus", arguments(execution_mode="parallel")))
    assert result["consensus_status"] == "partial"
    assert result["attempt_success"] is False
    thread = get_thread(result["continuation_id"])
    assert thread.successful_turns == 0
    assert thread.attempts[-1]["success"] is False
    assert thread.attempts[-1]["status"] == "partial"
    assert len(thread.turns) == 2
    assert all(turn.attempt_status == "partial" for turn in thread.turns)
    assert thread.workflow_states["consensus"]["outcomes"][0]["status"] == "success"
    assert thread.workflow_states["consensus"]["outcomes"][1]["error"]["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_partial_consensus_can_be_synthesized_cross_tool_without_disclosing_peer_history_to_consultants(backend):
    import server

    async def handler(request):
        if request.target.cli_name == "second":
            raise BackendError("rate_limited", "Rate limited.")
        return backend.result(request)

    backend.handler = handler
    first_args = arguments(execution_mode="parallel")
    partial = payload(await server.handle_call_tool("consensus", first_args))
    continuation_id = partial["continuation_id"]
    assert partial["consensus_status"] == "partial"
    assert get_thread(continuation_id).successful_turns == 0

    await server.handle_call_tool(
        "clink",
        {
            "prompt": "Synthesize the valid partial consensus and identify the missing perspective.",
            "cli_name": "synthesizer",
            "continuation_id": continuation_id,
        },
    )
    consultant_requests = backend.requests[:2]
    assert len(backend.requests) == 3
    for request in consultant_requests:
        assert request.history_turns == ()
        assert request.prompt == first_args["step"]
        assert "PEER_" not in request.prompt + request.system_prompt
        assert "CALLER_PRIVATE_FINDINGS" not in request.prompt + request.system_prompt
    synthesis = backend.requests[2]
    assert synthesis.tool_name == "clink"
    assert synthesis.history_turns
    history = "\n".join(synthesis.history_turns)
    assert first_args["step"] in history
    assert "PEER_first" in history
    assert '"valid": true' in history
    assert '"consensus_status": "partial"' in history
    assert "rate_limited" in history
    assert "partial/incomplete attempt" in history
    assert get_thread(continuation_id).successful_turns == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_mode", ["parallel", "sequential"])
async def test_invalidated_consensus_verdicts_are_not_usable_cross_tool_history(backend, execution_mode):
    import server

    context = backend.root / "context.py"
    context.write_text("original context")
    first_args = arguments(execution_mode=execution_mode, relevant_files=[str(context)])
    complete = payload(await server.handle_call_tool("consensus", first_args))
    continuation_id = complete["continuation_id"]
    if execution_mode == "sequential":
        later_args = arguments(
            step="Private notes summarize PEER_first for later synthesis.",
            step_number=2,
            continuation_id=continuation_id,
        )
        later_args.pop("models")
        complete = payload(await server.handle_call_tool("consensus", later_args))
    assert complete["consensus_status"] == "complete"
    assert "PEER_first" in get_thread(continuation_id).turns[-1].content

    context.write_text("changed context")
    prior_successful_turns = get_thread(continuation_id).successful_turns
    with pytest.raises(ToolExecutionError):
        await server.handle_call_tool("consensus", {**first_args, "continuation_id": continuation_id})
    assert get_thread(continuation_id).successful_turns == prior_successful_turns
    await server.handle_call_tool(
        "clink",
        {
            "prompt": "Explain which context remains usable after the consensus was invalidated.",
            "cli_name": "synthesizer",
            "continuation_id": continuation_id,
        },
    )
    assert len(backend.requests) == 3
    for request in backend.requests[:2]:
        assert request.history_turns == ()
    history = "\n".join(backend.requests[2].history_turns)
    assert first_args["step"] in history
    assert "PEER_first" not in history
    assert "PEER_second" not in history
    assert "invalidat" in history.lower()
    assert "unusab" in history.lower() or "invalid" in history.lower()
    retained = get_thread(continuation_id)
    assert any("PEER_first" in turn.content for turn in retained.turns)
    assert retained.successful_turns == prior_successful_turns + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("prune_consensus", [False, True])
async def test_file_invalidation_masks_prior_clink_synthesis_even_after_consensus_pruning(backend, prune_consensus):
    import server
    from utils.conversation_memory import CONVERSATION_TIMEOUT_SECONDS, get_storage

    async def handler(request):
        content = "DERIVED_SYNTHESIS_QUOTES_PEER_first" if request.target.cli_name == "synthesizer" else None
        return backend.result(request, content=content)

    backend.handler = handler
    context = backend.root / "context.py"
    context.write_text("original context")
    first_args = arguments(execution_mode="parallel", relevant_files=[str(context)])
    complete = payload(await server.handle_call_tool("consensus", first_args))
    continuation = complete["continuation_id"]
    await server.handle_call_tool(
        "clink", {"prompt": "Synthesize the consultation", "cli_name": "synthesizer", "continuation_id": continuation}
    )
    assert "PEER_first" in "\n".join(backend.requests[-1].history_turns)
    saved = get_thread(continuation)
    assert "DERIVED_SYNTHESIS_QUOTES_PEER_first" in saved.turns[-1].content
    if prune_consensus:
        saved.turns = saved.turns[2:]
        saved.omitted_turns += 2
        get_storage().setex(f"thread:{continuation}", CONVERSATION_TIMEOUT_SECONDS, saved.model_dump_json())
    historical_turns = [turn.model_dump() for turn in get_thread(continuation).turns]
    prior_count = get_thread(continuation).successful_turns
    context.write_text("changed context")
    with pytest.raises(ToolExecutionError):
        await server.handle_call_tool("consensus", {**first_args, "continuation_id": continuation})
    assert [turn.model_dump() for turn in get_thread(continuation).turns] == historical_turns
    assert get_thread(continuation).successful_turns == prior_count
    response = payload(
        await server.handle_call_tool(
            "clink", {"prompt": "Explain usable context", "cli_name": "fresh-reader", "continuation_id": continuation}
        )
    )
    replay = "\n".join(backend.requests[-1].history_turns)
    assert "PEER_first" not in replay and "PEER_second" not in replay
    assert "DERIVED_SYNTHESIS" not in replay
    assert "downstream synthesis are unusable" in replay
    assert response["metadata"]["replay"]["warnings"]
    assert response["metadata"]["replay"]["consensus_invalidated"] is True
    assert len(backend.requests) == 4


@pytest.mark.asyncio
async def test_file_invalidation_rejects_saved_debug_findings_and_cached_step_before_delegation(backend):
    import server

    context = backend.root / "context.py"
    context.write_text("original context")
    first_args = arguments(execution_mode="parallel", relevant_files=[str(context)])
    complete = payload(await server.handle_call_tool("consensus", first_args))
    continuation = complete["continuation_id"]
    debug_args = {
        "step": "Investigate a finding from PEER_first",
        "step_number": 1,
        "total_steps": 2,
        "next_step_required": True,
        "findings": "NAMESPACE_FINDINGS_DERIVED_FROM_PEER_first",
        "use_assistant_model": False,
        "continuation_id": continuation,
    }
    await server.handle_call_tool("debug", debug_args)
    before = get_thread(continuation)
    assert "NAMESPACE_FINDINGS_DERIVED" in str(before.workflow_states["debug"])
    context.write_text("changed context")
    with pytest.raises(ToolExecutionError):
        await server.handle_call_tool("consensus", {**first_args, "continuation_id": continuation})
    for request in (
        debug_args,
        {**debug_args, "step_number": 2, "next_step_required": False, "use_assistant_model": True},
    ):
        with pytest.raises(ToolExecutionError) as error:
            await server.handle_call_tool("debug", request)
        import json

        assert json.loads(str(error.value))["error"]["code"] == "consensus_invalidated"
    after = get_thread(continuation)
    assert after.workflow_states["debug"] == before.workflow_states["debug"]
    assert after.successful_turns == before.successful_turns
    assert len(backend.requests) == 2
