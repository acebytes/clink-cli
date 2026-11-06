"""File context passes through the backend once; local workflow steps never read it."""

import pytest

from clink.contracts import BackendError
from tools.analyze import AnalyzeTool
from tools.codereview import CodeReviewTool
from tools.debug import DebugIssueTool
from tools.shared.exceptions import ToolExecutionError


def arguments(path, **overrides):
    values = {
        "step": "Examine the input boundary.",
        "step_number": 1,
        "total_steps": 2,
        "next_step_required": True,
        "findings": "The input is unchecked.",
        "relevant_files": [str(path)],
        "confidence": "medium",
    }
    values.update(overrides)
    return values


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "subject.py"
    path.write_text("FILE_BODY_SENTINEL = 'only the backend embeds this'\n")
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", [AnalyzeTool, CodeReviewTool])
async def test_intermediate_step_references_files_without_read_or_launch(tool_class, cli_backend, source, monkeypatch):
    monkeypatch.setattr("utils.file_utils.read_file_content", lambda *args, **kw: pytest.fail("local step read a file"))
    await tool_class().execute(arguments(source))
    assert cli_backend.validations == []
    assert cli_backend.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", [CodeReviewTool, DebugIssueTool])
async def test_final_step_forwards_files_without_duplicate_embedding(tool_class, cli_backend, source, monkeypatch):
    monkeypatch.setattr(
        "utils.file_utils.read_file_content", lambda *args, **kw: pytest.fail("tool embedded file body")
    )
    await tool_class().execute(arguments(source, next_step_required=False))
    request = cli_backend.requests[0]
    assert request.files == (str(source),)
    assert "FILE_BODY_SENTINEL" not in request.prompt


@pytest.mark.asyncio
async def test_expert_files_are_unique_and_deterministic(cli_backend, source, tmp_path):
    other = tmp_path / "other.py"
    other.write_text("value = 1\n")
    await CodeReviewTool().execute(
        arguments(source, next_step_required=False, relevant_files=[str(source), str(other), str(source)])
    )
    assert cli_backend.requests[0].files == tuple(sorted([str(source), str(other)]))


@pytest.mark.asyncio
async def test_admission_error_prevents_file_reads_and_launch(cli_backend, source, monkeypatch):
    cli_backend.error = BackendError("path_not_allowed", "Outside approved roots.")
    monkeypatch.setattr("utils.file_utils.read_file_content", lambda *args, **kw: pytest.fail("read before admission"))
    with pytest.raises(ToolExecutionError):
        await CodeReviewTool().execute(arguments(source, next_step_required=False))
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_opt_out_keeps_final_files_local(cli_backend, source, monkeypatch):
    monkeypatch.setattr("utils.file_utils.read_file_content", lambda *args, **kw: pytest.fail("opt-out read a file"))
    await CodeReviewTool().execute(arguments(source, next_step_required=False, use_assistant_model=False))
    assert cli_backend.validations == []
    assert cli_backend.requests == []
