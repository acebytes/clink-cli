"""Local fork metadata; this utility makes no network requests."""

import json
import platform
from typing import Any

from mcp.types import TextContent

from config import FORK_REPOSITORY, SOURCE_COMMIT, SOURCE_REPOSITORY, __author__, __updated__, __version__
from tools.shared.base_models import ToolRequest
from tools.shared.base_tool import BaseTool


class VersionTool(BaseTool):
    def get_name(self) -> str:
        return "version"

    def get_description(self) -> str:
        return "Report clink-mcp-server version, platform and PAL source provenance locally."

    def get_input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    def get_system_prompt(self) -> str:
        return ""

    def requires_model(self) -> bool:
        return False

    def get_request_model(self):
        return ToolRequest

    def get_annotations(self) -> dict[str, Any]:
        return {"readOnlyHint": True, "openWorldHint": False}

    async def prepare_prompt(self, request) -> str:
        return ""

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        payload = {
            "status": "success",
            "name": "clink-mcp-server",
            "version": __version__,
            "updated": __updated__,
            "author": __author__,
            "repository": FORK_REPOSITORY,
            "source": {"repository": SOURCE_REPOSITORY, "commit": SOURCE_COMMIT, "version": "9.8.2"},
            "license": "Apache-2.0; copyright Beehive Innovations retained",
            "platform": platform.system(),
            "python": platform.python_version(),
            "continuations": "process-local UUID replay; no native session resume",
        }
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
