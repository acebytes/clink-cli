"""Provider-independent, immutable contracts for advisory CLI delegation."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class BackendError(RuntimeError):
    """An explicit terminal backend failure with public, content-free diagnostics."""

    def __init__(self, code: str, message: str, remedy: str = "", metadata: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.remedy = remedy
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "remedy": self.remedy, **self.metadata}


@dataclass(frozen=True)
class ClientSnapshot:
    """Deep immutable launch configuration, independent of future registry changes."""

    name: str
    adapter: str
    executable: tuple[str, ...]
    working_dir: Path
    allowed_roots: tuple[Path, ...]
    config_args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    timeout_seconds: int = 1800
    parser: str = ""
    default_model: str | None = None


@dataclass(frozen=True)
class RoleSnapshot:
    """Resolved role expertise, captured before any asynchronous execution."""

    name: str
    prompt_text: str
    role_args: tuple[str, ...] = ()
    description: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class AdapterPolicy:
    """Observed adapter admission, never inferred from installation or help alone."""

    adapter: str
    version: str | None = None
    verified: bool = False
    verification_status: str = "unverified"
    images: bool = False
    native_search: bool = False
    file_references: bool = False
    repository_instructions: str = "unverified"
    read_scope: str = "unverified"
    evidence: str | None = None
    evidence_sha256: str | None = None
    executable_sha256: str | None = None
    recipe_sha256: str | None = None
    resource_files: tuple[tuple[str, str], ...] = ()
    supported_models: tuple[str, ...] = ()
    native_default_model: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_files", tuple(tuple(pair) for pair in self.resource_files))
        object.__setattr__(self, "supported_models", tuple(self.supported_models))


@dataclass(frozen=True)
class BackendTarget:
    """A frozen client/role/model selection ready for common preflight."""

    client: ClientSnapshot
    role: RoleSnapshot
    requested_model: str | None
    effective_model: str | None
    working_directory: Path
    policy: AdapterPolicy
    warnings: tuple[tuple[str, str], ...] = ()
    default_origin: str = "native"

    @property
    def cli_name(self) -> str:
        return self.client.name

    @property
    def role_prompt(self) -> str:
        return self.role.prompt_text


@dataclass(frozen=True)
class CLIRequest:
    """One immutable, bounded call; deadline includes upstream lock and queue waits."""

    prompt: str
    target: BackendTarget
    system_prompt: str = ""
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    admitted_at: float = field(default_factory=time.monotonic)
    deadline: float | None = None
    tool_name: str = "clink"
    require_search: bool = False
    context_manifest_json: str | None = None
    history_turns: tuple[str, ...] = ()
    history_omitted_turns: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not isinstance(self.system_prompt, str):
            raise BackendError("invalid_config", "CLI prompt and tool instructions must be strings.")
        if self.context_manifest_json is not None and not isinstance(self.context_manifest_json, str):
            raise BackendError("invalid_config", "The context manifest must be immutable JSON text.")
        if isinstance(self.files, str) or isinstance(self.images, str) or isinstance(self.history_turns, str):
            raise BackendError("invalid_config", "Context paths and replay turns must be sequences of strings.")
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(self, "history_turns", tuple(self.history_turns))
        if any(not isinstance(turn, str) for turn in self.history_turns):
            raise BackendError("invalid_config", "Replay turns must be strings.")
        if type(self.history_omitted_turns) is not int or self.history_omitted_turns < 0:
            raise BackendError("invalid_config", "Omitted replay turn count must be a nonnegative integer.")
        for name, value in (("admitted_at", self.admitted_at), ("deadline", self.deadline)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                raise BackendError("invalid_config", f"CLI request {name} must be a finite monotonic timestamp.")


@dataclass(frozen=True)
class CLIResult:
    """Normalized output without raw streams, argv, or native session identities."""

    content: str
    status: str
    request_id: str
    cli_name: str
    role: str
    requested_model: str | None
    actual_model: str | None
    duration_seconds: float
    warnings: tuple[dict[str, str], ...] = ()
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "backend": {"cli_name": self.cli_name, "role": self.role},
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "warnings": list(self.warnings),
            "truncated": self.truncated,
            **self.metadata,
        }
