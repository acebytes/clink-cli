"""Strict native generation v2 contracts, kept separate from the v1 authority boundary."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clink.generation_contracts import (
    INPUT_BYTES,
    INSTRUCTIONS_BYTES,
    OUTPUT_BYTES,
    PROMPT_BYTES,
    RAW_REQUEST_BYTES,
    RESULT_BYTES,
    STDIN_SECONDS,
    GenerationInputError,
    GenerationRequest,
    _uuid,
    canonical_json,
    execution_identity,
    parse_request,
    result_exit_code,
    sha256,
    terminal_result,
)

__all__ = [
    "INPUT_BYTES",
    "INSTRUCTIONS_BYTES",
    "OUTPUT_BYTES",
    "PROFILE",
    "PROJECT_DIR_BYTES",
    "PROMPT_BYTES",
    "RAW_REQUEST_BYTES",
    "RESULT_BYTES",
    "STDIN_SECONDS",
    "GenerationInputError",
    "NativeGenerationRequest",
    "canonical_json",
    "execution_native_identity",
    "parse_native_request",
    "result_exit_code",
    "sha256",
    "terminal_native_result",
]

PROFILE = "generation_native_v1"
PROJECT_DIR_BYTES = 4096
_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "cli",
        "model",
        "instructions",
        "input",
        "timeout_seconds",
        "execution_mode",
        "project_dir",
    }
)
_V3_FIELDS = (_FIELDS - {"model"}) | {"model_selection"}


@dataclass(frozen=True)
class NativeModelSelection:
    """Capture the strategy independently of a CLI's unknowable native default."""

    kind: str
    id: str | None = None

    def payload(self) -> dict[str, str]:
        return {"kind": self.kind, **({"id": self.id} if self.id is not None else {})}


@dataclass(frozen=True)
class NativeGenerationRequest(GenerationRequest):
    """Keep supplied strings intact and bind native authority to an explicit project."""

    model: str | None
    project_dir: str
    execution_mode: str = "native"
    schema_version: int = 2
    model_selection: NativeModelSelection | None = None

    @property
    def selected_model(self) -> str | None:
        return self.model_selection.id if self.model_selection is not None else self.model

    def effective_payload(self) -> dict[str, Any]:
        payload = {
            **super().effective_payload(),
            "schema_version": self.schema_version,
            "execution_mode": self.execution_mode,
            "project_dir": self.project_dir,
        }
        if self.schema_version == 3:
            payload.pop("model")
            payload["model_selection"] = self.model_selection.payload() if self.model_selection is not None else None
        return payload


def _concrete_claude_model(model: object) -> bool:
    return isinstance(model, str) and model.startswith("claude-") and any(char in "0123456789" for char in model)


