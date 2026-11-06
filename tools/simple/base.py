"""
Base class for simple MCP tools.

Simple tools follow a straightforward pattern:
1. Receive request
2. Prepare prompt (with absolute file paths, context, etc.)
3. Call AI model
4. Format and return response

They use the shared SchemaBuilder for consistent schema generation
and inherit all the conversation, file processing, and model handling
capabilities from BaseTool.
"""

import logging
from abc import abstractmethod
from typing import Any, Optional

from tools.shared.base_models import ToolRequest
from tools.shared.base_tool import BaseTool
from tools.shared.exceptions import ToolExecutionError
from tools.shared.schema_builders import SchemaBuilder


class SimpleTool(BaseTool):
    """
    Base class for simple (non-workflow) tools.

    Simple tools are request/response tools that don't require multi-step workflows.
    They benefit from:
    - Automatic schema generation using SchemaBuilder
    - Inherited conversation handling and file processing
    - Standardized model integration
    - Consistent error handling and response formatting

    To create a simple tool:
    1. Inherit from SimpleTool
    2. Implement get_tool_fields() to define tool-specific fields
    3. Implement prepare_prompt() for prompt preparation
    4. Optionally override format_response() for custom formatting
    5. Optionally override get_required_fields() for custom requirements

    Example:
        class ChatTool(SimpleTool):
            def get_name(self) -> str:
                return "chat"

            def get_tool_fields(self) -> Dict[str, Dict[str, Any]]:
                return {
                    "prompt": {
                        "type": "string",
                        "description": "Your question or idea...",
                    },
                    "absolute_file_paths": SimpleTool.FILES_FIELD,
                }

            def get_required_fields(self) -> List[str]:
                return ["prompt"]
    """

    # Common field definitions that simple tools can reuse
    FILES_FIELD = SchemaBuilder.SIMPLE_FIELD_SCHEMAS["absolute_file_paths"]
    IMAGES_FIELD = SchemaBuilder.COMMON_FIELD_SCHEMAS["images"]

    @abstractmethod
    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        """
        Return tool-specific field definitions.

        This method should return a dictionary mapping field names to their
        JSON schema definitions. Common fields (model, temperature, etc.)
        are added automatically by the base class.

        Returns:
            Dict mapping field names to JSON schema objects

        Example:
            return {
                "prompt": {
                    "type": "string",
                    "description": "The user's question or request",
                },
                "absolute_file_paths": SimpleTool.FILES_FIELD,  # Reuse common field
                "max_tokens": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum tokens for response",
                }
            }
        """
        pass

    def get_required_fields(self) -> list[str]:
        """
        Return list of required field names.

        Override this to specify which fields are required for your tool.
        The model field is automatically added if in auto mode.

        Returns:
            List of required field names
        """
        return []

    def get_annotations(self) -> Optional[dict[str, Any]]:
        """
        Return tool annotations. Simple tools are read-only by default.

        All simple tools perform operations without modifying the environment.
        They may call external AI models for analysis or conversation, but they
        don't write files or make system changes.

        Override this method if your simple tool needs different annotations.

        Returns:
            Dictionary with readOnlyHint set to True
        """
        return {"readOnlyHint": True}

    def format_response(self, response: str, request, model_info: Optional[dict] = None) -> str:
        """
        Format the AI response before returning to the client.

        This is a hook method that subclasses can override to customize
        response formatting. The default implementation returns the response as-is.

        Args:
            response: The raw response from the AI model
            request: The validated request object
            model_info: Optional model information dictionary

        Returns:
            Formatted response string
        """
        return response

    def get_input_schema(self) -> dict[str, Any]:
        """
        Generate the complete input schema using SchemaBuilder.

        This method automatically combines:
        - Tool-specific fields from get_tool_fields()
        - Common fields (temperature, thinking_mode, etc.)
        - Model field with proper auto-mode handling
        - Required fields from get_required_fields()

        Tools can override this method for custom schema generation while
        still benefiting from SimpleTool's convenience methods.

        Returns:
            Complete JSON schema for the tool
        """
        required_fields = list(self.get_required_fields())
        return SchemaBuilder.build_schema(
            tool_specific_fields=self.get_tool_fields(),
            required_fields=required_fields,
            model_field_schema=self.get_model_field_schema(),
            auto_mode=self.is_effective_auto_mode(),
        )

    def get_request_model(self):
        """
        Return the request model class.

        Simple tools use the base ToolRequest by default.
        Override this if your tool needs a custom request model.
        """
        return ToolRequest

    # Hook methods for safe attribute access without hasattr/getattr

    def get_request_model_name(self, request) -> Optional[str]:
        """Get model name from request. Override for custom model name handling."""
        try:
            return request.model
        except AttributeError:
            return None

    def get_request_images(self, request) -> list:
        """Get images from request. Override for custom image handling."""
        try:
            return request.images if request.images is not None else []
        except AttributeError:
            return []

    def get_request_continuation_id(self, request) -> Optional[str]:
        """Get continuation_id from request. Override for custom continuation handling."""
        try:
            return request.continuation_id
        except AttributeError:
            return None

    def get_request_prompt(self, request) -> str:
        """Get prompt from request. Override for custom prompt handling."""
        try:
            return request.prompt
        except AttributeError:
            return ""

    def get_request_temperature(self, request) -> Optional[float]:
        """Get temperature from request. Override for custom temperature handling."""
        try:
            return request.temperature
        except AttributeError:
            return None

    def get_request_thinking_mode(self, request) -> Optional[str]:
        """Get thinking_mode from request. Override for custom thinking mode handling."""
        try:
            return request.thinking_mode
        except AttributeError:
            return None

    def get_request_files(self, request) -> list:
        """Get absolute file paths from request. Override for custom file handling."""
        try:
            files = request.absolute_file_paths
        except AttributeError:
            files = None
        if files is None:
            return []
        return files

    def get_request_as_dict(self, request) -> dict:
        """Convert request to dictionary. Override for custom serialization."""
        try:
            # Try Pydantic v2 method first
            return request.model_dump()
        except AttributeError:
            try:
                # Fall back to Pydantic v1 method
                return request.dict()
            except AttributeError:
                # Last resort - convert to dict manually
                return {"prompt": self.get_request_prompt(request)}

    def set_request_files(self, request, files: list) -> None:
        """Set absolute file paths on request. Override for custom file setting."""
        try:
            request.absolute_file_paths = files
        except AttributeError:
            pass

    def get_actually_processed_files(self) -> list:
        """Get actually processed files. Override for custom file tracking."""
        try:
            return self._actually_processed_files
        except AttributeError:
            return []

    async def execute(self, arguments: dict[str, Any]) -> list:
        """Validate and delegate once; the server owns replay and atomic recording."""
        from mcp.types import TextContent
        from pydantic import ValidationError

        from clink.contracts import BackendError
        from tools.models import ToolOutput
        from utils.call_state import StateError

        self._current_arguments = arguments
        try:
            request = self.get_request_model()(**arguments)
            path_error = self._validate_file_paths(request)
            if path_error:
                raise BackendError(
                    "path_not_allowed", path_error, "Supply existing absolute paths inside allowed roots."
                )
            files = self._resolve_cli_target(
                request, files=self.get_request_files(request), images=self.get_request_images(request)
            )
            self.set_request_files(request, list(files))
            prompt = await self.prepare_prompt(request)
            result = await self._run_cli(
                request,
                prompt,
                self.get_system_prompt() + "\n\n" + self.get_language_instruction(),
                files=self.get_request_files(request),
                images=self.get_request_images(request),
            )
            output = self._parse_response(result.content, request, {"metadata": result.to_metadata()})
            return [TextContent(type="text", text=output.model_dump_json())]
        except (ToolExecutionError, StateError):
            raise
        except BackendError as exc:
            output = ToolOutput(status="error", content=str(exc), metadata=exc.to_dict())
            raise ToolExecutionError(output.model_dump_json()) from exc
        except (ValidationError, ValueError):
            output = ToolOutput(
                status="error",
                content="Request validation failed; check the declared fields.",
                metadata={"code": "invalid_request"},
            )
            raise ToolExecutionError(output.model_dump_json()) from None
        except Exception as exc:
            logging.getLogger(__name__).error(
                "tool_execution_failed tool=%s error_type=%s", self.get_name(), type(exc).__name__
            )
            output = ToolOutput(
                status="error",
                content="The tool could not complete this request.",
                metadata={"code": "tool_error", "error_type": type(exc).__name__},
            )
            raise ToolExecutionError(output.model_dump_json()) from None

    def _parse_response(self, raw_text: str, request, model_info: Optional[dict] = None):
        """Keep the public envelope while sharing transaction-owned continuation IDs."""
        from tools.models import ContinuationOffer, ToolOutput
        from utils.call_state import get_current_call
        from utils.conversation_memory import MAX_CONVERSATION_TURNS

        metadata = dict((model_info or {}).get("metadata", {}))
        metadata["tool_name"] = self.get_name()
        offer = None
        call = get_current_call()
        if call is not None:
            remaining = max(0, MAX_CONVERSATION_TURNS - call.context.successful_turns - 2)
            if remaining:
                offer = ContinuationOffer(
                    continuation_id=call.continuation_id,
                    remaining_turns=remaining,
                    note="Reuse this continuation_id to preserve conversation context.",
                )
        return ToolOutput(
            status="continuation_available" if offer else "success",
            content=self.format_response(raw_text, request, model_info),
            continuation_offer=offer,
            metadata=metadata,
        )

    # Convenience methods for common tool patterns

    def build_standard_prompt(
        self, system_prompt: str, user_content: str, request, file_context_title: str = "CONTEXT FILES"
    ) -> str:
        """
        Build a standard prompt with system prompt, user content, and optional files.

        This is a convenience method that handles the common pattern of:
        1. Adding file content if present
        2. Checking token limits
        3. Adding web search instructions
        4. Combining everything into a well-formatted prompt

        Args:
            system_prompt: The system prompt for the tool
            user_content: The main user request/content
            request: The validated request object
            file_context_title: Title for the file context section

        Returns:
            Complete formatted prompt ready for the AI model
        """
        # Check size limits against raw user input before enriching with internal context
        content_to_validate = self.get_prompt_content_for_size_validation(user_content)
        self._validate_token_limit(content_to_validate, "Content")

        # Only the common backend reads/embeds declared files, once under its byte cap.
        files = self.get_request_files(request)
        if files:
            references = "\n".join(f"- {path}" for path in files)
            user_content = f"{user_content}\n\n=== {file_context_title} REFERENCES ===\n{references}"

        # Add standardized web search guidance
        websearch_instruction = self.get_websearch_instruction(self.get_websearch_guidance())

        # Combine system prompt with user content
        full_prompt = f"""{system_prompt}{websearch_instruction}

=== USER REQUEST ===
{user_content}
=== END REQUEST ===

Please provide a thoughtful, comprehensive response:"""

        return full_prompt

    def get_prompt_content_for_size_validation(self, user_content: str) -> str:
        """
        Override to use original user prompt for size validation when conversation history is embedded.

        When server.py embeds conversation history into the prompt field, it also stores
        the original user prompt in _original_user_prompt. We use that for size validation
        to avoid incorrectly triggering size limits due to conversation history.

        Args:
            user_content: The user content (may include conversation history)

        Returns:
            The original user prompt if available, otherwise the full user content
        """
        # Check if we have the current arguments from execute() method
        current_args = getattr(self, "_current_arguments", None)
        if current_args:
            # If server.py embedded conversation history, it stores original prompt separately
            original_user_prompt = current_args.get("_original_user_prompt")
            if original_user_prompt is not None:
                # Use original user prompt for size validation (excludes conversation history)
                return original_user_prompt

        # Fallback to default behavior (validate full user content)
        return user_content

    def get_websearch_guidance(self) -> Optional[str]:
        """
        Return tool-specific web search guidance.

        Override this to provide tool-specific guidance for when web searches
        would be helpful. Return None to use the default guidance.

        Returns:
            Tool-specific web search guidance or None for default
        """
        return None

    def handle_prompt_file_with_fallback(self, request) -> str:
        """Use only an explicitly attached prompt.txt as this call's primary input."""
        import os

        from clink.contracts import BackendError

        files = self.get_request_files(request)
        original = self._current_arguments.get("_original_arguments")
        if original is None:
            explicit_files = files
        else:
            originals = {os.path.realpath(os.path.expanduser(path)) for path in original.get("absolute_file_paths", [])}
            explicit_files = [path for path in files if os.path.realpath(path) in originals]
        prompt_content, updated_files = self.handle_prompt_file(explicit_files)
        if prompt_content is not None:
            consumed = set(explicit_files) - set(updated_files or [])
            self.set_request_files(request, [path for path in files if path not in consumed])
        user_content = prompt_content if prompt_content is not None else self.get_request_prompt(request)
        self._effective_user_prompt = user_content
        size_check = self.check_prompt_size(user_content)
        if size_check:
            raise BackendError("input_limit", size_check["content"], size_check["metadata"]["remedy"])
        return user_content

    def get_chat_style_websearch_guidance(self) -> str:
        """
        Get Chat tool-style web search guidance.

        Returns web search guidance that matches the original Chat tool pattern.
        This is useful for tools that want to maintain the same search behavior.

        Returns:
            Web search guidance text
        """
        return """When discussing topics, consider if searches for these would help:
- Documentation for any technologies or concepts mentioned
- Current best practices and patterns
- Recent developments or updates
- Community discussions and solutions"""

    def supports_custom_request_model(self) -> bool:
        """
        Indicate whether this tool supports custom request models.

        Simple tools support custom request models by default. Tools that override
        get_request_model() to return something other than ToolRequest should
        return True here.

        Returns:
            True if the tool uses a custom request model
        """
        return self.get_request_model() != ToolRequest

    def _validate_file_paths(self, request) -> Optional[str]:
        """
        Validate that all file paths in the request are absolute paths.

        This is a security measure to prevent path traversal attacks and ensure
        proper access control. All file paths must be absolute (starting with '/').

        Args:
            request: The validated request object

        Returns:
            Optional[str]: Error message if validation fails, None if all paths are valid
        """
        import os

        # Check if request has absolute file paths attribute (legacy tools may still provide 'files')
        files = self.get_request_files(request)
        if files:
            for file_path in files:
                if not os.path.isabs(file_path):
                    return (
                        f"Error: All file paths must be FULL absolute paths to real files / folders - DO NOT SHORTEN. "
                        f"Received relative path: {file_path}\n"
                        f"Please provide the full absolute path starting with '/' (must be FULL absolute paths to real files / folders - DO NOT SHORTEN)"
                    )

        return None

    def prepare_chat_style_prompt(self, request, system_prompt: str = None) -> str:
        """
        Prepare a prompt using Chat tool-style patterns.

        This convenience method replicates the Chat tool's prompt preparation logic:
        1. Handle prompt.txt file if present
        2. Add file context with specific formatting
        3. Add web search guidance
        4. Format with system prompt

        Args:
            request: The validated request object
            system_prompt: System prompt to use (uses get_system_prompt() if None)

        Returns:
            Complete formatted prompt
        """
        # Use provided system prompt or get from tool
        if system_prompt is None:
            system_prompt = self.get_system_prompt()

        # Get user content (handles prompt.txt files)
        user_content = self.handle_prompt_file_with_fallback(request)

        # Build standard prompt with Chat-style web search guidance
        websearch_guidance = self.get_chat_style_websearch_guidance()

        # Override the websearch guidance temporarily
        original_guidance = self.get_websearch_guidance
        self.get_websearch_guidance = lambda: websearch_guidance

        try:
            full_prompt = self.build_standard_prompt(system_prompt, user_content, request, "CONTEXT FILES")
        finally:
            # Restore original guidance method
            self.get_websearch_guidance = original_guidance

        if system_prompt:
            marker = "\n\n=== USER REQUEST ===\n"
            if marker in full_prompt:
                _, user_section = full_prompt.split(marker, 1)
                return f"=== USER REQUEST ===\n{user_section}"

        return full_prompt
