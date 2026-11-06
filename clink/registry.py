"""Atomically validate operator-owned CLI manifests without launching CLI processes."""

from __future__ import annotations

import json
import logging
import os
import shlex
import stat
from collections.abc import Iterable
from dataclasses import dataclass
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
from clink.models import (
    CLIClientConfig,
    CLIRoleConfig,
    ConsumerCLIConfig,
    ResolvedCLIClient,
    ResolvedCLIRole,
    ResolvedGenerationNative,
    ResolvedNativeTask,
)
from clink.native_policy import NATIVE_ADAPTERS
from utils.env import get_env

logger = logging.getLogger("clink.registry")

CONFIG_ENV_VAR = "CLI_CLIENTS_CONFIG_PATH"
MAX_PROMPT_BYTES = 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
_INFERRED_ADAPTERS = frozenset({"codex", "claude", "gemini"})
_SAFE_EXTRA_ARGS = {"claude": frozenset({"--verbose"})}


def _reject_constant(_value: str) -> None:
    raise ValueError("non-JSON numeric constant")


class RegistryLoadError(RuntimeError):
    """Configuration failed validation; existing registry state is retained."""


@dataclass(frozen=True)
class ManifestSource:
    """Private provenance for the winning manifest in a validated snapshot."""

    path: Path
    content: bytes
    identity: tuple[int, ...]


