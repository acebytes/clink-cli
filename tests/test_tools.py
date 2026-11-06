"""Exercise retained tool metadata, requests and path validation with a fake CLI boundary."""

import json

import pytest

from tools import AnalyzeTool, ChatTool, CodeReviewTool, ThinkDeepTool
from tools.models import ToolOutput
from tools.shared.exceptions import ToolExecutionError


@pytest.mark.parametrize(
    "tool_class,name", [(ThinkDeepTool, "thinkdeep"), (CodeReviewTool, "codereview"), (AnalyzeTool, "analyze")]
)
def test_tool_metadata(tool_class, name):
    tool = tool_class()
    assert tool.get_name() == name
    assert tool.get_default_temperature() == 1.0
    schema = tool.get_input_schema()
    assert {"step", "step_number", "total_steps", "next_step_required", "findings"} <= set(schema["required"])
    assert "model" not in schema["required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", [ThinkDeepTool, CodeReviewTool, AnalyzeTool])
async def test_execute_success_uses_cli(tool_class, cli_backend, tmp_path):
    source = tmp_path / "subject.py"
    source.write_text("def subject(): pass\n")
    result = await tool_class().execute(
        {
            "step": "Assess the input boundary.",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "findings": "Input validation needs review.",
            "relevant_files": [str(source)],
            "confidence": "medium",
            "model": "native-exact",
        }
    )
    payload = json.loads(result[0].text)
    assert payload["status"] == "calling_expert_analysis"
    assert payload["expert_analysis"]["raw_analysis"] == cli_backend.content
    assert len(cli_backend.requests) == 1
    assert cli_backend.requests[0].files == (str(source),)


@pytest.mark.asyncio
async def test_thinkdeep_tool_relative_path_rejected(cli_backend):
    with pytest.raises(ToolExecutionError) as error:
        await ThinkDeepTool().execute(
            {
                "step": "Review an input.",
                "step_number": 1,
                "total_steps": 1,
                "next_step_required": False,
                "findings": "Initial findings",
                "files_checked": ["./local/file.py"],
            }
        )
    assert "absolute" in json.loads(error.value.payload)["content"].lower()
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_chat_tool_relative_path_rejected(cli_backend, tmp_path):
    with pytest.raises(ToolExecutionError) as error:
        await ChatTool().execute(
            {
                "prompt": "Explain this code",
                "absolute_file_paths": ["code.py"],
                "working_directory_absolute_path": str(tmp_path),
            }
        )
    assert "absolute" in json.loads(error.value.payload)["content"].lower()
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_analyze_tool_accepts_absolute_paths(cli_backend, tmp_path):
    source = tmp_path / "subject.py"
    source.write_text("value = 3\n")
    await AnalyzeTool().execute(
        {
            "step": "Analyze the source.",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "findings": "The input is unvalidated.",
            "relevant_files": [str(source)],
        }
    )
    assert cli_backend.requests[0].files == (str(source),)


def test_trace_complete_status_in_registry():
    from tools.tracer import TracerTool

    assert TracerTool().get_completion_status() == "tracing_complete"


def test_trace_complete_model_validation():
    assert ToolOutput(status="trace_complete", content="Trace is complete").status == "trace_complete"
