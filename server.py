"""
clink-mcp-server — advisory CLI orchestration

This module implements the core MCP (Model Context Protocol) server that provides
AI-powered tools for code analysis, review, and assistance using multiple AI models.

The server follows the MCP specification to expose various AI tools as callable functions
that can be used by MCP clients (like Claude). Each tool provides specialized functionality
such as code review, debugging, deep thinking, and general chat capabilities.

Key Components:
- MCP Server: Handles protocol communication and tool discovery
- Tool Registry: Maps tool names to their implementations
- Request Handler: Processes incoming tool calls and returns formatted responses
- Configuration: Manages API keys and model settings

The server runs on stdio (standard input/output) and communicates using JSON-RPC messages
as defined by the MCP protocol.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from mcp.server import Server  # noqa: E402
from mcp.server.models import InitializationOptions  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import (  # noqa: E402
    GetPromptResult,
    Prompt,
    PromptMessage,
    PromptsCapability,
    ServerCapabilities,
    TextContent,
    Tool,
    ToolAnnotations,
    ToolsCapability,
)
from pydantic import ValidationError

from config import __version__
from tools import (  # noqa: E402
    AnalyzeTool,
    ChallengeTool,
    ChatTool,
    CLinkTool,
    CodeReviewTool,
    ConsensusTool,
    DebugIssueTool,
    DocgenTool,
    ListModelsTool,
    LookupTool,
    PlannerTool,
    PrecommitTool,
    RefactorTool,
    SecauditTool,
    TestGenTool,
    ThinkDeepTool,
    TracerTool,
    VersionTool,
)
from tools.shared.exceptions import ToolExecutionError  # noqa: E402
from utils.env import env_override_enabled, get_env  # noqa: E402

# Configure logging for server operations
# Can be controlled via LOG_LEVEL environment variable (DEBUG, INFO, WARNING, ERROR)
log_level = (get_env("LOG_LEVEL", "INFO") or "INFO").upper()

# Create timezone-aware formatter


class LocalTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        """Override to use local timezone instead of UTC"""
        ct = self.converter(record.created)
        if datefmt:
            s = time.strftime(datefmt, ct)
        else:
            t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
            s = f"{t},{record.msecs:03.0f}"
        return s


# Configure both console and file logging
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Clear any existing handlers first
root_logger = logging.getLogger()
root_logger.handlers.clear()

# Create and configure stderr handler explicitly
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(getattr(logging, log_level, logging.INFO))
stderr_handler.setFormatter(LocalTimeFormatter(log_format))
root_logger.addHandler(stderr_handler)

# Note: MCP stdio_server interferes with stderr during tool execution
# All logs are properly written to logs/mcp_server.log for monitoring

# Set root logger level
root_logger.setLevel(getattr(logging, log_level, logging.INFO))

# Add rotating file handler for local log monitoring

try:
    # Create logs directory in project root
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # Main server log with size-based rotation (20MB max per file)
    # This ensures logs don't grow indefinitely and are properly managed
    file_handler = RotatingFileHandler(
        log_dir / "mcp_server.log",
        maxBytes=20 * 1024 * 1024,  # 20MB max file size
        backupCount=5,  # Keep 10 rotated files (100MB total)
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, log_level, logging.INFO))
    file_handler.setFormatter(LocalTimeFormatter(log_format))
    logging.getLogger().addHandler(file_handler)

    # Create a special logger for MCP activity tracking with size-based rotation
    mcp_logger = logging.getLogger("mcp_activity")
    mcp_file_handler = RotatingFileHandler(
        log_dir / "mcp_activity.log",
        maxBytes=10 * 1024 * 1024,  # 20MB max file size
        backupCount=2,  # Keep 5 rotated files (20MB total)
        encoding="utf-8",
    )
    mcp_file_handler.setLevel(logging.INFO)
    mcp_file_handler.setFormatter(LocalTimeFormatter("%(asctime)s - %(message)s"))
    mcp_logger.addHandler(mcp_file_handler)
    mcp_logger.setLevel(logging.INFO)
    # Ensure MCP activity also goes to stderr
    mcp_logger.propagate = True

    # Log setup info directly to root logger since logger isn't defined yet
    logging.info(f"Logging to: {log_dir / 'mcp_server.log'}")
    logging.info(f"Process PID: {os.getpid()}")

except Exception as e:
    print(f"Warning: Could not set up file logging: {e}", file=sys.stderr)

logger = logging.getLogger(__name__)

# Log PAL_MCP_FORCE_ENV_OVERRIDE configuration for transparency
if env_override_enabled():
    logger.info("PAL_MCP_FORCE_ENV_OVERRIDE enabled - .env file values will override system environment variables")
    logger.debug("Environment override prevents conflicts between different AI tools passing cached API keys")
else:
    logger.debug("PAL_MCP_FORCE_ENV_OVERRIDE disabled - system environment variables take precedence")


# Create the MCP server instance with a unique name identifier
# This name is used by MCP clients to identify and connect to this specific server
server: Server = Server("clink-mcp-server")


# Constants for tool filtering
ESSENTIAL_TOOLS = {"version", "listmodels"}


def parse_disabled_tools_env() -> set[str]:
    """
    Parse the DISABLED_TOOLS environment variable into a set of tool names.

    Returns:
        Set of lowercase tool names to disable, empty set if none specified
    """
    disabled_tools_env = (get_env("DISABLED_TOOLS", "") or "").strip()
    if not disabled_tools_env:
        return set()
    return {t.strip().lower() for t in disabled_tools_env.split(",") if t.strip()}


def validate_disabled_tools(disabled_tools: set[str], all_tools: dict[str, Any]) -> None:
    """
    Validate the disabled tools list and log appropriate warnings.

    Args:
        disabled_tools: Set of tool names requested to be disabled
        all_tools: Dictionary of all available tool instances
    """
    essential_disabled = disabled_tools & ESSENTIAL_TOOLS
    if essential_disabled:
        logger.warning(f"Cannot disable essential tools: {sorted(essential_disabled)}")
    unknown_tools = disabled_tools - set(all_tools.keys())
    if unknown_tools:
        logger.warning(f"Unknown tools in DISABLED_TOOLS: {sorted(unknown_tools)}")


def apply_tool_filter(all_tools: dict[str, Any], disabled_tools: set[str]) -> dict[str, Any]:
    """
    Apply the disabled tools filter to create the final tools dictionary.

    Args:
        all_tools: Dictionary of all available tool instances
        disabled_tools: Set of tool names to disable

    Returns:
        Dictionary containing only enabled tools
    """
    enabled_tools = {}
    for tool_name, tool_instance in all_tools.items():
        if tool_name in ESSENTIAL_TOOLS or tool_name not in disabled_tools:
            enabled_tools[tool_name] = tool_instance
        else:
            logger.debug(f"Tool '{tool_name}' disabled via DISABLED_TOOLS")
    return enabled_tools


def log_tool_configuration(disabled_tools: set[str], enabled_tools: dict[str, Any]) -> None:
    """
    Log the final tool configuration for visibility.

    Args:
        disabled_tools: Set of tool names that were requested to be disabled
        enabled_tools: Dictionary of tools that remain enabled
    """
    if not disabled_tools:
        logger.info("All tools enabled (DISABLED_TOOLS not set)")
        return
    actual_disabled = disabled_tools - ESSENTIAL_TOOLS
    if actual_disabled:
        logger.debug(f"Disabled tools: {sorted(actual_disabled)}")
        logger.info(f"Active tools: {sorted(enabled_tools.keys())}")


def filter_disabled_tools(all_tools: dict[str, Any]) -> dict[str, Any]:
    """
    Filter tools based on DISABLED_TOOLS environment variable.

    Args:
        all_tools: Dictionary of all available tool instances

    Returns:
        dict: Filtered dictionary containing only enabled tools
    """
    disabled_tools = parse_disabled_tools_env()
    if not disabled_tools:
        log_tool_configuration(disabled_tools, all_tools)
        return all_tools
    validate_disabled_tools(disabled_tools, all_tools)
    enabled_tools = apply_tool_filter(all_tools, disabled_tools)
    log_tool_configuration(disabled_tools, enabled_tools)
    return enabled_tools


# Initialize the tool registry with all available AI-powered tools
# Each tool provides specialized functionality for different development tasks
# These instances describe schemas; execution constructs a fresh instance per call.
TOOLS = {
    "chat": ChatTool(),  # Interactive development chat and brainstorming
    "clink": CLinkTool(),  # Bridge requests to configured AI CLIs
    "thinkdeep": ThinkDeepTool(),  # Step-by-step deep thinking workflow with expert analysis
    "planner": PlannerTool(),  # Interactive sequential planner using workflow architecture
    "consensus": ConsensusTool(),  # Step-by-step consensus workflow with multi-model analysis
    "codereview": CodeReviewTool(),  # Comprehensive step-by-step code review workflow with expert analysis
    "precommit": PrecommitTool(),  # Step-by-step pre-commit validation workflow
    "debug": DebugIssueTool(),  # Root cause analysis and debugging assistance
    "secaudit": SecauditTool(),  # Comprehensive security audit with OWASP Top 10 and compliance coverage
    "docgen": DocgenTool(),  # Step-by-step documentation generation with complexity analysis
    "analyze": AnalyzeTool(),  # General-purpose file and code analysis
    "refactor": RefactorTool(),  # Step-by-step refactoring analysis workflow with expert validation
    "tracer": TracerTool(),  # Static call path prediction and control flow analysis
    "testgen": TestGenTool(),  # Step-by-step test generation workflow with expert validation
    "challenge": ChallengeTool(),  # Critical challenge prompt wrapper to avoid automatic agreement
    "apilookup": LookupTool(),  # Quick web/API lookup instructions
    "listmodels": ListModelsTool(),  # List all available AI models by provider
    "version": VersionTool(),  # Display server version and system information
}
TOOLS = filter_disabled_tools(TOOLS)

# Rich prompt templates for all tools
PROMPT_TEMPLATES = {
    "chat": {
        "name": "chat",
        "description": "Chat and brainstorm ideas",
        "template": "Chat with {model} about this",
    },
    "clink": {
        "name": "clink",
        "description": "Forward a request to a configured AI CLI (e.g., Codex)",
        "template": "Use clink with cli_name=<cli> to run this prompt",
    },
    "thinkdeep": {
        "name": "thinkdeeper",
        "description": "Step-by-step deep thinking workflow with expert analysis",
        "template": "Start comprehensive deep thinking workflow with {model} using {thinking_mode} thinking mode",
    },
    "planner": {
        "name": "planner",
        "description": "Break down complex ideas, problems, or projects into multiple manageable steps",
        "template": "Create a detailed plan with {model}",
    },
    "consensus": {
        "name": "consensus",
        "description": "Step-by-step consensus workflow with multi-model analysis",
        "template": "Start comprehensive consensus workflow with {model}",
    },
    "codereview": {
        "name": "review",
        "description": "Perform a comprehensive code review",
        "template": "Perform a comprehensive code review with {model}",
    },
    "precommit": {
        "name": "precommit",
        "description": "Step-by-step pre-commit validation workflow",
        "template": "Start comprehensive pre-commit validation workflow with {model}",
    },
    "debug": {
        "name": "debug",
        "description": "Debug an issue or error",
        "template": "Help debug this issue with {model}",
    },
    "secaudit": {
        "name": "secaudit",
        "description": "Comprehensive security audit with OWASP Top 10 coverage",
        "template": "Perform comprehensive security audit with {model}",
    },
    "docgen": {
        "name": "docgen",
        "description": "Generate comprehensive code documentation with complexity analysis",
        "template": "Generate comprehensive documentation with {model}",
    },
    "analyze": {
        "name": "analyze",
        "description": "Analyze files and code structure",
        "template": "Analyze these files with {model}",
    },
    "refactor": {
        "name": "refactor",
        "description": "Refactor and improve code structure",
        "template": "Refactor this code with {model}",
    },
    "tracer": {
        "name": "tracer",
        "description": "Trace code execution paths",
        "template": "Generate tracer analysis with {model}",
    },
    "testgen": {
        "name": "testgen",
        "description": "Generate comprehensive tests",
        "template": "Generate comprehensive tests with {model}",
    },
    "challenge": {
        "name": "challenge",
        "description": "Challenge a statement critically without automatic agreement",
        "template": "Challenge this statement critically",
    },
    "apilookup": {
        "name": "apilookup",
        "description": "Look up the latest API or SDK information",
        "template": "Lookup latest API docs for {model}",
    },
    "listmodels": {
        "name": "listmodels",
        "description": "List available AI models",
        "template": "List all available models",
    },
    "version": {
        "name": "version",
        "description": "Show server version and system information",
        "template": "Show PAL MCP Server version",
    },
}


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """
    List all available tools with their descriptions and input schemas.

    This handler is called by MCP clients during initialization to discover
    what tools are available. Each tool provides:
    - name: Unique identifier for the tool
    - description: Detailed explanation of what the tool does
    - inputSchema: JSON Schema defining the expected parameters

    Returns:
        List of Tool objects representing all available tools
    """
    logger.debug("MCP client requested tool list")

    # Try to log client info if available (this happens early in the handshake)
    try:
        from utils.client_info import format_client_info, get_client_info_from_context

        client_info = get_client_info_from_context(server)
        if client_info:
            formatted = format_client_info(client_info)
            logger.info(f"MCP Client Connected: {formatted}")

            # Log to activity file as well
            try:
                mcp_activity_logger = logging.getLogger("mcp_activity")
                friendly_name = client_info.get("friendly_name", "CLI Agent")
                raw_name = client_info.get("name", "Unknown")
                version = client_info.get("version", "Unknown")
                mcp_activity_logger.info(f"MCP_CLIENT_INFO: {friendly_name} (raw={raw_name} v{version})")
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Could not log client info during list_tools: {e}")
    tools = []

    # Add all registered AI-powered tools from the TOOLS registry
    for tool in TOOLS.values():
        # Get optional annotations from the tool
        annotations = tool.get_annotations()
        tool_annotations = ToolAnnotations(**annotations) if annotations else None

        tools.append(
            Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.get_input_schema(),
                annotations=tool_annotations,
            )
        )

    logger.debug(f"Returning {len(tools)} tools to MCP client")
    return tools


def _json_response(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


def _failure(code: str, message: str, request_id: str, remedy: str = "") -> list[TextContent]:
    return _json_response(
        {"status": "error", "error": {"code": code, "message": message, "remedy": remedy}, "request_id": request_id}
    )


def _is_failure(result: list[TextContent]) -> bool:
    for item in result:
        try:
            payload = json.loads(item.text)
        except (ValueError, AttributeError):
            continue
        if isinstance(payload, dict) and (payload.get("status") == "error" or payload.get("error")):
            return True
    return False


def _records_success(result: list[TextContent]) -> bool:
    if _is_failure(result):
        return False
    for item in result:
        try:
            payload = json.loads(item.text)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("attempt_success") is False:
            return False
    return True


def _with_replay_metadata(result: list[TextContent], call) -> list[TextContent]:
    enriched = []
    for item in result:
        try:
            payload = json.loads(item.text)
        except ValueError:
            enriched.append(item)
            continue
        if isinstance(payload, dict):
            payload.setdefault("metadata", {})["replay"] = {
                **call.replay_metadata,
                "files": "current filesystem state; prior file contents are not snapshots",
            }
            enriched.append(TextContent(type="text", text=json.dumps(payload, ensure_ascii=False)))
        else:
            enriched.append(item)
    return enriched


def _backend_identity(result: list[TextContent]) -> dict[str, Any]:
    for item in result:
        try:
            metadata = json.loads(item.text).get("metadata", {})
        except (ValueError, AttributeError):
            continue
        if isinstance(metadata, dict) and metadata.get("backend"):
            return {key: metadata[key] for key in ("backend", "requested_model", "actual_model") if key in metadata}
    return {}


def _has_partial_context(result: list[TextContent]) -> bool:
    for item in result:
        try:
            payload = json.loads(item.text)
        except ValueError:
            continue
        if isinstance(payload, dict) and any(
            outcome.get("status") == "success" for outcome in payload.get("accumulated_responses", [])
        ):
            return True
    return False


def _bound_response(result: list[TextContent], request_id: str) -> list[TextContent]:
    from config import MCP_RESPONSE_SIZE_LIMIT

    size = len(json.dumps([item.model_dump() for item in result], ensure_ascii=False).encode("utf-8")) + 128
    if size <= MCP_RESPONSE_SIZE_LIMIT:
        return result
    return _failure("output_limit", "The complete tool response exceeds the configured MCP response limit.", request_id)


def _replay_arguments(arguments: dict[str, Any], call) -> dict[str, Any]:
    from utils.conversation_memory import get_conversation_file_list, get_conversation_image_list

    prepared = dict(arguments)
    prepared["_replay_history"] = call.history()
    prepared["_replay_history_turns"] = call.history_turns()
    prepared["_replay_omitted_turns"] = call.replay_metadata["omitted_turns"]
    # Paths are revalidated by the selected adapter before any read or launch.
    files = get_conversation_file_list(call.context)
    if files:
        key = "relevant_files" if "step_number" in arguments else "absolute_file_paths"
        prepared[key] = list(dict.fromkeys([*arguments.get(key, []), *files]))
    images = get_conversation_image_list(call.context)
    if images:
        prepared["images"] = list(dict.fromkeys([*arguments.get("images", []), *images]))
    return prepared


async def _execute_call(name: str, arguments: dict[str, Any], request_id: str, admitted_at: float) -> list[TextContent]:
    from clink.contracts import BackendError
    from utils.call_state import StateError, conversation_call

    if name not in TOOLS:
        return _failure("unknown_tool", "Unknown or disabled tool.", request_id)
    if not isinstance(arguments, dict) or any(str(key).startswith("_") for key in arguments):
        return _failure("invalid_request", "Tool arguments must be an object without private fields.", request_id)
    from config import MCP_PROMPT_SIZE_LIMIT

    if len(json.dumps(arguments, ensure_ascii=False).encode("utf-8")) > MCP_PROMPT_SIZE_LIMIT:
        return _failure("input_limit", "Required tool arguments exceed the 1 MiB UTF-8 input budget.", request_id)
    # Discovery descriptors are never used for execution. Every call owns its tool state.
    tool = type(TOOLS[name])()
    try:
        request = tool.get_request_model()(**arguments)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        fields = sorted({".".join(map(str, err["loc"]))[:80] for err in errors[:8]})
        suffix = f" ({exc.error_count()} validation errors; first eight shown)" if len(errors) > 8 else ""
        return _failure("invalid_request", "Invalid fields: " + ", ".join(fields) + suffix, request_id)
    original = request.model_dump(exclude_unset=True)
    schema = tool.get_input_schema()
    if schema.get("additionalProperties") is False and set(arguments) - set(schema.get("properties", {})):
        return _failure("invalid_request", "Unknown fields are not accepted by this tool.", request_id)
    deadline = admitted_at + min(1800.0, float(original.get("timeout_seconds", 1800.0)))
    internal = {
        "_original_arguments": original,
        "_request_id": request_id,
        "_admitted_at": admitted_at,
        "_deadline": deadline,
    }
    if name in {"challenge", "version", "listmodels"} and not original.get("continuation_id"):
        return _bound_response(await tool.execute({**original, **internal}), request_id)
    async with conversation_call(
        name, original, deadline=deadline, admitted_at=admitted_at, request_id=request_id
    ) as call:
        try:
            if call.cached_result is not None:
                if name == "consensus":
                    await tool.validate_cached_result(call.workflow_state)
                return [TextContent.model_validate(item) for item in call.cached_result]
            prepared = original if name == "consensus" else _replay_arguments(original, call)
            prepared = {**prepared, **internal, "continuation_id": call.continuation_id}
            result = _bound_response(_with_replay_metadata(await tool.execute(prepared), call), request_id)
        except (BackendError, StateError) as exc:
            result = _failure(exc.code, str(exc), request_id, exc.remedy if isinstance(exc, BackendError) else "")
        except ToolExecutionError as exc:
            # ToolExecutionError already carries a public, structured failure.
            try:
                payload = json.loads(exc.payload)
            except ValueError:
                payload = {"status": "error", "error": {"code": "tool_error", "message": "Tool execution failed."}}
            result = _bound_response(_json_response(payload), request_id)
        except ValidationError:
            result = _failure("invalid_request", "Tool context failed request validation.", request_id)
        result = _bound_response(result, request_id)
        success = _records_success(result)
        call.commit(
            result,
            success=success,
            user_content=tool.__dict__.get("_effective_user_prompt")
            or original.get("prompt")
            or original.get("step")
            or "",
            assistant_content="\n".join(item.text for item in result),
            files=list(dict.fromkeys([*original.get("absolute_file_paths", []), *original.get("relevant_files", [])])),
            images=original.get("images") or [],
            backend_identity=_backend_identity(result),
            retain_failure_context=name == "consensus" and not success and _has_partial_context(result),
        )
        return result


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Execute with isolated per-call state and one serialized continuation transaction."""
    from clink.contracts import BackendError
    from utils.call_state import StateError

    request_id = str(uuid.uuid4())
    admitted_at = time.monotonic()
    log_name = name if name in TOOLS else "unknown"
    logger.info("tool_start request_id=%s tool=%s", request_id, log_name)
    try:
        result = await _execute_call(name, arguments, request_id, admitted_at)
    except (BackendError, StateError) as exc:
        result = _failure(exc.code, str(exc), request_id, exc.remedy if isinstance(exc, BackendError) else "")
    except asyncio.CancelledError:
        logger.info("tool_cancelled request_id=%s tool=%s", request_id, log_name)
        raise
    except Exception as exc:
        # Diagnostic type only: exception messages may contain prompts or credentials.
        logger.error("tool_failure request_id=%s tool=%s type=%s", request_id, log_name, type(exc).__name__)
        result = _failure("internal_error", "The tool could not complete the request.", request_id)
    # Cached results and every early/caught failure pass the same public byte ceiling.
    result = _bound_response(result, request_id)
    logger.info(
        "tool_end request_id=%s tool=%s duration=%.3f success=%s",
        request_id,
        log_name,
        time.monotonic() - admitted_at,
        not _is_failure(result),
    )
    if _is_failure(result):
        raise ToolExecutionError(result[0].text)
    return result


