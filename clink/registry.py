"""Atomically validate operator-owned CLI manifests without launching CLI processes."""

from __future__ import annotations

import json
import logging
import shlex
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from clink.constants import (
    CHILD_ENV_ALLOWLIST,
    CONFIG_DIR,
    INTERNAL_DEFAULTS,
    LEGACY_USER_CONFIG_DIR,
    USER_CONFIG_DIR,
    CLIInternalDefaults,
)
from clink.models import CLIClientConfig, CLIRoleConfig, ResolvedCLIClient, ResolvedCLIRole, ResolvedNativeTask
from utils.env import get_env

logger = logging.getLogger("clink.registry")

CONFIG_ENV_VAR = "CLI_CLIENTS_CONFIG_PATH"
MAX_PROMPT_BYTES = 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
_INFERRED_ADAPTERS = frozenset({"codex", "claude", "gemini"})
_SAFE_EXTRA_ARGS = {"claude": frozenset({"--verbose"})}


class RegistryLoadError(RuntimeError):
    """Configuration failed validation; existing registry state is retained."""


class ClinkRegistry:
    """Resolve a strict version-one configuration snapshot before publishing it."""

    def __init__(self) -> None:
        self._startup_cwd = Path.cwd().resolve(strict=True)
        self._clients: dict[str, ResolvedCLIClient] = {}
        self._load()

    def _load(self) -> None:
        clients: dict[str, ResolvedCLIClient] = {}
        for source, required, deprecated in self._config_sources():
            source_clients: dict[str, ResolvedCLIClient] = {}
            paths = tuple(self._source_files(source, required=required))
            if deprecated and paths:
                logger.warning(
                    "Legacy CLI configuration directory %s is deprecated; migrate to %s", source, USER_CONFIG_DIR
                )
            for config_path in paths:
                data = self._read_manifest(config_path)
                if "schema_version" not in data:
                    logger.warning(
                        "Legacy CLI manifest %s has no schema_version; migrate to schema_version 1", config_path
                    )
                try:
                    config = CLIClientConfig.model_validate(data)
                except ValidationError as exc:
                    # Pydantic's normal message can expose credential values supplied in invalid fields.
                    details = "; ".join(
                        f"{'.'.join(str(part) for part in error['loc']) or 'manifest'} ({error['type']})"
                        for error in exc.errors(include_input=False)
                    )
                    raise RegistryLoadError(f"Invalid configuration in {config_path}: {details}") from None
                resolved = self._resolve_config(config, source_path=config_path)
                key = resolved.name.lower()
                if key in source_clients:
                    raise RegistryLoadError(
                        f"Duplicate CLI name '{resolved.name}' within configuration source {source}"
                    )
                source_clients[key] = resolved
            clients.update(source_clients)
        if not clients:
            raise RegistryLoadError(f"No CLI clients configured; add a manifest or set {CONFIG_ENV_VAR}")
        self._clients = clients

    def reload(self) -> None:
        """Validate a complete replacement snapshot; ordinary v1 operation reloads on restart."""
        self._load()

    def list_clients(self) -> list[str]:
        return sorted(client.name for client in self._clients.values())

    def list_roles(self, cli_name: str) -> list[str]:
        return sorted(self.get_client(cli_name).roles)

    def get_client(self, cli_name: str) -> ResolvedCLIClient:
        key = cli_name.lower()
        if key not in self._clients:
            available = ", ".join(self.list_clients())
            raise KeyError(f"CLI '{cli_name}' is not configured. Available clients: {available}")
        return self._clients[key]

    def _config_sources(self) -> Iterable[tuple[Path, bool, bool]]:
        yield CONFIG_DIR, False, False
        yield LEGACY_USER_CONFIG_DIR, False, True
        yield USER_CONFIG_DIR, False, False
        explicit = get_env(CONFIG_ENV_VAR)
        if explicit:
            yield self._resolve_path(explicit, self._startup_cwd), True, False

    def _iter_config_files(self) -> Iterable[Path]:
        """Retain the legacy inspection helper with version-one precedence."""
        for source, required, _deprecated in self._config_sources():
            yield from self._source_files(source, required=required)

    def _source_files(self, source: Path, *, required: bool) -> Iterable[Path]:
        try:
            if source.is_file():
                if source.suffix.lower() != ".json":
                    raise RegistryLoadError(f"Configuration source must be a JSON file or directory: {source}")
                yield source.resolve(strict=True)
            elif source.is_dir():
                for path in sorted(source.iterdir()):
                    if path.suffix.lower() == ".json" and path.is_file():
                        yield path.resolve(strict=True)
            elif required or source.exists():
                raise RegistryLoadError(f"Configuration source does not exist or is not a file/directory: {source}")
        except OSError as exc:
            raise RegistryLoadError(f"Cannot read configuration source {source}: {exc.strerror}") from None

    def _read_manifest(self, source_path: Path) -> dict:
        try:
            with source_path.open("rb") as stream:
                content = stream.read(MAX_CONFIG_BYTES + 1)
            if len(content) > MAX_CONFIG_BYTES:
                raise RegistryLoadError(f"Configuration file exceeds {MAX_CONFIG_BYTES} bytes: {source_path}")
            data = json.loads(content, object_pairs_hook=self._unique_json_object)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            # Do not include JSON source excerpts or arbitrary error values in diagnostics.
            raise RegistryLoadError(
                f"Cannot read valid JSON configuration: {source_path} ({type(exc).__name__})"
            ) from None
        if not isinstance(data, dict):
            raise RegistryLoadError(f"Configuration must be a JSON object: {source_path}")
        return data

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
        value: dict = {}
        for key, item in pairs:
            if key in value:
                raise RegistryLoadError("Duplicate JSON field in CLI configuration")
            value[key] = item
        return value

    def _resolve_config(self, raw: CLIClientConfig, *, source_path: Path) -> ResolvedCLIClient:
        adapter = raw.adapter
        if adapter is None and raw.name.lower() in _INFERRED_ADAPTERS:
            adapter = raw.name.lower()
        if adapter not in INTERNAL_DEFAULTS:
            raise RegistryLoadError(f"CLI '{raw.name}' requires a registered adapter in {source_path}")
        defaults = INTERNAL_DEFAULTS[adapter]
        executable = self._resolve_executable(raw, adapter, source_path)
        self._validate_args(raw.additional_args, adapter, "additional_args", source_path)
        env = self._merge_env(raw, defaults)
        working_dir = (
            self._resolve_directory(raw.working_dir, source_path.parent, "working_dir")
            if raw.working_dir is not None
            else self._startup_cwd
        )
        if raw.allowed_roots is None:
            roots = (working_dir,)
        else:
            if not raw.allowed_roots:
                raise RegistryLoadError(f"allowed_roots must contain at least one directory in {source_path}")
            roots = tuple(
                dict.fromkeys(
                    self._resolve_directory(root, source_path.parent, "allowed_roots") for root in raw.allowed_roots
                )
            )
        if not any(working_dir == root or root in working_dir.parents for root in roots):
            raise RegistryLoadError(f"working_dir is outside allowed_roots in {source_path}")
        native_task = None
        if raw.native_task is not None:
            if adapter != "claude":
                raise RegistryLoadError(f"Native task profiles currently require the claude adapter in {source_path}")
            native_roles = raw.native_task.roles
            if raw.native_task.enabled and "default" not in native_roles:
                raise RegistryLoadError(f"An enabled native task profile requires a default role in {source_path}")
            native_task = ResolvedNativeTask(
                enabled=raw.native_task.enabled,
                inherit_env=tuple(raw.native_task.inherit_env),
                required_env=tuple(raw.native_task.required_env),
                roles=self._resolve_role_mapping(native_roles, defaults, source_path, adapter, native=True),
                default_model=raw.native_task.default_model,
            )
        return ResolvedCLIClient(
            name=raw.name,
            adapter=adapter,
            executable=executable,
            working_dir=working_dir,
            allowed_roots=roots,
            internal_args=defaults.additional_args,
            config_args=tuple(raw.additional_args),
            env=env,
            timeout_seconds=raw.timeout_seconds or defaults.timeout_seconds,
            parser=defaults.parser,
            runner=defaults.runner,
            roles=self._resolve_roles(raw, defaults, source_path, adapter),
            default_model=raw.default_model,
            native_task=native_task,
        )

    def _resolve_executable(self, raw: CLIClientConfig, adapter: str, source_path: Path) -> tuple[str, ...]:
        if not raw.command or not raw.command.strip() or "\x00" in raw.command:
            raise RegistryLoadError(f"CLI '{raw.name}' must specify a nonempty command in {source_path}")
        try:
            command = shlex.split(raw.command, posix=True)
        except ValueError:
            raise RegistryLoadError(f"Invalid command quoting in {source_path}") from None
        if not command or not command[0]:
            raise RegistryLoadError(f"CLI '{raw.name}' must specify a nonempty command in {source_path}")
        self._validate_args(command[1:], adapter, "command arguments", source_path)
        executable = command[0]
        if "/" in executable or executable.startswith((".", "~")):
            executable = str(self._resolve_path(executable, source_path.parent))
        return (executable, *command[1:])

    @staticmethod
    def _validate_args(arguments: Iterable[str], adapter: str, label: str, source_path: Path) -> None:
        allowed = _SAFE_EXTRA_ARGS.get(adapter, frozenset())
        if any(argument not in allowed for argument in arguments):
            raise RegistryLoadError(f"Unsafe or unsupported {label} in {source_path}; use structured model fields")

    @staticmethod
    def _merge_env(raw: CLIClientConfig, defaults: CLIInternalDefaults) -> dict[str, str]:
        merged = dict(defaults.env)
        merged.update(raw.env)
        for key, value in merged.items():
            if key not in CHILD_ENV_ALLOWLIST:
                raise RegistryLoadError(f"Environment key '{key}' is not permitted for CLI '{raw.name}'")
            if "\x00" in value:
                raise RegistryLoadError(f"Environment key '{key}' contains an invalid value for CLI '{raw.name}'")
        return merged

    def _resolve_roles(
        self, raw: CLIClientConfig, defaults: CLIInternalDefaults, source_path: Path, adapter: str
    ) -> dict[str, ResolvedCLIRole]:
        roles = dict(raw.roles)
        roles.setdefault("default", CLIRoleConfig())
        return self._resolve_role_mapping(roles, defaults, source_path, adapter)

    def _resolve_role_mapping(
        self,
        roles: dict[str, CLIRoleConfig],
        defaults: CLIInternalDefaults,
        source_path: Path,
        adapter: str,
        *,
        native: bool = False,
    ) -> dict[str, ResolvedCLIRole]:
        resolved: dict[str, ResolvedCLIRole] = {}
        for name, config in roles.items():
            if native and (config.role_args or config.prompt_path is None):
                raise RegistryLoadError(f"Native roles require prompt_path and no role_args in {source_path}")
            self._validate_args(config.role_args, adapter, f"role_args for role '{name}'", source_path)
            candidate = config.prompt_path if config.prompt_path is not None else defaults.default_role_prompt
            if not candidate:
                raise RegistryLoadError(f"Role '{name}' requires prompt_path in {source_path}")
            prompt_path = self._resolve_path(candidate, source_path.parent)
            try:
                if not prompt_path.is_file():
                    raise RegistryLoadError(f"prompt_path is not an existing regular file: {prompt_path}")
                with prompt_path.open("rb") as stream:
                    content = stream.read(MAX_PROMPT_BYTES + 1)
                if len(content) > MAX_PROMPT_BYTES:
                    raise RegistryLoadError(f"prompt_path exceeds {MAX_PROMPT_BYTES} bytes: {prompt_path}")
                prompt_text = content.decode("utf-8")
            except (OSError, UnicodeError, ValueError) as exc:
                raise RegistryLoadError(f"Cannot read UTF-8 prompt_path {prompt_path} ({type(exc).__name__})") from None
            resolved[name] = ResolvedCLIRole(
                name=name,
                prompt_path=prompt_path,
                prompt_text=prompt_text,
                role_args=tuple(config.role_args),
                description=config.description,
                model=config.model,
            )
        return resolved

    def _resolve_directory(self, candidate: str, base_dir: Path, label: str) -> Path:
        if not candidate:
            raise RegistryLoadError(f"{label} must be a nonempty directory path")
        try:
            path = self._resolve_path(candidate, base_dir).resolve(strict=True)
            if not path.is_dir():
                raise RegistryLoadError(f"{label} is not a directory: {path}")
            return path
        except (OSError, ValueError, RuntimeError):
            raise RegistryLoadError(f"{label} must be an existing real directory") from None

    @staticmethod
    def _resolve_path(candidate: str, base_dir: Path) -> Path:
        try:
            path = Path(candidate).expanduser()
            return (path if path.is_absolute() else base_dir / path).resolve()
        except (OSError, ValueError, RuntimeError):
            raise RegistryLoadError("Invalid configuration path") from None


_REGISTRY: ClinkRegistry | None = None


def get_registry() -> ClinkRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ClinkRegistry()
    return _REGISTRY
