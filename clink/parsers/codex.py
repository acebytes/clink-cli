"""Parser for Codex CLI JSONL output."""

from __future__ import annotations

from typing import Any

from .base import (
    MAX_SOURCE_URLS,
    BaseParser,
    ParsedCLIResponse,
    ParserError,
    bounded_counts,
    cli_failure,
    load_json,
    model_identifier,
    source_url,
)

_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "total_tokens",
    "reasoning_output_tokens",
)


class CodexJSONLParser(BaseParser):
    """Parse stdout emitted by `codex exec --json`."""

    name = "codex_jsonl"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
        agent_messages: list[str] = []
        terminal: dict[str, Any] | None = None
        search_performed = False
        source_urls: list[str] = []

        for line in lines:
            event = load_json(line, allow_codex_web_search_ids=True)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not event["type"]:
                raise ParserError("Codex CLI JSONL events must be typed objects")
            if "status" in event and not isinstance(event["status"], str):
                raise ParserError("Codex CLI event status must be textual")

            event_type = event["type"]
            if (
                event_type == "error"
                or event_type.endswith(".failed")
                or event_type in {"turn.cancelled", "turn.aborted", "turn.interrupted"}
                or event.get("status") in ("error", "failed", "cancelled", "aborted", "interrupted")
                or event.get("error") is not None
                or event.get("errors")
                or event.get("is_error", False) is not False
            ):
                raise cli_failure(event, stderr, message="Codex CLI reported a failed turn")
            if terminal is not None:
                raise ParserError("Codex CLI emitted events after its terminal result")

            if event_type.startswith("item."):
                item = event.get("item")
                if not isinstance(item, dict) or not isinstance(item.get("type"), str) or not item["type"]:
                    raise ParserError("Codex CLI item events must contain typed item objects")
                if "status" in item and not isinstance(item["status"], str):
                    raise ParserError("Codex CLI item status must be textual")
                if (
                    item["type"] == "error"
                    or item.get("status") in ("error", "failed", "cancelled", "aborted", "interrupted")
                    or item.get("error") is not None
                    or item.get("errors")
                    or item.get("is_error", False) is not False
                ):
                    raise cli_failure(item, stderr, message="Codex CLI reported a failed item")

            if event_type == "item.completed":
                if item["type"] == "web_search":
                    performed, urls = self._search_evidence(item, stderr)
                    search_performed = search_performed or performed
                    for url in urls:
                        if url not in source_urls and len(source_urls) < MAX_SOURCE_URLS:
                            source_urls.append(url)
                if item["type"] == "command_execution" and "exit_code" in item:
                    exit_code = item["exit_code"]
                    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                        raise ParserError("Codex CLI command exit code must be an integer")
                    if exit_code != 0:
                        raise cli_failure(item, stderr, message="Codex CLI command execution failed")
                if item["type"] == "agent_message":
                    text = item.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise ParserError("Codex CLI agent_message must contain nonempty text")
                    agent_messages.append(text.strip())
            elif event_type == "turn.completed":
                if event.get("status", "completed") not in ("completed", "success"):
                    raise ParserError("Codex CLI terminal status did not indicate success")
                if "usage" in event and not isinstance(event["usage"], dict):
                    raise ParserError("Codex CLI usage must be an object")
                terminal = event

        if terminal is None:
            raise cli_failure(stderr, message="Codex CLI JSONL output did not include turn.completed")
        if not agent_messages:
            raise cli_failure(
                stderr,
                message="Codex CLI JSONL output did not include an agent_message item",
                code="empty_response",
            )

        content = "\n\n".join(agent_messages).strip()
        metadata: dict[str, Any] = {}
        usage = bounded_counts(terminal.get("usage"), _USAGE_FIELDS)
        if usage:
            metadata["usage"] = usage
        actual_model = model_identifier(terminal.get("model"))
        if actual_model:
            metadata["actual_model"] = actual_model
        if search_performed:
            metadata["search_performed"] = True
            metadata["source_urls"] = source_urls

        return ParsedCLIResponse(content=content, metadata=metadata)

    def _search_evidence(self, item: dict[str, Any], stderr: str) -> tuple[bool, list[str]]:
        """Read only the source-tested flat text_result URL shape from returned results."""

        action = item.get("action")
        results = item.get("results")
        if "query" in item and not isinstance(item["query"], str):
            raise ParserError("Codex CLI web search query must be textual")
        if action is not None and not isinstance(action, dict):
            raise ParserError("Codex CLI web search action must be an object")
        action_type = action.get("type") if isinstance(action, dict) else None
        if isinstance(action, dict):
            if not isinstance(action_type, str):
                raise ParserError("Codex CLI web search action must be typed")
            for field in ("query", "url", "pattern"):
                if action.get(field) is not None and not isinstance(action[field], str):
                    raise ParserError("Codex CLI web search action text fields must be textual")
            queries = action.get("queries")
            if queries is not None and not (
                isinstance(queries, list) and all(isinstance(query, str) for query in queries)
            ):
                raise ParserError("Codex CLI web search queries must be an array of text")
        if results is None:
            return False, []
        if not isinstance(results, list):
            raise ParserError("Codex CLI web search results must be an array")

        urls: list[str] = []
        recognized = not results
        for result in results:
            if not isinstance(result, dict):
                continue
            if (
                result.get("type") in ("error", "failed")
                or result.get("status") in ("error", "failed", "cancelled", "aborted", "interrupted")
                or result.get("error") is not None
                or result.get("errors")
                or (result.get("is_error") is not None and result["is_error"] is not False)
            ):
                raise cli_failure(result, stderr, message="Codex CLI web search returned a failed result")
            if result.get("type") != "text_result":
                continue
            url = source_url(result.get("url"))
            if url:
                recognized = True
                if url not in urls and len(urls) < MAX_SOURCE_URLS:
                    urls.append(url)
        known_action = action_type in ("search", "open_page", "find_in_page")
        return action_type == "search" and recognized, urls if known_action else []
