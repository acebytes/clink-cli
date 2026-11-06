"""Real stdio MCP transport with no installed CLIs or provider credentials."""

import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_stdio_discovery_local_workflows_and_errors(tmp_path):
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "server.py")],
        cwd=str(root),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "PYTHONNOUSERSITE": "1",
            "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
            "LOG_LEVEL": "ERROR",
        },
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "clink-mcp-server"
            schemas = await session.list_tools()
            assert len(schemas.tools) == 18
            prompts = await session.list_prompts()
            assert prompts.prompts
            prompt = await session.get_prompt("chat")
            assert prompt.messages
            for name, arguments in (
                ("version", {}),
                ("listmodels", {}),
                ("challenge", {"prompt": "A claim"}),
                ("planner", {"step": "Plan complete", "step_number": 1, "total_steps": 1, "next_step_required": False}),
                (
                    "docgen",
                    {
                        "step": "Docs complete",
                        "step_number": 2,
                        "total_steps": 2,
                        "next_step_required": False,
                        "findings": "Documented",
                        "num_files_documented": 1,
                        "total_files_to_document": 1,
                        "document_complexity": True,
                        "document_flow": True,
                        "update_existing": True,
                        "comments_on_complex_logic": True,
                    },
                ),
                (
                    "tracer",
                    {
                        "step": "Trace complete",
                        "step_number": 1,
                        "total_steps": 1,
                        "next_step_required": False,
                        "findings": "Traced",
                        "target_description": "add",
                        "trace_mode": "precision",
                    },
                ),
            ):
                response = await session.call_tool(name, arguments)
                assert not response.isError, (name, response.content)
                assert json.loads(response.content[0].text)
            response = await session.call_tool("clink", {"prompt": "No CLI is installed"})
            assert response.isError is True
            payload = json.loads(response.content[0].text)
            assert (
                payload.get("metadata", {}).get("code") == "cli_missing"
                or payload.get("error", {}).get("code") == "cli_missing"
            )
