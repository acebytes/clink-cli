"""Parser registry for clink."""

from __future__ import annotations

from .base import BaseParser, ParsedCLIResponse, ParserError
from .claude import ClaudeJSONParser
from .codex import CodexJSONLParser
from .gemini import GeminiJSONParser

_PARSER_CLASSES: dict[str, type[BaseParser]] = {
    CodexJSONLParser.name: CodexJSONLParser,
    GeminiJSONParser.name: GeminiJSONParser,
    ClaudeJSONParser.name: ClaudeJSONParser,
}


def get_parser(name: str) -> BaseParser:
    normalized = name.lower() if isinstance(name, str) else ""
    if normalized not in _PARSER_CLASSES:
        raise ParserError("No parser registered for the requested format")
    parser_cls = _PARSER_CLASSES[normalized]
    return parser_cls()


__all__ = [
    "BaseParser",
    "ParsedCLIResponse",
    "ParserError",
    "get_parser",
]
