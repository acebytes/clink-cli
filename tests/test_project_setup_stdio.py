"""Exercise generated project registrations through independent MCP stdio servers."""

import json
from contextlib import AsyncExitStack

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from clink.setup import setup_project


@pytest.mark.asyncio
async def test_generated_registrations_keep_two_project_servers_independent(tmp_path):
    home = tmp_path / "synthetic-home"
    home.mkdir()
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    projects = [tmp_path / "project one", tmp_path / "project two"]
    for index, project in enumerate(projects):
        project.mkdir()
        setup_project(project)
        manifest = project / ".clink" / "cli_clients" / "agy.json"
        config = json.loads(manifest.read_text())
        config["default_model"] = f"project-{index}-selection"
        manifest.write_text(json.dumps(config))

    async with AsyncExitStack() as stack:
        sessions = []
        for project in projects:
            registration = json.loads((project / ".mcp.json").read_text())["mcpServers"]["clink"]
            parameters = StdioServerParameters(
                **registration,
                cwd=str(tmp_path),
                env={
                    "PATH": str(empty_path),
                    "HOME": str(home),
                    "PYTHONNOUSERSITE": "1",
                    "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
                    "LOG_LEVEL": "ERROR",
                },
            )
            read, write = await stack.enter_async_context(stdio_client(parameters))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            sessions.append(session)

        for index in (0, 1, 0):
            response = await sessions[index].call_tool("listmodels", {})
            assert not response.isError
            clients = json.loads(response.content[0].text)["clients"]
            assert len(clients) == 5
            assert all(client["allowed_roots"] == [str(projects[index].resolve())] for client in clients)
            assert all(not client["installed"] and not client["available"] for client in clients)
            agy = next(client for client in clients if client["cli_name"] == "agy")
            assert agy["default_model"] == f"project-{index}-selection"
