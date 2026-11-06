"""Focused contracts for native jobs, separate from advisory CLI execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from clink.contracts import BackendTarget


@dataclass(frozen=True)
class ParsedNativeResponse:
    """Keep private session identity separate from bounded public metadata."""

    state: str
    content: str = ""
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FrozenNativeContext:
    """Pin native identity and names, without caller text or resolved environment values."""

    target: BackendTarget
    profile_id: str
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    deadline: float | None = None
    environment_names: tuple[str, ...] = ()
    required_environment_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeRunOutcome:
    """Separate terminal execution state from agent claims and private native identity."""

    state: str
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    partial_effects: bool = True


class NativeRuntime(Protocol):
    """Execute once and finish child cleanup before returning or propagating cancellation."""

    def prepare(
        self,
        cli_name: str,
        role: str | None,
        model: str | None,
        project_dir: str,
        files: tuple[str, ...] = (),
        images: tuple[str, ...] = (),
        absolute_deadline: float | None = None,
    ) -> FrozenNativeContext: ...

    async def run(
        self,
        context: FrozenNativeContext,
        prompt: str,
        session_id: str | None = None,
        *,
        attempt_id: str,
        started: Callable[[], None] | None = None,
    ) -> NativeRunOutcome: ...
