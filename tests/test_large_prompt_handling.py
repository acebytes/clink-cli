"""Validate byte limits, primary prompt attachments and context forwarding without models."""

import json

import pytest

from clink.contracts import BackendError
from config import MCP_PROMPT_SIZE_LIMIT
from tests.cli_helpers import context_target
from tools.chat import ChatTool
from tools.codereview import CodeReviewTool
from tools.shared.exceptions import ToolExecutionError


def chat_arguments(tmp_path, **overrides):
    values = {"prompt": "A normal question.", "working_directory_absolute_path": str(tmp_path)}
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_chat_large_prompt_detection(cli_backend, tmp_path):
    with pytest.raises(ToolExecutionError) as error:
        await ChatTool().execute(chat_arguments(tmp_path, prompt="x" * (MCP_PROMPT_SIZE_LIMIT + 1)))
    payload = json.loads(error.value.payload)
    assert payload["metadata"]["code"] == "input_limit"
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_chat_normal_prompt_works(cli_backend, tmp_path):
    result = await ChatTool().execute(chat_arguments(tmp_path))
    assert json.loads(result[0].text)["status"] == "success"
    assert "A normal question." in cli_backend.requests[0].prompt


@pytest.mark.asyncio
async def test_chat_prompt_file_handling(cli_backend, tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Detailed question from the explicit attachment.\n", encoding="utf-8")
    await ChatTool().execute(chat_arguments(tmp_path, absolute_file_paths=[str(prompt)]))
    request = cli_backend.requests[0]
    assert "Detailed question from the explicit attachment." in request.prompt
    assert request.files == ()


@pytest.mark.asyncio
async def test_workflow_large_step_is_rejected_before_launch(cli_backend):
    with pytest.raises(ToolExecutionError) as error:
        await CodeReviewTool().execute(
            {
                "step": "x" * (MCP_PROMPT_SIZE_LIMIT + 1),
                "step_number": 2,
                "total_steps": 2,
                "next_step_required": False,
                "findings": "Initial observations.",
            }
        )
    payload = json.loads(error.value.payload)
    assert payload["metadata"]["code"] == "input_limit"
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_multiple_files_with_prompt_txt(cli_backend, tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Main request text.\n", encoding="utf-8")
    source = tmp_path / "subject.py"
    source.write_text("value = 1\n")
    await ChatTool().execute(chat_arguments(tmp_path, absolute_file_paths=[str(prompt), str(source)]))
    request = cli_backend.requests[0]
    assert request.files == (str(source),)
    assert request.prompt.count("Main request text.") == 1


def test_boundary_case_exactly_at_limit():
    assert ChatTool().check_prompt_size("x" * MCP_PROMPT_SIZE_LIMIT) is None


def test_boundary_case_just_over_limit():
    payload = ChatTool().check_prompt_size("x" * (MCP_PROMPT_SIZE_LIMIT + 1))
    assert payload["metadata"]["code"] == "input_limit"
    assert payload["metadata"]["prompt_size"] == MCP_PROMPT_SIZE_LIMIT + 1


@pytest.mark.asyncio
async def test_empty_prompt_without_file_remains_explicit(cli_backend, tmp_path):
    await ChatTool().execute(chat_arguments(tmp_path, prompt=""))
    assert len(cli_backend.requests) == 1
    assert "=== USER REQUEST ===" in cli_backend.requests[0].prompt


def test_prompt_file_read_error(tmp_path):
    tool = ChatTool()
    tool._backend_target = context_target(tmp_path)
    with pytest.raises(BackendError) as error:
        tool.handle_prompt_file([str(tmp_path / "prompt.txt")])
    assert error.value.code == "path_not_allowed"


@pytest.mark.asyncio
async def test_large_file_context_is_forwarded_for_backend_budgeting(cli_backend, tmp_path):
    source = tmp_path / "context.py"
    source.write_text("CONTEXT_BODY_SENTINEL" * 10000)
    await ChatTool().execute(chat_arguments(tmp_path, absolute_file_paths=[str(source)]))
    request = cli_backend.requests[0]
    assert request.files == (str(source),)
    assert "CONTEXT_BODY_SENTINEL" not in request.prompt


def test_utf8_bytes_define_boundary():
    payload = ChatTool().check_prompt_size("界" * (MCP_PROMPT_SIZE_LIMIT // 3 + 1))
    assert payload["metadata"]["code"] == "input_limit"
    assert payload["metadata"]["prompt_size"] > MCP_PROMPT_SIZE_LIMIT


def test_primary_prompt_file_cannot_bypass_byte_limit(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_bytes(b"x" * (MCP_PROMPT_SIZE_LIMIT + 1))
    tool = ChatTool()
    tool._backend_target = context_target(tmp_path)
    with pytest.raises(BackendError) as error:
        tool.handle_prompt_file([str(prompt)])
    assert error.value.code == "input_limit"


def test_raw_prompt_limit_excludes_replay_but_backend_still_owns_assembled_limit():
    tool = ChatTool()
    tool._current_arguments = {"_original_user_prompt": "Current request."}
    history = "old history " * 200000
    assert tool.get_prompt_content_for_size_validation(history) == "Current request."
    assert tool.check_prompt_size(history)["metadata"]["code"] == "input_limit"


def test_prompt_file_requires_admitted_target_before_read(tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Unadmitted context must not be read.")
    monkeypatch.setattr("clink.files.read_context", lambda *args: pytest.fail("read without an admitted target"))
    with pytest.raises(BackendError) as error:
        ChatTool().handle_prompt_file([str(prompt)])
    assert error.value.code == "path_not_allowed"


def test_prompt_file_cannot_escape_admitted_roots(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Outside the declared root.")
    tool = ChatTool()
    tool._backend_target = context_target(approved)
    with pytest.raises(BackendError) as error:
        tool.handle_prompt_file([str(prompt)])
    assert error.value.code == "path_not_allowed"
