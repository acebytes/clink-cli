"""Preserve caller-driven planner behavior through server-owned transactions."""

import json

import pytest

from server import handle_call_tool
from tools.models import ToolModelCategory
from tools.planner import PlannerRequest, PlannerTool
from tools.shared.exceptions import ToolExecutionError
from utils.conversation_memory import get_thread


def arguments(**overrides):
    values = {"step": "Plan a small feature.", "step_number": 1, "total_steps": 3, "next_step_required": True}
    values.update(overrides)
    return values


async def execute(**overrides):
    result = await handle_call_tool("planner", arguments(**overrides))
    return json.loads(result[0].text)


def test_tool_metadata():
    tool = PlannerTool()
    assert tool.get_name() == "planner"
    assert "sequential planning" in tool.get_description()
    assert tool.get_default_temperature() == 1.0
    assert tool.get_model_category() == ToolModelCategory.EXTENDED_REASONING
    assert tool.get_default_thinking_mode() == "medium"


def test_request_validation():
    request = PlannerRequest(**arguments(step_number=3, total_steps=10))
    assert request.step_number == 3
    assert request.is_step_revision is False
    with pytest.raises(ValueError):
        PlannerRequest(step="Incomplete request")


def test_input_schema_generation():
    schema = PlannerTool().get_input_schema()
    fields = schema["properties"]
    assert {"step", "step_number", "total_steps", "next_step_required"} <= set(schema["required"])
    assert {"is_step_revision", "is_branch_point", "branch_id", "continuation_id"} <= set(fields)
    assert not {"model", "cli_name", "role", "images", "temperature", "thinking_mode"} & set(fields)


def test_model_category_does_not_trigger_delegation(cli_backend):
    assert PlannerTool().requires_model() is False
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_execute_first_step(cli_backend):
    payload = await execute(total_steps=10)
    assert payload["step_number"] == 1
    assert payload["status"] == "pause_for_deep_thinking"
    assert payload["thinking_required"] is True
    assert payload["continuation_id"]
    assert "MANDATORY: DO NOT call the planner tool again immediately" in payload["next_steps"]
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_execute_subsequent_step():
    first = await execute(total_steps=8)
    payload = await execute(
        step="Choose the deployment boundary.", step_number=2, total_steps=8, continuation_id=first["continuation_id"]
    )
    assert payload["status"] == "pause_for_deep_thinking"
    assert payload["step_number"] == 2
    assert payload["continuation_id"] == first["continuation_id"]
    assert "reflection" in payload["next_steps"]


@pytest.mark.asyncio
async def test_execute_with_continuation_context():
    first = await execute(step="Record original migration scope.")
    await execute(step="Choose a rollback plan.", step_number=2, continuation_id=first["continuation_id"])
    state = get_thread(first["continuation_id"]).workflow_states["planner"]
    assert state["initial_request"] == "Record original migration scope."
    assert [item["step"] for item in state["work_history"]] == [
        "Record original migration scope.",
        "Choose a rollback plan.",
    ]


@pytest.mark.asyncio
async def test_execute_final_step(cli_backend):
    payload = await execute(step="Deploy and monitor the system.", next_step_required=False)
    assert payload["status"] == "planning_complete"
    assert payload["planning_complete"] is True
    assert "COMPLETE PLAN:" in payload["plan_summary"]
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_execute_with_branching():
    first = await execute(step="Evaluate two rollout paths.")
    payload = await execute(
        step="Choose phased rollout.",
        step_number=2,
        is_branch_point=True,
        branch_from_step=1,
        branch_id="phased",
        continuation_id=first["continuation_id"],
    )
    assert payload["metadata"]["branches"] == ["phased"]
    state = get_thread(first["continuation_id"]).workflow_states["planner"]
    assert "phased" in state["branches"]


@pytest.mark.asyncio
async def test_execute_with_revision():
    first = await execute(step="Choose REST initially.")
    payload = await execute(
        step="Revise the boundary to GraphQL.",
        step_number=2,
        is_step_revision=True,
        revises_step_number=1,
        continuation_id=first["continuation_id"],
    )
    assert payload["metadata"]["is_step_revision"] is True
    assert payload["metadata"]["revises_step_number"] == 1
    state = get_thread(first["continuation_id"]).workflow_states["planner"]
    assert state["work_history"][-1]["is_step_revision"] is True


@pytest.mark.asyncio
async def test_execute_adjusts_total_steps():
    payload = await execute(step_number=8, total_steps=5)
    assert payload["step_number"] == payload["total_steps"] == 8
    assert payload["status"] == "pause_for_planning"


@pytest.mark.asyncio
async def test_execute_error_handling():
    with pytest.raises(ToolExecutionError) as error:
        await handle_call_tool("planner", {"step": "Incomplete request"})
    payload = json.loads(error.value.payload)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_execute_step_history_tracking():
    first = await execute(step="First step")
    second = await execute(step="Second step", step_number=2, continuation_id=first["continuation_id"])
    history = get_thread(second["continuation_id"]).workflow_states["planner"]["work_history"]
    assert len(history) == 2
    assert [item["step"] for item in history] == ["First step", "Second step"]


@pytest.mark.asyncio
async def test_interactive_planning_flow():
    payload = await execute(step="Plan a system redesign.", total_steps=5)
    assert payload["status"] == "pause_for_deep_thinking"
    assert payload["thinking_required"] is True
    assert get_thread(payload["continuation_id"]).successful_turns == 2


@pytest.mark.asyncio
async def test_simple_planning_flow():
    payload = await execute()
    assert payload["status"] == "pause_for_planning"
    assert "thinking_required" not in payload
    assert "Continue with step 2" in payload["next_steps"]
