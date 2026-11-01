"""Local CLI discovery without authentication or inference probes."""

import json
from typing import Any

from mcp.types import TextContent

from clink.backend import get_backend
from tools.shared.base_models import ToolRequest
from tools.shared.base_tool import BaseTool


class ListModelsTool(BaseTool):
    def get_name(self) -> str:
        return "listmodels"

    def get_description(self) -> str:
        return "Describe configured CLIs, executable availability, roles, native model defaults and restrictions without inference."

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
        from clink.native import get_native_runtime

        payload = {
            "status": "success",
            "clients": get_backend().describe(),
            "native_task_clients": get_native_runtime().describe(),
            "authentication": "unknown; no login or inference probes performed",
            "model_selection": "request → role → client → native CLI default; no aliases or backend fallback",
        }
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
