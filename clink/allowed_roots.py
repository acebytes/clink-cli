"""CLI-owned, explicit enrollment of canonical directories in operator manifests."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import clink.registry as registry_module
from clink.registry import ClinkRegistry, ManifestSource, RegistryLoadError
from clink.setup import (
    SetupError,
    _check_parent,
    _directory,
    _identity,
    _json_object,
    _PlannedFile,
    _publish,
    _read_destination,
)


class EnrollmentError(RuntimeError):
    """An enrollment failed; publication state is structural, never parsed from prose."""

    def __init__(
        self, message: str, *, grant_saved: bool | None = False, error_code: str = "enrollment_failed"
    ) -> None:
        super().__init__(message)
        self.grant_saved = grant_saved
        self.error_code = error_code


def canonical_directory(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            raise OSError
        return path
    except (OSError, RuntimeError, ValueError):
        raise EnrollmentError(f"Choose an existing accessible directory: {json.dumps(value)}") from None


def allow_command(
    target: str, cli_name: str, project: Path | None = None, consumer_config_dir: str | Path | None = None
) -> str:
    arguments = ["clink"]
    if consumer_config_dir is not None:
        arguments.extend(("--consumer-config-dir", str(consumer_config_dir)))
    arguments.extend(("allow", target, "--cli", cli_name))
    if project is not None:
        arguments.extend(("--project", str(project)))
    return shlex.join([*arguments, "--yes"])


def approved(registry: ClinkRegistry, cli_name: str, target: Path, *, native: bool = False) -> bool:
    client = registry.get_client(cli_name)
    eligible = (
        {"user", "legacy_user", "explicit", "consumer"} if native else {"user", "legacy_user", "explicit", "project"}
    )
    if not client.allowed_roots_explicit or client.configuration_scope not in eligible:
        return False
    for root in client.allowed_roots:
        if canonical_directory(str(root)) != root:
            raise EnrollmentError("An approved directory changed; review the operator manifest before proceeding.")
    return any(target == root or root in target.parents for root in client.allowed_roots)


def _set_manifest_field(content: bytes, field: str, new_value: str | list[str], path: Path) -> bytes:
    """Replace only the top-level value span, or insert without rewriting existing bytes."""
    _json_object(content, path)
    text = content.decode("utf-8")
    decoder = json.JSONDecoder()
    index = text.index("{") + 1
    last_end = index
    indent = "  "
    while True:
        index += len(text[index:]) - len(text[index:].lstrip())
        if text[index] == "}":
            addition = json.dumps(field) + ": " + json.dumps(new_value, ensure_ascii=False)
            prefix = "," if last_end != text.index("{") + 1 else ""
            separator = ("\r\n" if "\r\n" in text else "\n") + indent if "\n" in text else " "
            return (text[:last_end] + prefix + separator + addition + text[last_end:]).encode("utf-8")
        line_start = text.rfind("\n", 0, index) + 1
        if line_start and not text[line_start:index].strip():
            indent = text[line_start:index]
        key, end = decoder.raw_decode(text, index)
        index = end
        index += len(text[index:]) - len(text[index:].lstrip())
        index += 1  # Strict object validation above established the colon.
        index += len(text[index:]) - len(text[index:].lstrip())
        previous, end = decoder.raw_decode(text, index)
        if key == field:
            value = json.dumps(new_value, ensure_ascii=False)
            if isinstance(new_value, list) and isinstance(previous, list) and new_value[: len(previous)] == previous:
                value = text[index:end]
                for root in new_value[len(previous) :]:
                    entry = json.dumps(root, ensure_ascii=False)
                    insert = len(value[:-1].rstrip())
                    if "\n" in value:
                        last_line = value[:insert].rsplit("\n", 1)[-1]
                        array_indent = re.match(r"[\t ]*", last_line).group()
                        separator = ("\r\n" if "\r\n" in value else "\n") + array_indent
                    else:
                        separator = " "
                    comma = "," if previous else ""
                    value = value[:insert] + comma + separator + entry + value[insert:]
                    previous.append(root)
            return (text[:index] + value + text[end:]).encode("utf-8")
        last_end = end
        index = end + len(text[end:]) - len(text[end:].lstrip())
        if text[index] == ",":
            index += 1


def _portable(registry: ClinkRegistry, cli_name: str, source: ManifestSource, target: Path) -> dict[str, Any]:
    data = _json_object(source.content, source.path)
    client = registry.get_client(cli_name)
    # A bundled runtime default is not consent to persist that directory.
    working_dir = client.working_dir if data.get("working_dir") is not None else target
    data["working_dir"] = str(working_dir if working_dir.is_relative_to(target) else target)
    data["allowed_roots"] = [str(target)]
    data["command"] = shlex.join(client.executable)
    role_maps = [(data.setdefault("roles", {}), client.roles)]
    if client.native_task is not None:
        role_maps.append((data["native_task"].setdefault("roles", {}), client.native_task.roles))
    for raw_roles, resolved_roles in role_maps:
        for name, role in resolved_roles.items():
            raw_roles.setdefault(name, {})["prompt_path"] = str(role.prompt_path)
    return data


def _parent_identities(path: Path) -> tuple[tuple[Path, int, int], ...]:
    identities = []
    for parent in reversed((path, *path.parents)):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            break
        if not stat.S_ISDIR(info.st_mode):
            raise EnrollmentError(f"Configuration parent must be a real directory: {parent}")
        identities.append((parent, info.st_dev, info.st_ino))
    return tuple(identities)


@dataclass(frozen=True)
class Enrollment:
    registry: ClinkRegistry
    cli_name: str
    target: Path
    target_spelling: str
    target_identity: tuple[int, int]
    source: ManifestSource
    destination: Path
    content: bytes
    previous: bytes | None
    identity: tuple[int, ...] | None
    mode: int
    already_allowed: bool
    native: bool
    lock_root: Path
    parents: tuple[tuple[Path, int, int], ...]

    @property
    def entry(self) -> str:
        return json.dumps(str(self.target), ensure_ascii=False)

    @property
    def broad(self) -> bool:
        return self.target in {Path(self.target.anchor), Path.home().resolve()}

    def _registry_snapshot(self) -> ClinkRegistry:
        return ClinkRegistry(
            project_dir=self.registry.project_dir,
            consumer_config_dir=self.registry.consumer_config_dir,
            _consumer_identity=self.registry.consumer_config_identity,
        )

    def _validate(self) -> None:
        target = canonical_directory(self.target_spelling)
        info = target.stat()
        if target != self.target or (info.st_dev, info.st_ino) != self.target_identity:
            raise EnrollmentError("Selected directory changed during approval.")
        for parent, device, inode in self.parents:
            current_parent = parent.lstat()
            if not stat.S_ISDIR(current_parent.st_mode) or (current_parent.st_dev, current_parent.st_ino) != (
                device,
                inode,
            ):
                raise EnrollmentError("Configuration parent changed during approval.")
        current = self._registry_snapshot()
        if current.manifest_source(self.cli_name) != self.source or current.get_client(
            self.cli_name
        ) != self.registry.get_client(self.cli_name):
            raise EnrollmentError("Effective configuration changed during approval.")

    def persist(
        self, *, guard: Callable[[], None] | None = None, published: Callable[[], None] | None = None
    ) -> ClinkRegistry:
        if self.already_allowed:
            return self.registry
        saved = False

        def mark_saved() -> None:
            nonlocal saved
            saved = True
            if published is not None:
                published()

        try:
            if os.name != "posix":
                raise EnrollmentError("Directory enrollment requires POSIX no-follow file operations.")
            import fcntl

            root = Path(self.destination.anchor)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                self._validate()
                parts = self.destination.relative_to(root).parts
                with _directory(descriptor, parts[:-1], root, create=True) as lock_fd:
                    assert lock_fd is not None
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise EnrollmentError(
                            "Another directory enrollment is running; review before retrying."
                        ) from None
                    self._validate()

                    def prepublish() -> None:
                        self._validate()
                        _check_parent(descriptor, lock_fd, root, parts[:-1])
                        if guard is not None:
                            guard()

                    planned = _PlannedFile(
                        parts,
                        self.content,
                        self.previous,
                        self.identity,
                        self.mode,
                    )
                    _publish(descriptor, root, planned, prepublish=prepublish, published=mark_saved)
            finally:
                os.close(descriptor)
            refreshed = self._registry_snapshot()
            if not approved(refreshed, self.cli_name, self.target, native=self.native):
                raise EnrollmentError("Saved configuration did not establish the requested authority.")
            return refreshed
        except (EnrollmentError, RegistryLoadError, SetupError, OSError, ValueError) as exc:
            state = (
                "Grant saved, but enrollment did not finish; inspect it before retrying" if saved else "No grant saved"
            )
            raise EnrollmentError(
                f"{state}: configuration {self.destination}; allowed_roots entry {self.entry} "
                f"({type(exc).__name__}: {exc}).",
                grant_saved=saved,
                error_code=exc.error_code if isinstance(exc, EnrollmentError) else "enrollment_failed",
            ) from None
        except (asyncio.CancelledError, KeyboardInterrupt) as exc:
            # Keep cancellation semantics while preserving the publication fact for callers.
            exc.grant_saved = saved
            raise


def prepare_enrollment(
    registry: ClinkRegistry,
    cli_name: str,
    value: str,
    *,
    native: bool = False,
) -> Enrollment:
    """Prepare a bounded immutable change without creating directories or writing files."""
    target = canonical_directory(value)
    destination: Path | str = "selected operator manifest"
    try:
        if os.name != "posix":
            raise EnrollmentError("Directory enrollment requires POSIX no-follow file operations.")
        client = registry.get_client(cli_name)
        source = registry.manifest_source(cli_name)
        if (
            registry.consumer_config_dir is None
            and client.configuration_scope != "bundled"
            and source.path.is_relative_to(registry_module.CONFIG_DIR.resolve())
        ):
            raise EnrollmentError(
                f"Refusing to edit a bundled manifest selected as operator configuration: {source.path}. "
                "Select an independent operator manifest with CLI_CLIENTS_CONFIG_PATH."
            )
        native = native or registry.consumer_config_dir is not None
        already = approved(registry, cli_name, target, native=native)
        if native and registry.project_dir is not None:
            raise EnrollmentError("Native generation requires independent user or explicit configuration.")
        destination = source.path
        portable = client.configuration_scope == "bundled"
        if portable:
            destination = registry_module.USER_CONFIG_DIR / f"{client.name.lower()}.json"
        destination = destination.absolute()
        lock_root = destination.parent
        root = Path(destination.anchor)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            previous, info = _read_destination(descriptor, destination.relative_to(root).parts, root)
        finally:
            os.close(descriptor)
        if portable and previous is not None:
            raise EnrollmentError("A user manifest already occupies the override destination.")
        if not portable and (previous != source.content or info is None or _identity(info) != source.identity):
            raise EnrollmentError("Selected configuration changed before approval.")
        if portable:
            data = _portable(registry, cli_name, source, target)
            content = (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        else:
            content = source.content
            data = _json_object(content, source.path)
            if data.get("allowed_roots") is None:
                if data.get("working_dir") is None:
                    content = _set_manifest_field(content, "working_dir", str(target), source.path)
                elif not client.working_dir.is_relative_to(target):
                    raise EnrollmentError(
                        f"Configured working_dir {str(client.working_dir)!r} lies outside the requested folder. "
                        "Set working_dir inside that folder, or explicitly approve the configured working_dir first."
                    )
            if not already:
                roots = [*(data.get("allowed_roots") or []), str(target)]
                content = _set_manifest_field(content, "allowed_roots", roots, source.path)
        if len(content) > registry_module.MAX_CONFIG_BYTES:
            raise EnrollmentError("Updated configuration exceeds the bounded file limit.")
        registry.validate_manifest(
            _json_object(content, destination),
            source_path=destination,
            configuration_scope=("user" if portable else client.configuration_scope),
        )
        info_target = target.stat()
        return Enrollment(
            registry,
            cli_name,
            target,
            value,
            (info_target.st_dev, info_target.st_ino),
            source,
            destination,
            content,
            previous,
            _identity(info) if info else None,
            stat.S_IMODE(info.st_mode) if info else 0o600,
            already,
            native,
            lock_root,
            _parent_identities(destination.parent),
        )
    except (EnrollmentError, RegistryLoadError, SetupError, OSError, KeyError, ValueError) as exc:
        raise EnrollmentError(
            f"No grant saved: configuration {destination}; allowed_roots entry {json.dumps(str(target))}: {exc}",
            grant_saved=False,
        ) from None
