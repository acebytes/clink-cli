"""
Core Tool Infrastructure for PAL MCP Tools

This module provides the fundamental base class for all tools:
- BaseTool: Abstract base class defining the tool interface

The BaseTool class defines the core contract that tools must implement and provides
common functionality for request validation, error handling, model management,
conversation handling, file processing, and response formatting.
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from mcp.types import TextContent

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from config import MCP_PROMPT_SIZE_LIMIT
from utils import estimate_tokens
from utils.conversation_memory import (
    ConversationTurn,
    get_conversation_file_list,
    get_thread,
)
from utils.env import get_env

# Import models from tools.models for compatibility
try:
    from tools.models import ToolOutput
except ImportError:
    # Fallback in case models haven't been set up yet
    ToolOutput = None

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """
    Abstract base class for all PAL MCP tools.

    This class defines the interface that all tools must implement and provides
    common functionality for request handling, CLI routing, and response formatting.

    The server owns conversation transactions and bounded replay. Tools retain their
    schemas, caller-driven protocols and prompt preparation. All delegated work uses
    one immutable backend target and the shared bounded CLI runner. Context paths are
    validated before file reads and revalidated before launch.

    To create a new tool:
    1. Create a new class that inherits from BaseTool
    2. Implement all abstract methods
    3. Define a request model that inherits from ToolRequest
    4. Register the tool in server.py's TOOLS dictionary
    """

    def __init__(self):
        # Cache tool metadata at initialization to avoid repeated calls
        self.name = self.get_name()
        self.description = self.get_description()
        self.default_temperature = self.get_default_temperature()
        self._effective_user_prompt: Optional[str] = None
        self._current_arguments: dict[str, Any] = {}
        # Tool initialization complete

    @abstractmethod
    def get_name(self) -> str:
        """
        Return the unique name identifier for this tool.

        This name is used by MCP clients to invoke the tool and must be
        unique across all registered tools.

        Returns:
            str: The tool's unique name (e.g., "review_code", "analyze")
        """
        pass

    @abstractmethod
    def get_description(self) -> str:
        """
        Return a detailed description of what this tool does.

        This description is shown to MCP clients (like Claude / Codex / Gemini) to help them
        understand when and how to use the tool. It should be comprehensive
        and include trigger phrases.

        Returns:
            str: Detailed tool description with usage examples
        """
        pass

    @abstractmethod
    def get_input_schema(self) -> dict[str, Any]:
        """
        Return the JSON Schema that defines this tool's parameters.

        This schema is used by MCP clients to validate inputs before
        sending requests. It should match the tool's request model.

        Returns:
            Dict[str, Any]: JSON Schema object defining required and optional parameters
        """
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """
        Return the system prompt that configures the AI model's behavior.

        This prompt sets the context and instructions for how the model
        should approach the task. It's prepended to the user's request.

        Returns:
            str: System prompt with role definition and instructions
        """
        pass

    def get_annotations(self) -> Optional[dict[str, Any]]:
        """
        Return optional annotations for this tool.

        Annotations provide hints about tool behavior without being security-critical.
        They help MCP clients make better decisions about tool usage.

        Returns:
            Optional[dict]: Dictionary with annotation fields like readOnlyHint, destructiveHint, etc.
                           Returns None if no annotations are needed.
        """
        return None

    def requires_model(self) -> bool:
        """
        Return whether this tool requires AI model access.

        Tools that override execute() to do pure data processing (like planner)
        should return False to skip model resolution at the MCP boundary.

        Returns:
            bool: True if tool needs AI model access (default), False for data-only tools
        """
        return True

    def is_effective_auto_mode(self) -> bool:
        """Native CLI model defaults never require caller model selection."""
        return False

    def get_model_field_schema(self) -> dict[str, Any]:
        """Describe native model selection without provider catalogs or aliases."""
        from .base_models import COMMON_FIELD_DESCRIPTIONS

        return {"type": "string", "description": COMMON_FIELD_DESCRIPTIONS["model"]}

    def get_default_temperature(self) -> float:
        """
        Return the default temperature setting for this tool.

        Override this method to set tool-specific temperature defaults.
        Lower values (0.0-0.3) for analytical tasks, higher (0.7-1.0) for creative tasks.

        Returns:
            float: Default temperature between 0.0 and 1.0
        """
        return 0.5

    def wants_line_numbers_by_default(self) -> bool:
        """
        Return whether this tool wants line numbers added to code files by default.

        By default, ALL tools get line numbers for precise code references.
        Line numbers are essential for accurate communication about code locations.

        Returns:
            bool: True if line numbers should be added by default for this tool
        """
        return True  # All tools get line numbers by default for consistency

    def get_default_thinking_mode(self) -> str:
        """
        Return the default thinking mode for this tool.

        Thinking mode controls computational budget for reasoning.
        Override for tools that need more or less reasoning depth.

        Returns:
            str: One of "minimal", "low", "medium", "high", "max"
        """
        return "medium"  # Default to medium thinking for better reasoning

    def get_model_category(self) -> "ToolModelCategory":
        """
        Return the model category for this tool.

        Model category influences which model is selected in auto mode.
        Override to specify whether your tool needs extended reasoning,
        fast response, or balanced capabilities.

        Returns:
            ToolModelCategory: Category that influences model selection
        """
        from tools.models import ToolModelCategory

        return ToolModelCategory.BALANCED

    @abstractmethod
    def get_request_model(self):
        """
        Return the Pydantic model class used for validating requests.

        This model should inherit from ToolRequest and define all
        parameters specific to this tool.

        Returns:
            Type[ToolRequest]: The request model class
        """
        pass

    def validate_file_paths(self, request) -> Optional[str]:
        """
        Validate that all file paths in the request are absolute.

        This is a critical security function that prevents path traversal attacks
        and ensures all file access is properly controlled. All file paths must
        be absolute to avoid ambiguity and security issues.

        Args:
            request: The validated request object

        Returns:
            Optional[str]: Error message if validation fails, None if all paths are valid
        """
        # Only validate files/paths if they exist in the request
        file_fields = [
            "absolute_file_paths",
            "file",
            "path",
            "directory",
            "notebooks",
            "test_examples",
            "style_guide_examples",
            "files_checked",
            "relevant_files",
        ]

        for field_name in file_fields:
            if hasattr(request, field_name):
                field_value = getattr(request, field_name)
                if field_value is None:
                    continue

                # Handle both single paths and lists of paths
                paths_to_check = field_value if isinstance(field_value, list) else [field_value]

                for path in paths_to_check:
                    if path and not os.path.isabs(path):
                        return f"All file paths must be FULL absolute paths. Invalid path: '{path}'"

        return None

    def _validate_token_limit(self, content: str, content_type: str = "Content") -> None:
        """
        Validate that user-provided content doesn't exceed the MCP prompt size limit.

        This enforcement is strictly for text crossing the MCP transport boundary
        (i.e., user input). Internal prompt construction may exceed this size and is
        governed by model-specific token limits.

        Args:
            content: The user-originated content to validate
            content_type: Description of the content type for error messages

        Raises:
            ValueError: If content exceeds the character size limit
        """
        if not content:
            logger.debug(f"{self.name} tool {content_type.lower()} validation skipped (no content)")
            return

        char_count = len(content.encode("utf-8"))
        if char_count > MCP_PROMPT_SIZE_LIMIT:
            token_estimate = estimate_tokens(content)
            error_msg = (
                f"{char_count:,} UTF-8 bytes (~{token_estimate:,} tokens). "
                f"Maximum is {MCP_PROMPT_SIZE_LIMIT:,} UTF-8 bytes."
            )
            logger.error(f"{self.name} tool {content_type.lower()} validation failed: {error_msg}")
            from clink.contracts import BackendError

            raise BackendError("input_limit", f"{content_type} too large: {error_msg}", "Split or narrow the request.")

        token_estimate = estimate_tokens(content)
        logger.debug(
            f"{self.name} tool {content_type.lower()} validation passed: "
            f"{char_count:,} UTF-8 bytes (~{token_estimate:,} tokens)"
        )

    # === CONVERSATION AND FILE HANDLING METHODS ===

    def get_conversation_embedded_files(self, continuation_id: Optional[str]) -> list[str]:
        """
        Get list of files already embedded in conversation history.

        This method returns the list of files that have already been embedded
        in the conversation history for a given continuation thread. Tools can
        use this to avoid re-embedding files that are already available in the
        conversation context.

        Args:
            continuation_id: Thread continuation ID, or None for new conversations

        Returns:
            list[str]: List of file paths already embedded in conversation history
        """
        if not continuation_id:
            # New conversation, no files embedded yet
            return []

        thread_context = get_thread(continuation_id)
        if not thread_context:
            # Thread not found, no files embedded
            return []

        embedded_files = get_conversation_file_list(thread_context)
        logger.debug(f"[FILES] {self.name}: Found {len(embedded_files)} embedded files")
        return embedded_files

    def filter_new_files(self, requested_files: list[str], continuation_id: Optional[str]) -> list[str]:
        """
        Filter out files that are already embedded in conversation history.

        This method prevents duplicate file embeddings by filtering out files that have
        already been embedded in the conversation history. This optimizes token usage
        while ensuring tools still have logical access to all requested files through
        conversation history references.

        Args:
            requested_files: List of files requested for current tool execution
            continuation_id: Thread continuation ID, or None for new conversations

        Returns:
            list[str]: List of files that need to be embedded (not already in history)
        """
        logger.debug(f"[FILES] {self.name}: Filtering {len(requested_files)} requested files")

        if not continuation_id:
            # New conversation, all files are new
            logger.debug(f"[FILES] {self.name}: New conversation, all {len(requested_files)} files are new")
            return requested_files

        try:
            embedded_files = set(self.get_conversation_embedded_files(continuation_id))
            logger.debug(f"[FILES] {self.name}: Found {len(embedded_files)} embedded files in conversation")

            # Safety check: If no files are marked as embedded but we have a continuation_id,
            # this might indicate an issue with conversation history. Be conservative.
            if not embedded_files:
                logger.debug(f"{self.name} tool: No files found in conversation history for thread {continuation_id}")
                logger.debug(
                    f"[FILES] {self.name}: No embedded files found, returning all {len(requested_files)} requested files"
                )
                return requested_files

            # Return only files that haven't been embedded yet
            new_files = [f for f in requested_files if f not in embedded_files]
            logger.debug(
                f"[FILES] {self.name}: After filtering: {len(new_files)} new files, {len(requested_files) - len(new_files)} already embedded"
            )
            logger.debug("new_context_files tool=%s count=%d", self.name, len(new_files))

            # Log filtering results for debugging
            if len(new_files) < len(requested_files):
                skipped = [f for f in requested_files if f in embedded_files]
                logger.debug("conversation_files_filtered tool=%s count=%d", self.name, len(skipped))
                logger.debug("existing_context_files tool=%s count=%d", self.name, len(skipped))

            return new_files

        except Exception as e:
            # If there's any issue with conversation history lookup, be conservative
            # and include all files rather than risk losing access to needed files
            logger.warning("conversation_file_lookup_failed tool=%s error_type=%s", self.name, type(e).__name__)
            logger.warning(f"{self.name} tool: Including all requested files as fallback")
            logger.debug(
                f"[FILES] {self.name}: Returning {len(requested_files)} declared references after lookup failure"
            )
            return requested_files

    def format_conversation_turn(self, turn: ConversationTurn) -> list[str]:
        """
        Format a conversation turn for display in conversation history.

        Tools can override this to provide custom formatting for their responses
        while maintaining the standard structure for cross-tool compatibility.

        This method is called by build_conversation_history when reconstructing
        conversation context, allowing each tool to control how its responses
        appear in subsequent conversation turns.

        Args:
            turn: The conversation turn to format (from utils.conversation_memory)

        Returns:
            list[str]: Lines of formatted content for this turn

        Example:
            Default implementation returns:
            ["Files used in this turn: file1.py, file2.py", "", "Response content..."]

            Tools can override to add custom sections, formatting, or metadata display.
        """
        parts = []

        # Add files context if present
        if turn.files:
            parts.append(f"Files used in this turn: {', '.join(turn.files)}")
            parts.append("")  # Empty line for readability

        # Add the actual content
        parts.append(turn.content)

        return parts

    def handle_prompt_file(self, files: Optional[list[str]]) -> tuple[Optional[str], Optional[list[str]]]:
        """Read one admitted prompt.txt verbatim under the same required-input cap."""
        from pathlib import Path

        from clink.contracts import BackendError
        from clink.files import read_context

        if not files:
            return None, files
        prompt_content = None
        updated_files = []
        for file_path in files:
            if os.path.basename(file_path) != "prompt.txt":
                updated_files.append(file_path)
                continue
            if prompt_content is not None:
                raise BackendError("invalid_request", "Only one prompt.txt may supply required prompt text.")
            try:
                if "_backend_target" not in self.__dict__:
                    raise BackendError("path_not_allowed", "Required prompt.txt must be admitted before reading.")
                data = read_context(
                    Path(file_path), self._backend_target.client.allowed_roots, MCP_PROMPT_SIZE_LIMIT + 1
                )
                if len(data) > MCP_PROMPT_SIZE_LIMIT:
                    raise BackendError("input_limit", "Required prompt.txt exceeds the 1 MiB UTF-8 input limit.")
                prompt_content = data.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise BackendError("path_not_allowed", "Required prompt.txt could not be read as UTF-8.") from exc
        return prompt_content, updated_files

    def get_prompt_content_for_size_validation(self, user_content: str) -> str:
        """
        Get the content that should be validated for MCP prompt size limits.

        This hook method allows tools to specify what content should be checked
        against the MCP transport size limit. By default, it returns the user content,
        but can be overridden to exclude conversation history when needed.

        Args:
            user_content: The user content that would normally be validated

        Returns:
            The content that should actually be validated for size limits
        """
        # Default implementation: validate the full user content
        return user_content

    def check_prompt_size(self, text: str) -> Optional[dict[str, Any]]:
        """Reject required UTF-8 text that cannot fit the shared request ceiling."""
        size = len(text.encode("utf-8")) if text else 0
        if size > MCP_PROMPT_SIZE_LIMIT:
            return {
                "status": "error",
                "content": "Required prompt text exceeds the 1 MiB UTF-8 input limit.",
                "content_type": "text",
                "metadata": {
                    "code": "input_limit",
                    "prompt_size": size,
                    "limit": MCP_PROMPT_SIZE_LIMIT,
                    "remedy": "Split the request or explicitly select less context; prompt.txt cannot bypass this limit.",
                },
            }
        return None

    def get_websearch_instruction(self, tool_specific: Optional[str] = None) -> str:
        """
        Generate standardized web search instruction.

        Args:
            tool_specific: Optional tool-specific search guidance

        Returns:
            str: Web search instruction to append to prompt
        """

        base_instruction = """

