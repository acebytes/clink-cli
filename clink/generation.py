"""Single-attempt generation through the existing bounded process supervisor."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from clink.backend import _directory
from clink.contracts import BackendError
from clink.generation_contracts import (
    OUTPUT_BYTES,
    PROMPT_BYTES,
    GenerationRequest,
    canonical_json,
    execution_identity,
    sha256,
    terminal_result,
)
from clink.generation_policy import (
    CATALOG_SHA256,
    DEFAULT_ADMISSION,
    GENERATION_INSTRUCTIONS,
    RUNTIME_BLOCKERS,
    GenerationAdmission,
    generation_arguments,
    generation_catalog_digest,
    generation_recipe_digest,
    supported_generation_models,
)
from clink.parsers.base import ParserError, cli_failure
from clink.parsers.codex_generation import CodexGenerationParser, parser_contract_digest
from clink.policy import ENVIRONMENT_KEYS, executable_digest
from clink.registry import ClinkRegistry, RegistryLoadError
from clink.runner import ProcessPool


def compose_prompt(request: GenerationRequest) -> bytes:
    """Keep exact dynamic strings in stdin; fixed authority uses the native developer role."""
    content = canonical_json({"instructions": request.instructions, "input": request.input})
    if len(GENERATION_INSTRUCTIONS.encode("utf-8")) + len(content) > PROMPT_BYTES:
        raise BackendError("input_limit", "The assembled generation prompt exceeds the 1 MiB byte limit.")
    return content


class GenerationService:
    """Keep generation admission and authority separate from consultation and native tasks."""

    def __init__(
        self,
        registry: ClinkRegistry | None = None,
        pool: ProcessPool | None = None,
        admission: GenerationAdmission | None = None,
    ):
        self.registry = registry
        self.pool = pool if pool is not None else ProcessPool()
        self.admission = admission if admission is not None else DEFAULT_ADMISSION

    @staticmethod
    def _require_runtime_ceiling() -> None:
        """Fail closed on implementation limits independently of injected admission evidence."""
        if RUNTIME_BLOCKERS:
            raise BackendError(
                "policy_unverified",
                "Generation is blocked: ordinary_mcp_runtime_ceiling_unavailable. "
                "The pinned recipe cannot suppress ordinary MCP servers at runtime.",
            )

    @staticmethod
    def _evidence_digest(admission: GenerationAdmission) -> str:
        if not admission.evidence_path:
            raise BackendError("policy_unverified", "Generation admission has no supporting evidence binding.")
        path = Path(admission.evidence_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        try:
            with path.open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
        except OSError:
            raise BackendError("policy_unverified", "Generation admission evidence is unavailable.") from None
        if len(content) > 1024 * 1024:
            raise BackendError("policy_unverified", "Generation admission evidence exceeds its bounded size.")
        return sha256(content)

    def _verify_bindings(self, executable: str, admission: GenerationAdmission) -> str:
        if not admission.verified or not all(
            (
                admission.version,
                admission.executable_sha256,
                admission.recipe_sha256,
                admission.catalog_sha256,
                admission.parser_sha256,
                admission.evidence_sha256,
            )
        ):
            raise BackendError("policy_unverified", "Generation requires its own verified binary and policy admission.")
        if admission != self.admission:
            raise BackendError("policy_unverified", "Generation admission changed while the request was queued.")
        try:
            resources_match = (
                generation_recipe_digest() == admission.recipe_sha256
                and admission.catalog_sha256 == CATALOG_SHA256
                and generation_catalog_digest() == admission.catalog_sha256
                and parser_contract_digest() == admission.parser_sha256
                and self._evidence_digest(admission) == admission.evidence_sha256
            )
        except OSError:
            raise BackendError("policy_unverified", "A generation policy resource is unavailable.") from None
        if not resources_match:
            raise BackendError("policy_unverified", "Generation resources differ from their verified policy bindings.")
        try:
            observed = executable_digest(executable)
        except OSError:
            raise BackendError("cli_missing", "The admitted generation executable is unavailable.") from None
        if observed != admission.executable_sha256:
            raise BackendError("policy_unverified", "The generation executable differs from its verified binary.")
        return observed

    async def run(self, request: GenerationRequest) -> dict[str, Any]:
        """Return one terminal envelope; cancellation returns only after shared-pool cleanup."""
        identity = execution_identity(request)
        try:
            if os.environ.get("CLINK_DELEGATION_DEPTH", "0") != "0":
                raise BackendError("recursion_blocked", "Nested generation delegation is prohibited.")
            if time.monotonic() >= request.deadline:
                raise BackendError("timeout", "The generation deadline expired before launch.")
            content = compose_prompt(request)
            admission = self.admission
            identity.update(
                recipe_sha256=generation_recipe_digest(),
                catalog_sha256=CATALOG_SHA256,
                admission="unverified",
            )
            if request.cli != "codex" or request.model not in supported_generation_models():
                raise BackendError("unsupported_model", "The model is outside the generation catalog.")
            self._require_runtime_ceiling()
            # Reject before registry discovery or binary access when production admission is absent.
            if not admission.verified:
                raise BackendError(
                    "policy_unverified", "Generation requires its own verified binary and policy admission."
                )
            registry = self.registry if self.registry is not None else ClinkRegistry()
            try:
                client = registry.get_client("codex")
            except KeyError:
                raise BackendError("invalid_config", "No Codex generation client is configured.") from None
            if client.adapter != "codex" or len(client.executable) != 1 or client.config_args:
                raise BackendError(
                    "invalid_config", "Generation requires a Codex executable without operator arguments."
                )
            found = shutil.which(client.executable[0])
            if not found:
                raise BackendError("cli_missing", "The configured Codex executable is unavailable.")
            try:
                executable = str(Path(found).resolve(strict=True))
            except (OSError, RuntimeError):
                raise BackendError("cli_missing", "The configured Codex executable is unavailable.") from None
            cwd = Path(client.working_dir)
            roots = tuple(Path(root) for root in client.allowed_roots)
            environment = {name: os.environ[name] for name in ENVIRONMENT_KEYS if name in os.environ}
            if any(name not in ENVIRONMENT_KEYS for name in client.env):
                raise BackendError("invalid_config", "Generation environment additions exceed the verified allowlist.")
            environment.update(client.env)
            environment["CLINK_DELEGATION_DEPTH"] = "1"
            # Immutable values are captured once, before queuing; role/native/default arguments never enter argv.
            command = (executable, *generation_arguments(rendered=True), "--model", request.model, "-")

            def prelaunch() -> None:
                identity["admission"] = "unverified"
                self._require_runtime_ceiling()
                if _directory(cwd, roots) != cwd:
                    raise BackendError("path_not_allowed", "The configured generation directory changed while queued.")
                identity["executable_sha256"] = self._verify_bindings(executable, admission)
                identity["admission"] = "verified"

            prelaunch()
            if time.monotonic() >= request.deadline:
                raise BackendError("timeout", "The generation deadline expired during admission checks.")
            try:
                output = await self.pool.execute(
                    command,
                    content=content,
                    cwd=str(cwd),
                    env=environment,
                    deadline=request.deadline,
                    request_id=request.request_id,
                    metadata={"client": "codex", "tool": "generate"},
                    prelaunch=prelaunch,
                )
            except (BackendError, asyncio.CancelledError):
                raise
            except Exception:
                raise BackendError(
                    "cleanup_unconfirmed", "Process supervision failed and complete cleanup could not be confirmed."
                ) from None
            if output.returncode != 0:
                failure = cli_failure(output.stderr, code="nonzero_exit")
                metadata: dict[str, Any] = {}
                if failure.code == "rate_limited":
                    match = re.search(r"(?im)^retry-after:\s*([0-9]{1,6})\s*$", output.stderr)
                    if match and int(match.group(1)) <= 86400:
                        metadata["retry_after_seconds"] = int(match.group(1))
                raise BackendError(failure.code, "The generation CLI did not complete successfully.", metadata=metadata)
            parsed = CodexGenerationParser().parse(output.stdout, output.stderr)
            identity["actual_model"] = parsed.metadata.get("actual_model")
            if identity["actual_model"] is not None and identity["actual_model"] != request.model:
                raise BackendError("unsupported_model", "The CLI reported a different actual model.")
            if not parsed.content.strip():
                raise BackendError("protocol_error", "The generation CLI returned no nonempty final text.")
            raw_text = parsed.content.encode("utf-8")
            if len(raw_text) > OUTPUT_BYTES:
                prefix = raw_text[:OUTPUT_BYTES].decode("utf-8", errors="ignore")
                return terminal_result(
                    started_at=request.started_at,
                    request=request,
                    status="incomplete",
                    output=prefix,
                    truncated=True,
                    code="output_limit",
                    message="The generated text exceeded the 131072-byte output limit.",
                    execution=identity,
                )
            return terminal_result(
                started_at=request.started_at,
                request=request,
                status="completed",
                output=parsed.content,
                execution=identity,
            )
        except asyncio.CancelledError:
            return terminal_result(
                started_at=request.started_at,
                request=request,
                status="cancelled",
                code="cancelled",
                message="The generation attempt was cancelled after controlled cleanup.",
                execution=identity,
            )
        except (BackendError, ParserError) as exc:
            code = "protocol_error" if exc.code == "parse_error" else exc.code
            retry_after = exc.metadata.get("retry_after_seconds") if isinstance(exc, BackendError) else None
            return terminal_result(
                started_at=request.started_at,
                request=request,
                status="timed_out" if code == "timeout" else "cancelled" if code == "cancelled" else "failed",
                code=code,
                message=str(exc),
                retry_after_seconds=retry_after,
                execution=identity,
            )
        except RegistryLoadError:
            return terminal_result(
                started_at=request.started_at,
                request=request,
                code="invalid_config",
                message="Generation client configuration is invalid.",
                execution=identity,
            )
        except Exception:
            # Never reflect arbitrary configuration, streams, commands or request text from unexpected errors.
            return terminal_result(
                started_at=request.started_at,
                request=request,
                code="internal_error",
                message="The generation attempt failed internally.",
                execution=identity,
            )

    async def shutdown(self) -> None:
        await self.pool.shutdown()
