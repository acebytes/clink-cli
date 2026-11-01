"""Replay reaches the CLI separately from each tool's current consumed field."""

import json

import pytest

from server import handle_call_tool
from tools.shared.exceptions import ToolExecutionError


@pytest.mark.asyncio
async def test_conversation_history_field_mapping(cli_backend, tmp_path):
    source = tmp_path / "subject.py"
    source.write_text("value = 1\n")
    for name in ["analyze", "chat", "debug", "codereview", "thinkdeep"]:
        first = json.loads(
            (
                await handle_call_tool(
                    "chat",
                    {"prompt": "Prior conversational context.", "working_directory_absolute_path": str(tmp_path)},
                )
            )[0].text
        )
        identifier = first["continuation_offer"]["continuation_id"]
        current = "Inspect this current request for " + name
        if name == "chat":
            arguments = {"prompt": current, "working_directory_absolute_path": str(tmp_path)}
        else:
            arguments = {
                "step": current,
                "step_number": 1,
                "total_steps": 1,
                "next_step_required": False,
                "findings": "A meaningful mismatch is visible.",
                "relevant_files": [str(source)],
                "confidence": "medium",
            }
        await handle_call_tool(name, {**arguments, "continuation_id": identifier})
        request = cli_backend.requests[-1]
        assert current in request.prompt
        assert "Prior conversational context." not in request.prompt
        assert any("Prior conversational context." in turn for turn in request.history_turns)
        assert request.tool_name == name


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_before_replay_or_launch(cli_backend):
    with pytest.raises(ToolExecutionError) as error:
        await handle_call_tool("unknown_tool", {"prompt": "User input"})
    assert json.loads(error.value.payload)["error"]["code"] == "unknown_tool"
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_tool_parameter_standardization():
    """Test that workflow tools use standardized investigation pattern"""
    from tools.analyze import AnalyzeWorkflowRequest
    from tools.codereview import CodeReviewRequest
    from tools.debug import DebugInvestigationRequest
    from tools.precommit import PrecommitRequest
    from tools.thinkdeep import ThinkDeepWorkflowRequest

    # Test analyze tool uses workflow pattern
    analyze = AnalyzeWorkflowRequest(
        step="What does this do?",
        step_number=1,
        total_steps=1,
        next_step_required=False,
        findings="Initial analysis",
        relevant_files=["/test.py"],
    )
    assert analyze.step == "What does this do?"

    # Debug tool now uses self-investigation pattern with different fields
    debug = DebugInvestigationRequest(
        step="Investigating error",
        step_number=1,
        total_steps=3,
        next_step_required=True,
        findings="Initial error analysis",
    )
    assert debug.step == "Investigating error"
    assert debug.findings == "Initial error analysis"

    # Test codereview tool uses workflow fields
    review = CodeReviewRequest(
        step="Initial code review investigation",
        step_number=1,
        total_steps=2,
        next_step_required=True,
        findings="Initial review findings",
        relevant_files=["/test.py"],
    )
    assert review.step == "Initial code review investigation"
    assert review.findings == "Initial review findings"

    # Test thinkdeep tool uses workflow pattern
    think = ThinkDeepWorkflowRequest(
        step="My analysis", step_number=1, total_steps=1, next_step_required=False, findings="Initial thinking analysis"
    )
    assert think.step == "My analysis"

    # Test precommit tool uses workflow fields
    precommit = PrecommitRequest(
        step="Validating changes for commit",
        step_number=1,
        total_steps=2,
        next_step_required=True,
        findings="Initial validation findings",
        path="/repo",  # path only needed for step 1
    )
    assert precommit.step == "Validating changes for commit"
    assert precommit.findings == "Initial validation findings"
