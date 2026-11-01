"""Clink tool behavior at the shared backend boundary, without native inference."""

import json

import pytest

from clink.contracts import BackendError, CLIResult
from tools.clink import CLinkTool
from tools.shared.exceptions import ToolExecutionError


@pytest.mark.asyncio
async def test_clink_routes_one_native_request_and_preserves_normalized_identity(cli_backend):
    result = await CLinkTool().execute(
        {
            "prompt": "Summarize the project",
            "cli_name": "gemini",
            "role": "codereviewer",
            "model": "native-model-exact",
        }
    )
    payload = json.loads(result[0].text)
    assert payload["status"] == "success"
    assert payload["content"] == cli_backend.content
    assert len(cli_backend.requests) == len(cli_backend.resolutions) == 1
    assert cli_backend.resolutions[0]["cli_name"] == "gemini"
    assert cli_backend.resolutions[0]["role"] == "codereviewer"
    assert cli_backend.resolutions[0]["model"] == "native-model-exact"
    metadata = payload["metadata"]
    assert metadata["backend"] == {"cli_name": "gemini", "role": "codereviewer"}
    assert metadata["requested_model"] == "native-model-exact"
    assert metadata["actual_model"] is None
    assert not {"command", "stdout", "stderr", "events", "raw", "session_id"}.intersection(metadata)


@pytest.mark.asyncio
async def test_clink_defaults_are_resolved_by_the_shared_backend(cli_backend):
    payload = json.loads((await CLinkTool().execute({"prompt": "Hello"}))[0].text)
    assert cli_backend.resolutions[0]["cli_name"] is None
    assert cli_backend.resolutions[0]["role"] is None
    assert cli_backend.resolutions[0]["model"] is None
    assert payload["metadata"]["backend"] == {"cli_name": "codex", "role": "default"}
    schema = CLinkTool().get_input_schema()
    # Status/cancel need no prompt; the request model enforces action-specific fields.
    assert "prompt" not in schema.get("required", [])
    assert schema["properties"]["action"]["default"] == "consult"


@pytest.mark.asyncio
async def test_clink_preserves_backend_truncation_and_file_scope_warnings(cli_backend, monkeypatch):
    async def run(request):
        cli_backend.requests.append(request)
        return CLIResult(
            content="A bounded backend excerpt.",
            status="success",
            request_id=request.request_id,
            cli_name="codex",
            role="default",
            requested_model=None,
            actual_model="reported-native",
            duration_seconds=0.42,
            truncated=True,
            warnings=({"code": "native_filesystem_access_disabled", "message": "Declared files are inline."},),
            metadata={"read_scope": "inline-only"},
        )

    monkeypatch.setattr(cli_backend, "run", run)
    payload = json.loads((await CLinkTool().execute({"prompt": "Summarize"}))[0].text)
    assert payload["content"] == "A bounded backend excerpt."
    assert payload["metadata"]["truncated"] is True
    assert payload["metadata"]["actual_model"] == "reported-native"
    assert payload["metadata"]["read_scope"] == "inline-only"
    assert payload["metadata"]["warnings"][0]["code"] == "native_filesystem_access_disabled"
    assert len(cli_backend.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ("nonzero_exit", "authentication_required", "policy_unverified", "input_limit", "output_limit", "timeout")
)
async def test_clink_preserves_backend_failures_without_retry(code, cli_backend, monkeypatch):
    async def run(request):
        cli_backend.requests.append(request)
        raise BackendError(code, "Delegation failed.", "Start a fresh request when ready.")

    monkeypatch.setattr(cli_backend, "run", run)
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute({"prompt": "Inspect"})
    payload = json.loads(error.value.payload)
    assert payload["status"] == "error"
    assert payload["metadata"]["code"] == code
    assert payload["metadata"]["remedy"] == "Start a fresh request when ready."
    assert len(cli_backend.requests) == 1


@pytest.mark.asyncio
async def test_clink_declares_context_and_keeps_contents_for_common_backend(cli_backend, tmp_path):
    path = tmp_path / "subject.py"
    path.write_text("private synthetic source body", encoding="utf-8")
    await CLinkTool().execute({"prompt": "Inspect", "absolute_file_paths": [str(path)]})
    request = cli_backend.requests[0]
    assert request.files == (str(path),)
    assert "private synthetic source body" not in request.prompt
    assert cli_backend.validations == [((str(path),), (), "clink")]