class ClinkRegistry:
    """Resolve a strict version-one configuration snapshot before publishing it."""

    def __init__(
        self,
        *,
        project_dir: Path | None = None,
        consumer_config_dir: str | Path | None = None,
        _consumer_identity: tuple[int, int] | None = None,
    ) -> None:
        if project_dir is not None and consumer_config_dir is not None:
            raise RegistryLoadError("--consumer-config-dir and --project are mutually exclusive")
        self._consumer_config_dir = (
            self._canonical_consumer_directory(consumer_config_dir) if consumer_config_dir is not None else None
        )
        if _consumer_identity is not None and self._consumer_config_dir is None:
            raise RegistryLoadError("Consumer identity requires an existing consumer binding")
        self._consumer_identity = _consumer_identity
        if self._consumer_config_dir is not None and self._consumer_identity is None:
            self._consumer_identity = self._directory_identity(self._consumer_config_dir)
        self._startup_cwd = Path.cwd().resolve(strict=True)
        self._project_dir = (
            self._resolve_directory(str(project_dir), self._startup_cwd, "project_dir")
            if project_dir is not None
            else None
        )
        self._clients: dict[str, ResolvedCLIClient] = {}
        self._sources: dict[str, ManifestSource] = {}
        self._load()

    @property
    def project_dir(self) -> Path | None:
        """Return the canonical server binding captured before the first snapshot."""
        return self._project_dir

    @property
    def consumer_config_dir(self) -> Path | None:
        """Return the immutable consumer binding, independent of all ordinary sources."""
        return self._consumer_config_dir

    @property
    def consumer_config_identity(self) -> tuple[int, int] | None:
        """Keep the original directory identity when constructing replacement snapshots."""
        return self._consumer_identity

    @staticmethod
    def _canonical_consumer_directory(value: str | Path) -> Path:
        try:
            spelling = os.fspath(value)
            path = Path(spelling)
            if not path.is_absolute() or spelling != str(path.resolve(strict=True)) or not path.is_dir():
                raise ValueError
            return path
        except (OSError, RuntimeError, ValueError):
            raise RegistryLoadError("consumer_config_dir must be an absolute existing canonical directory") from None

    @staticmethod
    def _directory_identity(path: Path) -> tuple[int, int]:
        try:
            info = path.stat()
            return info.st_dev, info.st_ino
        except OSError:
            raise RegistryLoadError("Consumer configuration directory is unavailable") from None

    def _check_consumer_binding(self) -> None:
        if self._consumer_config_dir is not None:
            path = self._canonical_consumer_directory(self._consumer_config_dir)
            if self._directory_identity(path) != self._consumer_identity:
                raise RegistryLoadError("Consumer configuration directory changed after binding")

    def _load(self) -> None:
        self._check_consumer_binding()
        clients: dict[str, ResolvedCLIClient] = {}
        provenance: dict[str, ManifestSource] = {}
        for source, required, deprecated in self._config_sources():
            source_clients: dict[str, ResolvedCLIClient] = {}
            paths = tuple(self._source_files(source, required=required))
            if deprecated and paths:
                logger.warning(
                    "Legacy CLI configuration directory %s is deprecated; migrate to %s", source, USER_CONFIG_DIR
                )
            for config_path in paths:
                data, manifest_source = self._read_manifest(config_path)
                if "schema_version" not in data and self._consumer_config_dir is None:
                    logger.warning(
                        "Legacy CLI manifest %s has no schema_version; migrate to schema_version 1", config_path
                    )
                if self._consumer_config_dir is not None:
                    configuration_scope = "consumer"
                elif required:
                    configuration_scope = "explicit"
                elif deprecated:
                    configuration_scope = "legacy_user"
                elif source == CONFIG_DIR:
                    configuration_scope = "bundled"
                elif source == USER_CONFIG_DIR:
                    configuration_scope = "user"
                elif self._project_dir is not None and source == self._project_dir / ".clink" / "cli_clients":
                    configuration_scope = "project"
                else:
                    configuration_scope = "unknown"
                resolved = self.validate_manifest(
                    data, source_path=config_path, configuration_scope=configuration_scope
                )
                key = resolved.name.lower()
                if key in source_clients:
                    raise RegistryLoadError(
                        f"Duplicate CLI name '{resolved.name}' within configuration source {source}"
                    )
                source_clients[key] = resolved
                provenance[key] = manifest_source
            clients.update(source_clients)
        if not clients and self._consumer_config_dir is not None:
            raise RegistryLoadError(
                "No consumer CLI manifests configured; bootstrap an intended generation family first"
            )
        if not clients:
            raise RegistryLoadError(f"No CLI clients configured; add a manifest or set {CONFIG_ENV_VAR}")
        self._clients = clients
        self._sources = provenance

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

    def manifest_source(self, cli_name: str) -> ManifestSource:
        """Return enrollment provenance without expanding public discovery fields."""
        self.get_client(cli_name)
        return self._sources[cli_name.lower()]

    def _config_sources(self) -> Iterable[tuple[Path, bool, bool]]:
        if self._consumer_config_dir is not None:
            yield self._consumer_config_dir, True, False
            return
        yield CONFIG_DIR, False, False
        yield LEGACY_USER_CONFIG_DIR, False, True
        yield USER_CONFIG_DIR, False, False
        if self._project_dir is not None:
            yield self._project_dir / ".clink" / "cli_clients", False, False
        explicit = get_env(CONFIG_ENV_VAR)
        if explicit:
            yield self._resolve_path(explicit, self._startup_cwd), True, False

    def _iter_config_files(self) -> Iterable[Path]:
        """Retain the legacy inspection helper with version-one precedence."""
        for source, required, _deprecated in self._config_sources():
            yield from self._source_files(source, required=required)

    def _source_files(self, source: Path, *, required: bool) -> Iterable[Path]:
        try:
            if self._consumer_config_dir is not None:
                self._check_consumer_binding()
                for path in sorted(source.iterdir()):
                    if path.suffix.lower() == ".json":
                        if not stat.S_ISREG(path.lstat().st_mode):
                            raise RegistryLoadError(
                                f"Consumer configuration must be a confined regular JSON file: {path}"
                            )
                        yield path
                return
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

    def _read_manifest(self, source_path: Path) -> tuple[dict, ManifestSource]:
        try:
            flags = os.O_RDONLY
            if os.name == "posix":
                flags |= os.O_NOFOLLOW | os.O_NONBLOCK
            if self._consumer_config_dir is not None:
                self._check_consumer_binding()
                if source_path.parent != self._consumer_config_dir:
                    raise RegistryLoadError("Consumer manifest escaped the bound directory")
                if os.name != "posix":
                    raise RegistryLoadError("Consumer configuration requires POSIX no-follow file operations")
                parent_fd = os.open(self._consumer_config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    parent_info = os.fstat(parent_fd)
                    if (parent_info.st_dev, parent_info.st_ino) != self._consumer_identity:
                        raise RegistryLoadError("Consumer configuration directory changed while reading")
                    descriptor = os.open(source_path.name, flags, dir_fd=parent_fd)
                finally:
                    os.close(parent_fd)
            else:
                descriptor = os.open(source_path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise RegistryLoadError(f"Configuration is not a regular file: {source_path}")
                content = stream.read(MAX_CONFIG_BYTES + 1)
                after = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise RegistryLoadError(f"Configuration changed while reading: {source_path}")
            self._check_consumer_binding()
            if len(content) > MAX_CONFIG_BYTES:
                raise RegistryLoadError(f"Configuration file exceeds {MAX_CONFIG_BYTES} bytes: {source_path}")
            data = json.loads(
                content,
                object_pairs_hook=self._unique_json_object,
                parse_constant=_reject_constant,
            )
        except RegistryLoadError as exc:
            raise RegistryLoadError(f"{exc} ({source_path})") from None
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            # Do not include JSON source excerpts or arbitrary error values in diagnostics.
            raise RegistryLoadError(
                f"Cannot read valid JSON configuration: {source_path} ({type(exc).__name__})"
            ) from None
        if not isinstance(data, dict):
            raise RegistryLoadError(f"Configuration must be a JSON object: {source_path}")
        return data, ManifestSource(source_path, content, identity)

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
        value: dict = {}
        for key, item in pairs:
            if key in value:
                raise RegistryLoadError("Duplicate JSON field in CLI configuration")
            value[key] = item
        return value

    def validate_manifest(
        self, data: dict, *, source_path: Path, configuration_scope: str = "unknown"
    ) -> ResolvedCLIClient:
        """Validate both initial and enrollment candidate manifests under the bound schema."""
        try:
            config = (
                ConsumerCLIConfig.model_validate(data)
                if self._consumer_config_dir is not None
                else CLIClientConfig.model_validate(data)
            )
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'manifest'} ({error['type']})"
                for error in exc.errors(include_input=False)
            )
            raise RegistryLoadError(f"Invalid configuration in {source_path}: {details}") from None
        if isinstance(config, ConsumerCLIConfig):
            if source_path != self._consumer_config_dir / f"{config.name}.json":
                raise RegistryLoadError(f"Consumer manifest filename must match its CLI: {source_path}")
            try:
                command = shlex.split(config.command, posix=True)
            except ValueError:
                raise RegistryLoadError(f"Invalid consumer executable quoting in {source_path}") from None
            if (
                len(command) != 1
                or not Path(command[0]).is_absolute()
                or any(ord(char) < 32 or ord(char) == 127 for char in config.command)
            ):
                raise RegistryLoadError(f"Consumer command must name one quoted absolute executable in {source_path}")
            roots = []
            for root in config.allowed_roots:
                resolved = self._resolve_directory(root, source_path.parent, "allowed_roots")
                if root != str(resolved):
                    raise RegistryLoadError(
                        f"Consumer allowed_roots must be canonical absolute directories in {source_path}"
                    )
                roots.append(resolved)
            defaults = INTERNAL_DEFAULTS[config.adapter]
            return ResolvedCLIClient(
                name=config.name,
                adapter=config.adapter,
                executable=tuple(command),
                working_dir=None,
                allowed_roots=tuple(dict.fromkeys(roots)),
                allowed_roots_explicit=True,
                configuration_scope="consumer",
                timeout_seconds=defaults.timeout_seconds,
                parser=defaults.parser,
                runner=defaults.runner,
                roles={},
                generation_native=ResolvedGenerationNative(
                    inherit_env=tuple(config.generation_native.inherit_env),
                    required_env=tuple(config.generation_native.required_env),
                ),
            )
        return self._resolve_config(config, source_path=source_path, configuration_scope=configuration_scope)

    def _resolve_config(
        self, raw: CLIClientConfig, *, source_path: Path, configuration_scope: str = "unknown"
    ) -> ResolvedCLIClient:
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
            else self._project_dir or self._startup_cwd
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
            if adapter not in NATIVE_ADAPTERS:
                raise RegistryLoadError(f"This adapter has no native task profile contract in {source_path}")
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
        generation_native = None
        if raw.generation_native is not None:
            if adapter not in {"codex", "claude", "agy", "grok"}:
                raise RegistryLoadError(
                    f"Native generation supports only Codex, Claude, Antigravity and Grok adapters in {source_path}"
                )
            generation_native = ResolvedGenerationNative(
                inherit_env=tuple(raw.generation_native.inherit_env),
                required_env=tuple(raw.generation_native.required_env),
            )
        return ResolvedCLIClient(
            name=raw.name,
            adapter=adapter,
            executable=executable,
            working_dir=working_dir,
            allowed_roots=roots,
            allowed_roots_explicit=raw.allowed_roots is not None,
            configuration_scope=configuration_scope,
            internal_args=defaults.additional_args,
            config_args=tuple(raw.additional_args),
            env=env,
            timeout_seconds=raw.timeout_seconds or defaults.timeout_seconds,
            parser=defaults.parser,
            runner=defaults.runner,
            roles=self._resolve_roles(raw, defaults, source_path, adapter),
            default_model=raw.default_model,
            native_task=native_task,
            generation_native=generation_native,
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


def get_registry(*, project_dir: Path | None = None) -> ClinkRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ClinkRegistry(project_dir=project_dir)
    elif project_dir is not None:
        requested = _REGISTRY._resolve_directory(str(project_dir), Path.cwd(), "project_dir")
        if requested != _REGISTRY.project_dir:
            raise RegistryLoadError("CLI registry already has a different project binding; restart to change --project")
    return _REGISTRY
