"""Explicit native generation with separate admission and owned final-only publication."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import time
from contextlib import nullcontext
from pathlib import Path
from types import MappingProxyType
from typing import Any

from clink.contracts import BackendError
from clink.generation_contracts import OUTPUT_BYTES, PROMPT_BYTES, canonical_json, sha256
from clink.native_generation_claude_policy import (
    CLAUDE_GENERATION_PROMPT,
    DEFAULT_CLAUDE_NATIVE_ADMISSION,
    claude_generation_arguments,
    claude_generation_recipe_digest,
)
from clink.native_generation_contracts import (
    NativeGenerationRequest,
    _concrete_claude_model,
    execution_native_identity,
    terminal_native_result,
)
from clink.native_generation_files import prepare_native_final_output
from clink.native_generation_policy import (
    DEFAULT_NATIVE_ADMISSION,
    NATIVE_GENERATION_PROMPT,
    NativeGenerationAdmission,
    native_generation_arguments,
    native_generation_recipe_digest,
)
from clink.native_policy import native_environment_names
from clink.parsers.base import ParserError, cli_failure
from clink.parsers.claude_native_generation import ClaudeNativeGenerationParser
from clink.parsers.claude_native_generation import parser_contract_digest as claude_parser_contract_digest
from clink.parsers.codex_native_generation import CodexNativeGenerationParser, parser_contract_digest
from clink.policy import executable_digest
from clink.registry import ClinkRegistry, RegistryLoadError
from clink.runner import ProcessPool


def compose_native_prompt(request: NativeGenerationRequest) -> bytes:
    """Separate source and transformation text without changing native capability authority."""
    prompt = CLAUDE_GENERATION_PROMPT if request.cli == "claude" else NATIVE_GENERATION_PROMPT
    if request.cli in {"agy", "grok"}:
        from clink.native_generation_families import family_generation_prompt

        prompt = family_generation_prompt(request.cli)
    content = (
        prompt.encode("utf-8")
        + b"\n\n"
        + canonical_json({"instructions": request.instructions, "input": request.input})
    )
    if len(content) > PROMPT_BYTES:
        raise BackendError("input_limit", "The native generation prompt exceeds the 1 MiB byte limit.")
    return content


class NativeGenerationService:
    """One native exec invocation; completion is a validated response, never fulfillment proof."""

    def __init__(
        self,
        registry: ClinkRegistry | None = None,
        pool: ProcessPool | None = None,
        admission: NativeGenerationAdmission | None = None,
        *,
        claude_admission: NativeGenerationAdmission | None = None,
        family_admissions: dict[str, NativeGenerationAdmission] | None = None,
    ):
        self.registry = registry
        self.pool = pool if pool is not None else ProcessPool()
        self.admission = admission if admission is not None else DEFAULT_NATIVE_ADMISSION
        self.claude_admission = claude_admission if claude_admission is not None else DEFAULT_CLAUDE_NATIVE_ADMISSION
        if family_admissions is not None and set(family_admissions) - {"agy", "grok"}:
            raise ValueError("Only Grok and Antigravity generation candidates may be supplied.")
        self.family_admissions = MappingProxyType(dict(family_admissions or {}))

    def _admission_for(self, cli: str) -> NativeGenerationAdmission:
        if cli == "codex":
            return self.admission
        if cli == "claude":
            return self.claude_admission
        from clink.native_generation_families import DEFAULT_AGY_NATIVE_ADMISSION, DEFAULT_GROK_NATIVE_ADMISSION

        return self.family_admissions.get(
            cli, DEFAULT_AGY_NATIVE_ADMISSION if cli == "agy" else DEFAULT_GROK_NATIVE_ADMISSION
        )

    @staticmethod
    def _recipe_digest(cli: str) -> str:
        if cli == "codex":
            return native_generation_recipe_digest()
        if cli == "claude":
            return claude_generation_recipe_digest()
        from clink.native_generation_families import family_generation_recipe_digest

        return family_generation_recipe_digest(cli)

    @staticmethod
    def _parser_digest(cli: str) -> str:
        if cli == "codex":
            return parser_contract_digest()
        if cli == "claude":
            return claude_parser_contract_digest()
        from clink.native_generation_families import family_generation_parser_digest

        return family_generation_parser_digest(cli)

    def _verify_admission(self, executable: str, admission: NativeGenerationAdmission, *, cli: str = "codex") -> str:
        if not admission.verified or not all(
            (
                admission.version,
                admission.executable_sha256,
                admission.recipe_sha256,
                admission.parser_sha256,
                admission.evidence_path,
                admission.evidence_sha256,
            )
        ):
            raise BackendError(
                "policy_unverified", "Native generation requires separate binary, recipe and response admission."
            )
        if self._admission_for(cli) != admission:
            raise BackendError("policy_unverified", "Native generation admission changed while queued.")
        evidence_path = Path(admission.evidence_path)
        if not evidence_path.is_absolute():
            evidence_path = Path(__file__).resolve().parent.parent / evidence_path
        try:
            with evidence_path.open("rb") as stream:
                evidence = stream.read(1024 * 1024 + 1)
            if len(evidence) > 1024 * 1024 or sha256(evidence) != admission.evidence_sha256:
                raise BackendError("policy_unverified", "Native generation evidence differs from its pinned binding.")
            if (
                self._recipe_digest(cli) != admission.recipe_sha256
                or self._parser_digest(cli) != admission.parser_sha256
            ):
                raise BackendError("policy_unverified", "Native generation recipe or response validation changed.")
        except OSError:
            raise BackendError("policy_unverified", "A native generation admission resource is unavailable.") from None
        try:
            observed = executable_digest(executable)
        except OSError:
            raise BackendError("cli_missing", "The admitted native generation executable is unavailable.") from None
        if observed != admission.executable_sha256:
            raise BackendError("policy_unverified", "Native generation executable differs from its admitted binary.")
        return observed

    async def run(self, request: NativeGenerationRequest) -> dict[str, Any]:
        identity = execution_native_identity(request)
        warnings: list[dict[str, str]] = []

        def result(**kwargs: Any) -> dict[str, Any]:
            response = terminal_native_result(
                started_at=request.started_at, request=request, execution=identity, **kwargs
            )
            response["warnings"] = warnings[:8]
            return response

        try:
            explicit = request.cli in {"codex", "claude"}
            valid_selection = (request.schema_version == 2 and request.model_selection is None and explicit) or (
                request.schema_version == 3
                and request.model_selection is not None
                and (
                    (
                        explicit
                        and request.model_selection.kind == "explicit"
                        and request.model_selection.id == request.model
                    )
                    or (
                        request.cli in {"agy", "grok"}
                        and request.model_selection.kind == "native_default"
                        and request.model_selection.id is None
                        and request.model is None
                    )
                )
            )
            if (
                request.execution_mode != "native"
                or not valid_selection
                or (explicit and (not isinstance(request.model, str) or not request.model or request.model == "auto"))
                or (request.cli == "claude" and not _concrete_claude_model(request.model))
            ):
                raise BackendError(
                    "invalid_request",
                    "Native generation requires explicit native mode, supported CLI and concrete model selection.",
                )
            if os.environ.get("CLINK_DELEGATION_DEPTH", "0") != "0":
                raise BackendError("recursion_blocked", "Nested native generation delegation is prohibited.")
            if time.monotonic() >= request.deadline:
                raise BackendError("timeout", "The native generation deadline expired before launch.")
            content = compose_native_prompt(request)
            claude = request.cli == "claude"
            new_family = request.cli in {"agy", "grok"}
            admission = self._admission_for(request.cli)
            identity.update(
                recipe_sha256=self._recipe_digest(request.cli),
                admission="unverified",
            )
            if not admission.verified:
                raise BackendError(
                    "policy_unverified", "Native generation requires its own verified profile admission."
                )
            registry = self.registry if self.registry is not None else ClinkRegistry()
            if registry.project_dir is not None:
                raise BackendError(
                    "invalid_config",
                    "Programmatic native generation requires independent user or explicit configuration.",
                )
            try:
                client = registry.get_client(request.cli)
            except KeyError:
                raise BackendError(
                    "invalid_config", f"No {request.cli} native-generation client is configured."
                ) from None
            if client.adapter != request.cli or len(client.executable) != 1 or client.config_args:
                raise BackendError(
                    "invalid_config",
                    f"Native generation requires a {request.cli} executable without operator arguments.",
                )
            if not client.allowed_roots_explicit or client.configuration_scope not in {
                "user",
                "legacy_user",
                "explicit",
                "consumer",
            }:
                raise BackendError(
                    "path_not_allowed",
                    f"Native generation requires explicit allowed_roots in a user-scope {request.cli} "
                    "manifest or a manifest selected independently with CLI_CLIENTS_CONFIG_PATH.",
                )
            found = shutil.which(client.executable[0])
            if not found:
                raise BackendError("cli_missing", f"The configured {request.cli} executable is unavailable.")
            try:
                executable = str(Path(found).resolve(strict=True))
                cwd = Path(request.project_dir)
                if str(cwd.resolve(strict=True)) != request.project_dir or not cwd.is_dir():
                    raise OSError
                directory_identity = cwd.stat()
                # Registry roots are already canonical approval snapshots. Resolving
                # them again must never transfer approval to a replacement symlink.
                roots = tuple(Path(root) for root in client.allowed_roots)
                if any(root.resolve(strict=True) != root or not root.is_dir() for root in roots):
                    raise OSError
            except (OSError, RuntimeError):
                raise BackendError(
                    "path_not_allowed",
                    "The native-generation project or approved roots are unavailable or noncanonical. "
                    f"Check allowed_roots in the independent user-scope {request.cli} manifest or CLI_CLIENTS_CONFIG_PATH.",
                ) from None
            if not any(cwd == root or root in cwd.parents for root in roots):
                raise BackendError(
                    "path_not_allowed",
                    "The native-generation project is outside approved roots. Configure its "
                    f"allowed_roots in a user-scope {request.cli} manifest or independently via CLI_CLIENTS_CONFIG_PATH.",
                )
            profile = client.generation_native
            inherited = tuple(profile.inherit_env) if profile is not None else ()
            required = tuple(profile.required_env) if profile is not None else ()
            baseline = native_environment_names(request.cli)
            names = frozenset((*baseline, *inherited))
            environment = {name: value for name, value in os.environ.items() if name in names}
            if any(name not in baseline for name in client.env):
                raise BackendError(
                    "invalid_config", "Native-generation static environment additions exceed baseline names."
                )
            environment.update(client.env)
            environment["CLINK_DELEGATION_DEPTH"] = "1"
            if any(not environment.get(name) for name in required):
                raise BackendError(
                    "missing_environment", "Required native-generation integration environment names are unavailable."
                )

            from clink.native_generation_families import prepare_family_generation_launch

            with (
                prepare_native_final_output() if request.cli == "codex" else nullcontext() as final_output,
                (
                    prepare_family_generation_launch(request.cli, (executable,), content)
                    if new_family
                    else nullcontext()
                ) as family_launch,
            ):
                command = (
                    family_launch.command
                    if family_launch is not None
                    else (
                        (executable, *claude_generation_arguments(), "--model", request.model)
                        if claude
                        else (
                            executable,
                            *native_generation_arguments(str(final_output.path)),
                            "--cd",
                            request.project_dir,
                            "--model",
                            request.model,
                            "-",
                        )
                    )
                )

                def prelaunch() -> None:
                    identity["admission"] = "unverified"
                    try:
                        current = cwd.lstat()
                        if (
                            not stat.S_ISDIR(current.st_mode)
                            or str(cwd.resolve(strict=True)) != request.project_dir
                            or (current.st_dev, current.st_ino)
                            != (directory_identity.st_dev, directory_identity.st_ino)
                            or any(root.resolve(strict=True) != root or not root.is_dir() for root in roots)
                        ):
                            raise OSError
                    except (OSError, RuntimeError):
                        raise BackendError(
                            "path_not_allowed", "The native-generation project or approved roots changed while queued."
                        ) from None
                    if final_output is not None:
                        final_output.validate_prelaunch()
                    if family_launch is not None:
                        family_launch.validate_input()
                    identity["executable_sha256"] = self._verify_admission(executable, admission, cli=request.cli)
                    identity["admission"] = "verified"

                def before_spawn() -> None:
                    prelaunch()
                    # Set before process creation: launch can become ambiguous during cancellation.
                    identity["side_effects_possible"] = True

                prelaunch()
                if time.monotonic() >= request.deadline:
                    raise BackendError("timeout", "The native generation deadline expired during admission checks.")
                try:
                    output = await self.pool.execute(
                        command,
                        content=family_launch.stdin if family_launch is not None else content,
                        cwd=str(cwd),
                        env=environment,
                        deadline=request.deadline,
                        request_id=request.request_id,
                        metadata={"client": request.cli, "tool": "generate"},
                        prelaunch=before_spawn,
                    )
                except (BackendError, asyncio.CancelledError):
                    raise
                except Exception:
                    raise BackendError(
                        "cleanup_unconfirmed", "Native process supervision failed and cleanup could not be confirmed."
                    ) from None
                try:
                    if new_family:
                        from clink.native_generation_families import family_generation_parser

                        parser = family_generation_parser(request.cli)
                    else:
                        parser = ClaudeNativeGenerationParser() if claude else CodexNativeGenerationParser()
                    parsed = parser.parse(output.stdout, output.stderr)
                except ParserError as exc:
                    if output.returncode != 0 and exc.code == "protocol_error":
                        classified = cli_failure(output.stderr, code="nonzero_exit")
                        raise BackendError(
                            classified.code, "The native generation CLI did not complete successfully."
                        ) from None
                    raise
                if output.returncode != 0:
                    classified = cli_failure(output.stderr, code="nonzero_exit")
                    raise BackendError(classified.code, "The native generation CLI did not complete successfully.")
                identity["actual_model"] = parsed.metadata.get("actual_model")
                if explicit and identity["actual_model"] is not None and identity["actual_model"] != request.model:
                    raise BackendError("unsupported_model", "The native CLI reported a different actual model.")
                tool_errors = parsed.metadata.get("tool_error_count", 0)
                if type(tool_errors) is int and tool_errors > 0:
                    warnings.append(
                        {
                            "code": "native_tool_errors",
                            "message": f"Native completion included {tool_errors} recoverable tool or item errors.",
                        }
                    )
                text = final_output.read() if final_output is not None else parsed.content
                encoded = text.encode("utf-8")
                if len(encoded) > OUTPUT_BYTES:
                    completion = {
                        "status": "incomplete",
                        "output": encoded[:OUTPUT_BYTES].decode("utf-8", errors="ignore"),
                        "truncated": True,
                        "code": "output_limit",
                        "message": "The native final text exceeded the 131072-byte output limit.",
                    }
                else:
                    completion = {"status": "completed", "output": text}
            # Include owned private-file cleanup in the terminal elapsed duration.
            return result(**completion)
        except asyncio.CancelledError:
            return result(
                status="cancelled",
                code="cancelled",
                message="Native generation was cancelled after controlled cleanup.",
            )
        except (BackendError, ParserError) as exc:
            code = "protocol_error" if exc.code == "parse_error" else exc.code
            retry_after = exc.metadata.get("retry_after_seconds") if isinstance(exc, BackendError) else None
            return result(
                status="timed_out" if code == "timeout" else "cancelled" if code == "cancelled" else "failed",
                code=code,
                message=str(exc),
                retry_after_seconds=retry_after,
            )
        except RegistryLoadError:
            return result(code="invalid_config", message="Native-generation client configuration is invalid.")
        except Exception:
            return result(code="internal_error", message="Native generation failed internally.")

    async def shutdown(self) -> None:
        await self.pool.shutdown()