WEB SEARCH CAPABILITY: You can request the calling agent to perform web searches to enhance your analysis with current information!

IMPORTANT: When you identify areas where web searches would significantly improve your response (such as checking current documentation, finding recent solutions, verifying best practices, or gathering community insights), you MUST explicitly instruct the agent to perform specific web searches and then respond back using the continuation_id from this response to continue the analysis.

Use clear, direct language based on the value of the search:

For valuable supplementary information: "Please perform a web search on '[specific topic/query]' and then continue this analysis using the continuation_id from this response if you find relevant information."

For important missing information: "Please search for '[specific topic/query]' and respond back with the findings using the continuation_id from this response - this information is needed to provide a complete analysis."

For critical/essential information: "SEARCH REQUIRED: Please immediately perform a web search on '[specific topic/query]' and respond back with the results using the continuation_id from this response. Cannot provide accurate analysis without this current information."

This ensures you get the most current and comprehensive information while maintaining conversation context through the continuation_id."""

        if tool_specific:
            return f"""{base_instruction}

{tool_specific}

When recommending searches, be specific about what information you need and why it would improve your analysis."""

        # Default instruction for all tools
        return f"""{base_instruction}

Consider requesting searches for:
- Current documentation and API references
- Recent best practices and patterns
- Known issues and community solutions
- Framework updates and compatibility
- Security advisories and patches
- Performance benchmarks and optimizations

