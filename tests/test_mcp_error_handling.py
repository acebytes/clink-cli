import json

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from server import server as mcp_server


@pytest.mark.asyncio
async def test_tool_execution_error_sets_is_error_flag_for_mcp_response(monkeypatch):
    """Ensure ToolExecutionError surfaces as CallToolResult with isError=True."""

    handler = mcp_server.request_handlers[CallToolRequest]

    arguments = {
        "prompt": "Trigger working_directory_absolute_path validation failure",
        "working_directory_absolute_path": "relative/path",  # Not absolute -> ToolExecutionError from ChatTool
        "absolute_file_paths": [],
        "model": "native-model",
    }

    request = CallToolRequest(params=CallToolRequestParams(name="chat", arguments=arguments))

    server_result = await handler(request)

    assert server_result.root.isError is True
    assert server_result.root.content, "Expected error response content"

    payload = server_result.root.content[0].text
    data = json.loads(payload)
    assert data["status"] == "error"
    assert "absolute" in data["content"].lower()
