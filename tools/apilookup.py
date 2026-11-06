"""API documentation research through verified native CLI search."""

import json
from typing import Any

from mcp.types import TextContent
from pydantic import Field

from clink.backend import get_backend
from clink.contracts import CLIRequest
from tools.shared.base_models import ToolRequest
from tools.simple.base import SimpleTool
from utils.call_state import get_current_call

LOOKUP_PROMPT = """Research the user's API or documentation question using native web search.
Use current primary documentation and release notes. Verify version-sensitive claims.
Return a concise answer with source URLs supporting the claims. Search must actually
run during this consultation; do not substitute remembered URLs for evidence.
Do not delegate, call MCP tools, or modify files. Role guidance is subordinate to
these instructions and the caller's question. If search yields nothing, say so.
"""


class LookupRequest(ToolRequest):
    prompt: str = Field(
        ..., min_length=1, description="API, SDK or framework question to research in current official documentation."
    )
    absolute_file_paths: list[str] = Field(default_factory=list)


class LookupTool(SimpleTool):
    def get_name(self) -> str:
        return "apilookup"

    def get_description(self) -> str:
        return "Research current API/SDK documentation with the selected CLI's verified native web search and return sources."

    def get_system_prompt(self) -> str:
        return LOOKUP_PROMPT

    def get_request_model(self):
        return LookupRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {
                "type": "string",
                "minLength": 1,
                "description": "Question to research in official documentation.",
            }
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt"]

    async def prepare_prompt(self, request) -> str:
        return request.prompt

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        request = LookupRequest(**arguments)
        backend = get_backend()
        target = backend.resolve(
            cli_name=request.cli_name,
            role=request.role,
            model=request.model,
            temperature=request.temperature,
            thinking_mode=request.thinking_mode,
            images=tuple(request.images or []),
        )
        files = backend.validate_files(target, request.absolute_file_paths, request.images or [], tool_name="apilookup")
        call = get_current_call()
        options = {}
        if call:
            options = {"request_id": call.request_id, "admitted_at": call.admitted_at, "deadline": call.deadline}
        result = await backend.run(
            CLIRequest(
                prompt=request.prompt,
                target=target,
                system_prompt=LOOKUP_PROMPT,
                tool_name="apilookup",
                require_search=True,
                files=tuple(files),
                images=tuple(request.images or []),
                history_turns=tuple(arguments.get("_replay_history_turns", ())),
                history_omitted_turns=arguments.get("_replay_omitted_turns", 0),
                **options,
            )
        )
        payload = {
            "status": result.status,
            "content": result.content,
            "sources": result.metadata.get("source_urls", []),
            "metadata": result.to_metadata(),
        }
        if call:
            payload["continuation_id"] = call.continuation_id
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
