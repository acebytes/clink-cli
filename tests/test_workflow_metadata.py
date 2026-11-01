"""Workflow metadata preserves CLI identity without claiming unknown actual models."""

import json

import pytest

from tools.debug import DebugIssueTool
from tools.shared.exceptions import ToolExecutionError


def arguments(**overrides):
    values = {
        "step": "Investigate a reproducible failure.",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "findings": "The request fails at validation.",
        "confidence": "medium",
        "model": "native-exact",
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_workflow_metadata_in_response(cli_backend, tmp_path):
    source = tmp_path / "subject.py"
    source.write_text("value = 1\n")
    result = await DebugIssueTool().execute(arguments(relevant_files=[str(source)]))
    metadata = json.loads(result[0].text)["metadata"]
    assert metadata["tool_name"] == "debug"
    assert metadata["backend"] == {"cli_name": "codex", "role": "default"}
    assert metadata["requested_model"] == "native-exact"
    assert metadata["actual_model"] is None
    assert metadata["status"] == "success"
    assert metadata["request_id"] == cli_backend.requests[0].request_id


@pytest.mark.asyncio
async def test_workflow_metadata_in_error_response():
    with pytest.raises(ToolExecutionError) as error:
        await DebugIssueTool().execute(arguments(step_number="private-invalid-input-sentinel"))
    payload = json.loads(error.value.payload)
    assert payload["status"] == "error"
    assert payload["metadata"]["code"] == "invalid_request"
    assert "private-invalid-input-sentinel" not in error.value.payload
    assert payload["metadata"]["tool_name"] == "debug"
    assert "backend" not in payload["metadata"]


@pytest.mark.asyncio
async def test_local_workflow_metadata_does_not_invent_backend(cli_backend):
    result = await DebugIssueTool().execute(arguments(next_step_required=True, total_steps=2))
    payload = json.loads(result[0].text)
    assert payload["metadata"] == {"tool_name": "debug"}
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_workflow_metadata_preserves_existing_response_fields(cli_backend):
    from tools.planner import PlannerTool

    result = await PlannerTool().execute(
        {
            "step": "Choose a deployment path.",
            "step_number": 1,
            "total_steps": 3,
            "next_step_required": True,
            "is_branch_point": True,
            "branch_from_step": 1,
            "branch_id": "safe-path",
        }
    )
    payload = json.loads(result[0].text)
    assert payload["metadata"]["tool_name"] == "planner"
    assert payload["metadata"]["branches"] == ["safe-path"]
    assert payload["metadata"]["is_branch_point"] is True
    assert payload["step_content"] == "Choose a deployment path."
    assert cli_backend.requests == []
