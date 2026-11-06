"""Parser interfaces for clink runner outputs."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

MAX_METADATA_NUMBER = 2**53 - 1
MAX_SOURCE_URLS = 20
MAX_SOURCE_URL_LENGTH = 2048
_MODEL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_AUTHENTICATION_SIGNATURE = re.compile(
    r"\b(?:authentication[_ -](?:required|error|failed)|invalid[_ -](?:api[_ -]key|authentication)|"
    r"unauthenticated|not[_ -](?:logged[_ -]in|authenticated)|please (?:log|sign) in|"
    r"401 unauthorized|(?:http|status(?: code)?)\s*[:=]?\s*401)\b",
    re.IGNORECASE,
)
_RATE_LIMIT_SIGNATURE = re.compile(
    r"\b(?:rate[_ -]limit(?:ed|[_ -](?:exceeded|error))?|too[_ -]many[_ -]requests|"
    r"quota[_ -](?:has[_ -]been[_ -])?exceeded|resource_exhausted|"
    r"429 too many requests|(?:http|status(?: code)?)\s*[:=]?\s*429)\b",
    re.IGNORECASE,
)


@dataclass
class ParsedCLIResponse:
    """Result of parsing CLI stdout/stderr."""

    content: str
    metadata: dict[str, Any]


class ParserError(RuntimeError):
    """Raised when CLI output cannot be parsed into a structured response."""

    def __init__(self, message: str, code: str = "parse_error") -> None:
        super().__init__(message)
        self.code = code


def cli_failure(
    *diagnostics: object, message: str = "CLI reported a failure", code: str = "parse_error"
) -> ParserError:
    """Classify known failure signatures without returning diagnostic text."""

    # Only inspect bounded error fields, never arbitrary session/assistant bodies.
    pending = [(value, 0) for value in diagnostics]
    texts: list[str] = []
    while pending and len(texts) < 32:
        value, depth = pending.pop(0)
        if isinstance(value, str):
            texts.append(value[:8192])
        elif isinstance(value, int) and not isinstance(value, bool):
            if value == 401:
                texts.append("status 401")
            elif value == 429:
                texts.append("status 429")
        elif isinstance(value, dict) and depth < 3:
            pending.extend(
                (value[key], depth + 1)
                for key in ("error", "errors", "code", "type", "message", "status")
                if key in value
            )
        elif isinstance(value, list) and depth < 3:
            pending.extend((part, depth + 1) for part in value[:16])

    text = "\n".join(texts)
    if _AUTHENTICATION_SIGNATURE.search(text):
        return ParserError("CLI authentication is required", code="authentication_required")
    if _RATE_LIMIT_SIGNATURE.search(text):
        return ParserError("CLI rate limit was reached", code="rate_limited")
    return ParserError(message, code=code)


def load_json(text: str, *, allow_codex_web_search_ids: bool = False) -> object:
    """Decode strict JSON without preserving raw input in parser exceptions."""

    duplicate_ids: list[dict[str, Any]] = []

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                if (
                    allow_codex_web_search_ids
                    and key == "id"
                    and isinstance(result[key], str)
                    and isinstance(value, str)
                ):
                    duplicate_ids.append(result)
                else:
                    raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("Non-finite JSON constant")

    invalid = False
    try:
        result = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        invalid = True
    if invalid:
        raise ParserError("CLI returned malformed JSON")
    if duplicate_ids:
        # Codex rust-v0.166.0 flattens WebSearchItem.id into ThreadItem, which
        # already has its own id. Both are opaque and never enter metadata.
        # No other duplicate field, item type, or location is admitted.
        item = result.get("item") if isinstance(result, dict) else None
        if not (
            isinstance(result, dict)
            and result.get("type") in ("item.started", "item.updated", "item.completed")
            and isinstance(item, dict)
            and item.get("type") == "web_search"
            and len(duplicate_ids) == 1
            and duplicate_ids[0] is item
        ):
            raise ParserError("CLI returned malformed JSON")
    return result


def bounded_counts(value: object, fields: tuple[str, ...]) -> dict[str, int]:
    """Copy only finite nonnegative integer telemetry from an explicit allowlist."""

    if not isinstance(value, dict):
        return {}
    return {
        key: count
        for key in fields
        if isinstance((count := value.get(key)), int)
        and not isinstance(count, bool)
        and 0 <= count <= MAX_METADATA_NUMBER
    }


def bounded_number(value: object) -> int | float | None:
    """Return a bounded, nonnegative telemetry number, excluding booleans."""

    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= MAX_METADATA_NUMBER
        and math.isfinite(value)
    ):
        return value
    return None


def model_identifier(value: object) -> str | None:
    """Accept only bounded native model identifiers, excluding free-form bodies."""

    if isinstance(value, str) and _MODEL_IDENTIFIER.fullmatch(value):
        return value
    return None


def source_url(value: object) -> str | None:
    """Keep bounded HTTP(S) source locations without credentials or URL tokens."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SOURCE_URL_LENGTH
        or "\\" in value
        or any(character.isspace() or unicodedata.category(character) in ("Cc", "Cf", "Cs") for character in value)
    ):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            return None
        hostname = parts.hostname
        if "%" in hostname:
            return None
        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
        else:
            ascii_host = hostname.encode("idna").decode("ascii").rstrip(".")
            if len(ascii_host) > 253 or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in ascii_host.split(".")
            ):
                return None
        # Accessing port validates bracketed hosts and the numeric port range.
        _ = parts.port
    except (ValueError, UnicodeError):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class BaseParser:
    """Base interface for CLI output parsers."""

    name: str = "base"

    def parse(self, stdout: str, stderr: str) -> ParsedCLIResponse:
        raise NotImplementedError("Parsers must implement parse()")
