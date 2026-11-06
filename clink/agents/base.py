"""Legacy agent facade; every execution uses the shared advisory backend."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from clink.contracts import BackendError, CLIRequest
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers import ParsedCLIResponse


@dataclass
class AgentOutput:
    """Compatibility response without raw streams, commands or native sessions."""

    parsed: ParsedCLIResponse
    sanitized_command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    parser_name: str
    output_file_content: str | None = None


class CLIAgentError(BackendError):
    """Compatibility name for explicit backend errors."""


class BaseCLIAgent:
    """Preserve old callers while prohibiting independent launch-policy paths."""

    def __init__(self, client: ResolvedCLIClient):
        self.client = client

    async def run(
        self,
        *,
        role: ResolvedCLIRole,
        prompt: str,
        system_prompt: str | None = None,
        files: Sequence[str],
        images: Sequence[str],
    ) -> AgentOutput:
        from clink.backend import get_backend

        backend = get_backend()
        try:
            target = backend.resolve(cli_name=self.client.name, role=role.name, images=tuple(images))
            result = await backend.run(
                CLIRequest(
                    prompt=prompt,
                    target=target,
                    system_prompt=system_prompt or "",
                    files=tuple(files),
                    images=tuple(images),
                )
            )
        except BackendError as exc:
            raise CLIAgentError(exc.code, str(exc), exc.remedy, exc.metadata) from exc
        return AgentOutput(
            parsed=ParsedCLIResponse(result.content, result.to_metadata()),
            sanitized_command=[],
            returncode=0,
            stdout="",
            stderr="",
            duration_seconds=result.duration_seconds,
            parser_name=target.client.parser,
        )