def parse_native_request(raw: bytes, *, started_at: float | None = None) -> NativeGenerationRequest:
    """Validate v2's explicit native selection without rewriting its hashed project path."""
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

    version = data.get("schema_version")
    fields = _V3_FIELDS if type(version) is int and version == 3 else _FIELDS
    if set(data) - fields or not (fields - {"timeout_seconds"}).issubset(data):
        reject("The request contains unknown fields or is missing required fields.")
    if type(version) is not int or version not in (2, 3):
        reject("Only integer schema_version 2 or 3 is supported.")
    if data["execution_mode"] != "native":
        reject("Generation v2 requires explicit execution_mode native.")

    selection = None
    if version == 3:
        value = data["model_selection"]
        if not isinstance(value, dict):
            reject("model_selection must be one closed selection object.")
        if value.get("kind") == "explicit" and set(value) == {"kind", "id"}:
            if data["cli"] not in ("codex", "claude"):
                reject("Explicit selection is supported only for Codex and Claude generation.")
            selection = NativeModelSelection("explicit", value["id"])
        elif value.get("kind") == "native_default" and set(value) == {"kind"}:
            if data["cli"] not in ("grok", "agy"):
                reject("Native-default selection is supported only for Grok and Antigravity generation.")
            selection = NativeModelSelection("native_default")
        else:
            reject("model_selection contains unknown, missing or conflicting fields.")
    elif data["cli"] not in ("codex", "claude"):
        reject("Generation v2 accepts explicit cli codex or claude.")
    model = selection.id if selection is not None else data["model"]
    if data["cli"] == "claude" and not _concrete_claude_model(model):
        reject("Claude generation requires a full native model identifier, without aliases.")

    # Reuse v1's common validation without extending its CLI authority.
    common = {
        key: value for key, value in data.items() if key not in {"execution_mode", "project_dir", "model_selection"}
    }
    common["schema_version"] = 1
    common["cli"] = "codex"
    # V1 validates the shared text/timeout fields. This value is validation-only;
    # the captured native-default request has no concrete model identifier.
    common["model"] = "native_default" if selection is not None and selection.kind == "native_default" else model
    try:
        common_raw = canonical_json(common)
    except (ValueError, RecursionError):
        reject("Request fields must contain valid Unicode and finite JSON values.")
    request = parse_request(common_raw, started_at=start)

    project_dir = data["project_dir"]
    if not isinstance(project_dir, str) or not project_dir or "\x00" in project_dir:
        reject("project_dir must be an existing absolute canonical directory.")
    try:
        project_size = len(project_dir.encode("utf-8"))
    except UnicodeError:
        reject("project_dir must contain valid Unicode.")
    if project_size > PROJECT_DIR_BYTES:
        reject("project_dir exceeds the 4096 UTF-8 byte limit.")
    try:
        path = Path(project_dir)
        valid_project = False
        if path.is_absolute():
            resolved = path.resolve(strict=True)
            valid_project = str(resolved) == project_dir and resolved.is_dir()
    except (OSError, RuntimeError, ValueError):
        reject("project_dir must be an existing absolute canonical directory.")
    if not valid_project:
        reject("project_dir must be an existing absolute canonical directory.")

    return NativeGenerationRequest(
        request_id=request.request_id,
        cli=data["cli"],
        model=None if selection is not None and selection.kind == "native_default" else request.model,
        instructions=request.instructions,
        input=request.input,
        timeout_ms=request.timeout_ms,
        started_at=request.started_at,
        project_dir=project_dir,
        schema_version=version,
        model_selection=selection,
    )


def execution_native_identity(request: NativeGenerationRequest | None = None) -> dict[str, Any]:
    identity = {
        **execution_identity(request),
        "profile": PROFILE,
        "catalog_sha256": None,
        "execution_mode": "native",
        "project_dir": request.project_dir if request else None,
        "side_effects_possible": False,
        "permission_observability": "limited",
        "fulfillment_verified": False,
    }
    if request is not None and request.schema_version == 3:
        identity["model_selection"] = request.model_selection.payload() if request.model_selection is not None else None
        identity["requested_model"] = request.selected_model
        identity["effective_model"] = request.selected_model
    return identity


def terminal_native_result(
    *,
    started_at: float,
    request: NativeGenerationRequest | None = None,
    request_id: str | None = None,
    status: str = "failed",
    output: str | None = None,
    truncated: bool = False,
    code: str | None = None,
    message: str = "",
    retry_after_seconds: int | float | None = None,
    execution: dict[str, Any] | None = None,
    schema_version: int = 2,
) -> dict[str, Any]:
    result = terminal_result(
        started_at=started_at,
        request=request,
        request_id=request_id,
        status=status,
        output=output,
        truncated=truncated,
        code=code,
        message=message,
        retry_after_seconds=retry_after_seconds,
        execution=execution if execution is not None else execution_native_identity(request),
    )
    result["schema_version"] = request.schema_version if request is not None else schema_version
    if result["schema_version"] == 3 and request is None:
        result["execution"]["model_selection"] = None
    return result
