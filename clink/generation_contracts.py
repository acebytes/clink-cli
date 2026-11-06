"""Strict, content-preserving contracts for one programmatic generation attempt."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from clink.contracts import BackendError

RAW_REQUEST_BYTES = 1024 * 1024
PROMPT_BYTES = 1024 * 1024
RESULT_BYTES = 1024 * 1024
INSTRUCTIONS_BYTES = 65536
INPUT_BYTES = 262144
OUTPUT_BYTES = 131072
STDIN_SECONDS = 10.0
PROFILE = "generation_v1"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_FIELDS = frozenset({"schema_version", "request_id", "cli", "model", "instructions", "input", "timeout_seconds"})


class GenerationInputError(BackendError):
    """A rejected request, with only an independently validated request identity."""

    def __init__(self, code: str, message: str, request_id: str | None = None):
        super().__init__(code, message)
        self.request_id = request_id


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    # Accept UUID hex case, but reject alternate non-UUID wire forms.
    return str(parsed) if value.lower() == str(parsed) else None


@dataclass(frozen=True)
class GenerationRequest:
    """Validated strings remain byte-identical; effective timeout is integer milliseconds."""

    request_id: str
    cli: str
    model: str
    instructions: str
    input: str
    timeout_ms: int
    started_at: float

    @property
    def deadline(self) -> float:
        return self.started_at + self.timeout_ms / 1000

    @property
    def instructions_sha256(self) -> str:
        return sha256(self.instructions.encode("utf-8"))

    @property
    def input_sha256(self) -> str:
        return sha256(self.input.encode("utf-8"))

    @property
    def request_sha256(self) -> str:
        return sha256(canonical_json(self.effective_payload()))

    def effective_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "cli": self.cli,
            "model": self.model,
            "instructions": self.instructions,
            "input": self.input,
            "timeout_ms": self.timeout_ms,
        }


def parse_request(raw: bytes, *, started_at: float | None = None) -> GenerationRequest:
    """Reject ambiguous JSON and coercions before any registry or executable access."""
    start = time.monotonic() if started_at is None else started_at
    if len(raw) > RAW_REQUEST_BYTES:
        raise GenerationInputError("input_limit", "The raw request exceeds the 1 MiB byte limit.")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise ValueError("nonfinite number")

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError):
        raise GenerationInputError("invalid_request", "The request must be one valid UTF-8 JSON object.") from None
    if not isinstance(data, dict):
        raise GenerationInputError("invalid_request", "The request must be a JSON object.")
    request_id = _uuid(data.get("request_id"))

    def reject(message: str, code: str = "invalid_request") -> None:
        raise GenerationInputError(code, message, request_id)

    if set(data) - _FIELDS or not (_FIELDS - {"timeout_seconds"}).issubset(data):
        reject("The request contains unknown fields or is missing required fields.")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        reject("Only integer schema_version 1 is supported.")
    if request_id is None:
        reject("request_id must be a UUID string.")
    if data["cli"] != "codex":
        reject("Generation v1 accepts only cli codex.")
    model = data["model"]
    if not isinstance(model, str) or not _MODEL.fullmatch(model) or model == "auto":
        reject("model must be an explicit bounded native identifier.")
    for field, maximum in (("instructions", INSTRUCTIONS_BYTES), ("input", INPUT_BYTES)):
        value = data[field]
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            reject("instructions and input must be nonblank strings without NUL.")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError:
            reject("Request strings must contain valid Unicode.")
        if size > maximum:
            reject("A request text field exceeds its UTF-8 byte limit.", "input_limit")
    timeout = data.get("timeout_seconds", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        reject("timeout_seconds must be a finite number between 1 and 1800.")
    try:
        valid_timeout = 1 <= timeout <= 1800 and math.isfinite(timeout)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        reject("timeout_seconds must be a finite number between 1 and 1800.")
    return GenerationRequest(
        request_id, "codex", model, data["instructions"], data["input"], math.ceil(float(timeout) * 1000), start
    )


def execution_identity(request: GenerationRequest | None = None) -> dict[str, Any]:
    return {
        "cli": request.cli if request else None,
        "profile": PROFILE,
        "requested_model": request.model if request else None,
        "effective_model": request.model if request else None,
        "actual_model": None,
        "recipe_sha256": None,
        "executable_sha256": None,
        "catalog_sha256": None,
        "admission": "not_checked",
    }


def terminal_result(
    *,
    started_at: float,
    request: GenerationRequest | None = None,
    request_id: str | None = None,
    status: str = "failed",
    output: str | None = None,
    truncated: bool = False,
    code: str | None = None,
    message: str = "",
    retry_after_seconds: int | float | None = None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "request_id": request.request_id if request else request_id,
        "status": status,
        "output": output,
        "truncated": truncated,
        "error": (
            {"code": code, "message": message[:1024], "retry_after_seconds": retry_after_seconds} if code else None
        ),
        "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
        "request_sha256": request.request_sha256 if request else None,
        "instructions_sha256": request.instructions_sha256 if request else None,
        "input_sha256": request.input_sha256 if request else None,
        "execution": execution if execution is not None else execution_identity(request),
        "warnings": [],
    }


def result_exit_code(result: dict[str, Any]) -> int:
    status = result["status"]
    if status == "completed":
        return 0
    if status == "incomplete":
        return 3
    if status == "timed_out":
        return 124
    if status == "cancelled":
        return 130
    if (result.get("error") or {}).get("code") in {"invalid_request", "input_limit", "invalid_invocation"}:
        return 2
    return 1