When recommending searches, be specific about what information you need and why it would improve your analysis. Always remember to instruct agent to use the continuation_id from this response when providing search results."""

    def get_language_instruction(self) -> str:
        """
        Generate language instruction based on LOCALE configuration.

        Returns:
            str: Language instruction to prepend to prompt, or empty string if
                 no locale set
        """
        # Read LOCALE directly from environment to support dynamic changes
        # Tests can monkeypatch LOCALE via the environment helper (or .env when override is enforced)

        locale = (get_env("LOCALE", "") or "").strip()

        if not locale:
            return ""

        # Simple language instruction
        return f"Always respond in {locale}.\n\n"

    # === ABSTRACT METHODS FOR SIMPLE TOOLS ===

    @abstractmethod
    async def prepare_prompt(self, request) -> str:
        """
        Prepare the complete prompt for the AI model.

        This method should construct the full prompt by combining:
        - System prompt from get_system_prompt()
        - Declared file references (the shared backend loads bounded contents)
        - Conversation history from reconstruct_thread_context()
        - User's request and any tool-specific context

        Args:
            request: The validated request object

        Returns:
            str: Complete prompt ready for the AI model
        """
        pass

    def format_response(self, response: str, request, model_info: dict = None) -> str:
        """
        Format the AI model's response for the user.

        This method allows tools to post-process the model's response,
        adding structure, validation, or additional context.

        The default implementation returns the response unchanged.
        Tools can override this method to add custom formatting.

        Args:
            response: Raw response from the AI model
            request: The original request object
            model_info: Optional model information and metadata

        Returns:
            str: Formatted response ready for the user
        """
        return response

    # === IMPLEMENTATION METHODS ===
    # These will be provided in a full implementation but are inherited from current base.py
    # for now to maintain compatibility.

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Execute the tool - will be inherited from existing base.py for now."""
        # This will be implemented by importing from the current base.py
        # for backward compatibility during the migration
        raise NotImplementedError("Subclasses must implement execute method")

    def _parse_response(self, raw_text: str, request, model_info: Optional[dict] = None):
        """Parse response - will be inherited for now."""
        # Implementation inherited from current base.py
        raise NotImplementedError("Subclasses must implement _parse_response method")

    def get_request_working_directory(self, request) -> Optional[str]:
        """Use configured cwd unless a tool explicitly defines an invocation cwd."""
        return None

    def _resolve_cli_target(self, request, *, files=(), images=()):
        """Freeze routing and validate declared context before any host file read."""
        from clink.backend import get_backend

        backend = get_backend()
        self._backend_target = backend.resolve(
            cli_name=request.cli_name,
            role=request.role,
            model=request.model,
            working_directory=self.get_request_working_directory(request),
            images=tuple(images),
            temperature=request.temperature,
            thinking_mode=request.thinking_mode,
        )
        return backend.validate_files(self._backend_target, tuple(files), tuple(images), tool_name=self.get_name())

    async def _run_cli(self, request, prompt: str, system_prompt: str, *, files=(), images=(), require_search=False):
        """Delegate one advisory request through the sole execution boundary."""
        from clink.backend import get_backend
        from clink.contracts import CLIRequest
        from utils.call_state import get_current_call

        if "_backend_target" not in self.__dict__:
            self._resolve_cli_target(request, files=files, images=images)
        timing = {}
        call = get_current_call()
        if call is not None:
            timing = {"admitted_at": call.admitted_at, "deadline": call.deadline, "request_id": call.request_id}
        result = await get_backend().run(
            CLIRequest(
                prompt=prompt,
                target=self._backend_target,
                system_prompt=system_prompt,
                files=tuple(files),
                images=tuple(images),
                tool_name=self.get_name(),
                require_search=require_search,
                history_turns=tuple(self._current_arguments.get("_replay_history_turns", ())),
                history_omitted_turns=self._current_arguments.get("_replay_omitted_turns", 0),
                **timing,
            )
        )
        self._backend_metadata = result.to_metadata()
        return result
