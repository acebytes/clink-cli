"""Retain basic MCP entrypoint behavior without provider setup or network calls."""

import json

import pytest

from server import handle_call_tool
from tools.shared.exceptions import ToolExecutionError


@pytest.mark.asyncio
async def test_handle_call_tool_unknown():
    with pytest.raises(ToolExecutionError) as error:
        await handle_call_tool("unknown_tool", {})
    assert json.loads(error.value.payload)["error"]["code"] == "unknown_tool"


@pytest.mark.asyncio
async def test_handle_chat(cli_backend, tmp_path):
    result = await handle_call_tool("chat", {"prompt": "Hello", "working_directory_absolute_path": str(tmp_path)})
    payload = json.loads(result[0].text)
    assert payload["status"] == "continuation_available"
    assert payload["continuation_offer"]["continuation_id"]
    assert payload["metadata"]["backend"]["cli_name"] == "codex"
    assert len(cli_backend.requests) == 1


@pytest.mark.asyncio
async def test_handle_version(cli_backend):
    payload = json.loads((await handle_call_tool("version", {}))[0].text)
    assert payload["name"] == "clink-mcp-server"
    assert payload["status"] == "success"
    assert payload["source"]["repository"] == "https://github.com/BeehiveInnovations/pal-mcp-server"
    assert payload["source"]["commit"] == "7afc7c1cc96e23992c8f105f960132c657883bb1"
    assert cli_backend.requests == []