@server.list_prompts()
async def handle_list_prompts() -> list[Prompt]:
    """
    List all available prompts for CLI Code shortcuts.

    This handler returns prompts that enable shortcuts like /pal:thinkdeeper.
    We automatically generate prompts from all tools (1:1 mapping) plus add
    a few marketing aliases with richer templates for commonly used tools.

    Returns:
        List of Prompt objects representing all available prompts
    """
    logger.debug("MCP client requested prompt list")
    prompts = []

    # Add a prompt for each tool with rich templates
    for tool_name, tool in TOOLS.items():
        if tool_name in PROMPT_TEMPLATES:
            # Use the rich template
            template_info = PROMPT_TEMPLATES[tool_name]
            prompts.append(
                Prompt(
                    name=template_info["name"],
                    description=template_info["description"],
                    arguments=[],  # MVP: no structured args
                )
            )
        else:
            # Fallback for any tools without templates (shouldn't happen)
            prompts.append(
                Prompt(
                    name=tool_name,
                    description=f"Use {tool.name} tool",
                    arguments=[],
                )
            )

    # Add special "continue" prompt
    prompts.append(
        Prompt(
            name="continue",
            description="Continue the previous conversation using the chat tool",
            arguments=[],
        )
    )

    logger.debug(f"Returning {len(prompts)} prompts to MCP client")
    return prompts


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict[str, Any] = None) -> GetPromptResult:
    """
    Get prompt details and generate the actual prompt text.

    This handler is called when a user invokes a prompt (e.g., /pal:thinkdeeper or /pal:chat:gpt5).
    It generates the appropriate text that CLI will then use to call the
    underlying tool.

    Supports structured prompt names like "chat:gpt5" where:
    - "chat" is the tool name
    - "gpt5" is the model to use

    Args:
        name: The name of the prompt to execute (can include model like "chat:gpt5")
        arguments: Optional arguments for the prompt (e.g., model, thinking_mode)

    Returns:
        GetPromptResult with the prompt details and generated message

    Raises:
        ValueError: If the prompt name is unknown
    """
    logger.debug("MCP prompt requested name=%s", name)

    # Handle special "continue" case
    if name.lower() == "continue":
        # This is "/pal:continue" - use chat tool as default for continuation
        tool_name = "chat"
        template_info = {
            "name": "continue",
            "description": "Continue the previous conversation",
            "template": "Continue the conversation",
        }
        logger.debug("Using /pal:continue - defaulting to chat tool")
    else:
        # Find the corresponding tool by checking prompt names
        tool_name = None
        template_info = None

        # Check if it's a known prompt name
        for t_name, t_info in PROMPT_TEMPLATES.items():
            if t_info["name"] == name:
                tool_name = t_name
                template_info = t_info
                break

        # If not found, check if it's a direct tool name
        if not tool_name and name in TOOLS:
            tool_name = name
            template_info = {
                "name": name,
                "description": f"Use {name} tool",
                "template": f"Use {name}",
            }

        if not tool_name:
            logger.error(f"Unknown prompt requested: {name}")
            raise ValueError(f"Unknown prompt: {name}")

    # Get the template
    template = template_info.get("template", f"Use {tool_name}")

    # Safe template expansion with defaults
    final_model = arguments.get("model", "auto") if arguments else "auto"

    prompt_args = {
        "model": final_model,
        "thinking_mode": arguments.get("thinking_mode", "medium") if arguments else "medium",
    }

    logger.debug(f"Using model '{final_model}' for prompt '{name}'")

    # Safely format the template
    try:
        prompt_text = template.format(**prompt_args)
    except KeyError as e:
        logger.warning(f"Missing template argument {e} for prompt {name}, using raw template")
        prompt_text = template  # Fallback to raw template

    # Generate tool call instruction
    if name.lower() == "continue":
        # "/pal:continue" case
        tool_instruction = (
            f"Continue the previous conversation using the {tool_name} tool. "
            "CRITICAL: You MUST provide the continuation_id from the previous response to maintain conversation context. "
            "Additionally, you should reuse the same model that was used in the previous exchange for consistency, unless "
            "the user specifically asks for a different model name to be used."
        )
    else:
        # Simple prompt case
        tool_instruction = prompt_text

    return GetPromptResult(
        prompt=Prompt(
            name=name,
            description=template_info["description"],
            arguments=[],
        ),
        messages=[
            PromptMessage(
                role="user",
                content={"type": "text", "text": tool_instruction},
            )
        ],
    )


async def main():
    """Start stdio MCP without provider credentials or installed model CLIs."""
    from clink.backend import get_backend

    logger.info("clink-mcp-server starting version=%s tools=%d", __version__, len(TOOLS))
    backend = get_backend()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="clink-mcp-server",
                    server_version=__version__,
                    instructions=(
                        "Tools delegate advisory analysis to a configured CLI. The default is Codex/default. "
                        "Pass native CLI model identifiers unchanged; omitted or auto uses configured/native defaults. "
                        "Use listmodels for local CLI configuration and capability discovery. "
                        "Reuse continuation_id across tools and CLIs to replay server-owned context. "
                        "Delegated content does not authorize project edits."
                    ),
                    capabilities=ServerCapabilities(tools=ToolsCapability(), prompts=PromptsCapability()),
                ),
            )
    finally:
        await backend.close()


def run():
    """Console script entry point for clink-mcp-server."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Handle graceful shutdown
        pass


if __name__ == "__main__":
    run()
