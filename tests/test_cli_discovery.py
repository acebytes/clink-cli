"""Local discovery and delegated search contracts, without native inference."""

import json
from types import SimpleNamespace

import pytest

import server
import tools.apilookup as lookup
import tools.listmodels as discovery
from clink.contracts import BackendError, CLIResult
from tools.shared.exceptions import ToolExecutionError


@pytest.mark.asyncio
async def test_discovery_does_not_resolve_or_execute(monkeypatch):
    clients = [{"cli_name": "fixture", "available": False, "roles": ["default"], "default_model": "native-model"}]
    backend = SimpleNamespace(describe=lambda: clients)
    monkeypatch.setattr(discovery, "get_backend", lambda: backend)
    data = json.loads((await server.handle_call_tool("listmodels", {}))[0].text)
    assert data["clients"] == clients
    assert data["authentication"].startswith("unknown")


@pytest.mark.asyncio
async def test_lookup_requests_verified_search_and_exposes_sources(monkeypatch):
    calls = []

    async def run(request):
        calls.append(request)
        return CLIResult(
            "Research answer",
            "success",
            request.request_id,
            "fixture",
            "default",
            None,
            None,
            0.1,
            metadata={"search_performed": True, "source_urls": ["https://example.org/docs"]},
        )

    monkeypatch.setattr(
        lookup,
        "get_backend",
        lambda: SimpleNamespace(
            resolve=lambda **kwargs: None, run=run, validate_files=lambda target, files, images, tool_name: tuple(files)
        ),
    )
    data = json.loads((await server.handle_call_tool("apilookup", {"prompt": "Find current docs"}))[0].text)
    assert calls[0].require_search is True
    assert data["sources"] == ["https://example.org/docs"]
    assert data["continuation_id"]


@pytest.mark.asyncio
async def test_lookup_forwards_attachments_and_revalidates_replayed_images(monkeypatch, tmp_path):
    source = tmp_path / "example.py"
    source.write_text("answer = 42\n")
    picture = str(tmp_path / "diagram.png")
    calls = []
    validations = []
    reject_images = False

    def resolve(**kwargs):
        if reject_images and kwargs.get("images"):
            raise BackendError("unsupported_capability", "This CLI does not support images.")

    def validate_files(target, files, images, tool_name):
        validations.append((tuple(files), tuple(images), tool_name))
        return tuple(files)

    async def run(request):
        calls.append(request)
        return CLIResult("Answer", "success", request.request_id, "fixture", "default", None, None, 0.1)

    backend = SimpleNamespace(resolve=resolve, run=run, validate_files=validate_files)
    monkeypatch.setattr(lookup, "get_backend", lambda: backend)
    first = json.loads(
        (
            await server.handle_call_tool(
                "apilookup", {"prompt": "Research this API", "absolute_file_paths": [str(source)], "images": [picture]}
            )
        )[0].text
    )
    assert calls[0].files == (str(source),)
    assert calls[0].images == (picture,)
    assert validations == [((str(source),), (picture,), "apilookup")]

    reject_images = True
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool(
            "apilookup", {"prompt": "Continue with another CLI", "continuation_id": first["continuation_id"]}
        )
    assert json.loads(caught.value.payload)["error"]["code"] == "unsupported_capability"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_disabled_tools_and_metadata_schema_are_local(monkeypatch):
    monkeypatch.setenv("DISABLED_TOOLS", "chat,analyze,version,listmodels")
    filtered = server.filter_disabled_tools(server.TOOLS)
    assert "chat" not in filtered and "analyze" not in filtered
    assert "version" in filtered and "listmodels" in filtered
    with pytest.raises(ToolExecutionError) as caught:
        await server.handle_call_tool("version", {"model": "unexpected"})
    assert json.loads(caught.value.payload)["error"]["code"] == "invalid_request"
