"""Clarification remains caller-driven when expert analysis uses a CLI."""

import json

import pytest

from clink.contracts import BackendError
from server import handle_call_tool
from tools.analyze import AnalyzeTool
from tools.debug import DebugIssueTool
from tools.models import FilesNeededRequest, ToolOutput
from tools.shared.exceptions import ToolExecutionError


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "subject.py"
    path.write_text("import missing_dependency\n")
    return path


def arguments(source, **overrides):
    values = {
        "step": "Analyze project dependencies.",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "findings": "A dependency is not declared.",
        "relevant_files": [str(source)],
        "confidence": "medium",
    }
    values.update(overrides)
    return values


def clarification(**overrides):
    values = {
        "status": "files_required_to_continue",
        "mandatory_instructions": "Inspect package.json first.",
        "files_needed": ["package.json"],
    }
    values.update(overrides)
    return json.dumps(values)


@pytest.mark.asyncio
async def test_clarification_request_parsing(cli_backend, source):
    cli_backend.content = clarification()
    result = await AnalyzeTool().execute(arguments(source))
    payload = json.loads(result[0].text)
    assert payload["status"] == "files_required_to_continue"
    assert json.loads(payload["content"])["files_needed"] == ["package.json"]
    assert "Provide the requested files" in payload["next_steps"]
    assert len(cli_backend.requests) == 1


@pytest.mark.asyncio
async def test_normal_response_not_parsed_as_clarification(cli_backend):
    result = await DebugIssueTool().execute(
        {
            "step": "Investigate an import error.",
            "step_number": 1,
            "total_steps": 3,
            "next_step_required": True,
            "findings": "The module is not imported.",
            "confidence": "high",
        }
    )
    payload = json.loads(result[0].text)
    assert payload["status"] == "pause_for_investigation"
    assert payload["investigation_required"] is True
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_malformed_clarification_request_treated_as_normal(cli_backend, source):
    cli_backend.content = '{"status":"files_required_to_continue", "prompt":"missing brace"'
    result = await AnalyzeTool().execute(arguments(source))
    payload = json.loads(result[0].text)
    assert payload["status"] == "calling_expert_analysis"
    assert payload["expert_analysis"]["raw_analysis"] == cli_backend.content


@pytest.mark.asyncio
async def test_clarification_with_suggested_action(cli_backend, source):
    action = {"tool": "analyze", "args": {"step": "Inspect the manifest."}}
    cli_backend.content = clarification(suggested_next_action=action)
    result = await AnalyzeTool().execute(arguments(source))
    payload = json.loads(result[0].text)
    assert json.loads(payload["content"])["suggested_next_action"] == action
    assert len(cli_backend.requests) == 1


def test_tool_output_model_serialization():
    output = ToolOutput(
        status="success", content="Useful context", content_type="markdown", metadata={"tool_name": "analyze"}
    )
    parsed = json.loads(output.model_dump_json())
    assert parsed["content"] == "Useful context"
    assert parsed["content_type"] == "markdown"
    assert parsed["metadata"]["tool_name"] == "analyze"


def test_clarification_request_model():
    request = FilesNeededRequest(
        mandatory_instructions="Need context",
        files_needed=["module.py"],
        suggested_next_action={"tool": "analyze", "args": {}},
    )
    assert request.status == "files_required_to_continue"
    assert request.files_needed == ["module.py"]
    assert request.suggested_next_action["tool"] == "analyze"


@pytest.mark.asyncio
async def test_error_response_format(cli_backend, source):
    cli_backend.error = BackendError("authentication_required", "Native login is required.")
    with pytest.raises(ToolExecutionError) as error:
        await AnalyzeTool().execute(arguments(source))
    payload = json.loads(error.value.payload)
    assert payload["status"] == "error"
    assert payload["metadata"]["code"] == "authentication_required"
    assert "Native login" in payload["content"]


@pytest.mark.asyncio
async def test_dependency_analysis_triggers_clarification(cli_backend, source):
    cli_backend.content = clarification(files_needed=["package.json", "package-lock.json"])
    result = await handle_call_tool("analyze", arguments(source))
    payload = json.loads(result[0].text)
    assert payload["status"] == "files_required_to_continue"
    assert json.loads(payload["content"])["files_needed"] == ["package.json", "package-lock.json"]
    assert payload["continuation_id"]


@pytest.mark.asyncio
async def test_multi_step_collaboration(cli_backend, source, tmp_path):
    cli_backend.content = clarification()
    first = json.loads((await handle_call_tool("analyze", arguments(source)))[0].text)
    manifest = tmp_path / "package.json"
    manifest.write_text('{"dependencies": {}}')
    cli_backend.content = "The declared dependency is missing."
    second = json.loads(
        (
            await handle_call_tool(
                "analyze",
                arguments(
                    source,
                    step="Inspect the supplied manifest.",
                    step_number=2,
                    total_steps=2,
                    relevant_files=[str(source), str(manifest)],
                    continuation_id=first["continuation_id"],
                ),
            )
        )[0].text
    )
    assert second["status"] == "calling_expert_analysis"
    assert second["expert_analysis"]["raw_analysis"] == cli_backend.content
    assert len(cli_backend.requests) == 2
    assert str(manifest) in cli_backend.requests[1].files
