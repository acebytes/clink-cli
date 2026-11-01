"""Create a project-local Clink configuration from bundled templates only."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from clink.constants import BUILTIN_PROMPTS_DIR, CONFIG_DIR, INTERNAL_DEFAULTS, PROJECT_ROOT
from clink.models import CLIClientConfig

MAX_SETUP_FILE_BYTES = 1024 * 1024


class SetupError(RuntimeError):
    """Setup could not proceed without changing operator-owned content."""


@dataclass(frozen=True)
class SetupResult:
    project_dir: Path
    created: tuple[Path, ...]
    unchanged: tuple[Path, ...]


@dataclass(frozen=True)
class _PlannedFile:
    parts: tuple[str, ...]
    content: bytes
    previous: bytes | None
    identity: tuple[int, ...] | None
    mode: int = 0o600


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-JSON numeric constant")


def _json_object(content: bytes, path: Path) -> dict[str, Any]:
    try:
        data = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise SetupError(f"Expected valid JSON without duplicate fields: {path}") from None
    if not isinstance(data, dict):
        raise SetupError(f"Expected a JSON object: {path}")
    return data


def _bounded_read(stream: Any, path: Path) -> bytes:
    content = stream.read(MAX_SETUP_FILE_BYTES + 1)
    if len(content) > MAX_SETUP_FILE_BYTES:
        raise SetupError(f"File exceeds {MAX_SETUP_FILE_BYTES} bytes: {path}")
    return content


def _bundled_content(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SetupError(f"Expected a bundled regular file: {path}")
    with path.open("rb") as stream:
        return _bounded_read(stream, path)


def _template_files(cli_names: Sequence[str] | None) -> dict[tuple[str, ...], bytes]:
    templates = {path.stem: path for path in sorted(CONFIG_DIR.glob("*.json"))}
    selected = sorted(set(cli_names)) if cli_names else sorted(templates)
    if not selected:
        raise SetupError(f"No bundled CLI templates found: {CONFIG_DIR}")
    for name in selected:
        if name not in templates:
            raise SetupError(f"Unknown bundled CLI {name!r}; choose from {', '.join(sorted(templates))}")
    contents: dict[tuple[str, ...], bytes] = {}
    prompt_root = BUILTIN_PROMPTS_DIR.resolve(strict=True)
    for name in selected:
        source = templates[name]
        data = _json_object(_bundled_content(source), source)
        try:
            raw = CLIClientConfig.model_validate(data)
        except ValidationError:
            raise SetupError(f"Invalid bundled CLI template: {source}") from None
        if raw.name != name or raw.adapter not in INTERNAL_DEFAULTS or "default" not in raw.roles:
            raise SetupError(f"Incomplete bundled CLI template: {source}")
        data["working_dir"] = "../.."
        data["allowed_roots"] = ["../.."]
        role_maps = [data["roles"]]
        if data.get("native_task") is not None:
            role_maps.append(data["native_task"]["roles"])
        for roles in role_maps:
            for role in roles.values():
                candidate = role.get("prompt_path")
                if not isinstance(candidate, str):
                    raise SetupError(f"Bundled role requires a prompt_path: {source}")
                prompt = (source.parent / candidate).resolve(strict=True)
                if not prompt.is_relative_to(prompt_root):
                    raise SetupError(f"Bundled role prompt is outside the bundled prompt directory: {source}")
                relative = prompt.relative_to(prompt_root)
                prompt_content = _bundled_content(prompt)
                try:
                    prompt_content.decode("utf-8")
                except UnicodeError:
                    raise SetupError(f"Expected a UTF-8 bundled role prompt: {prompt}") from None
                contents[(".clink", "prompts", *relative.parts)] = prompt_content
                role["prompt_path"] = f"../prompts/{relative.as_posix()}"
        contents[(".clink", "cli_clients", f"{name}.json")] = _json_bytes(data)
    _validate_templates(contents)
    return contents


def _validate_templates(contents: dict[tuple[str, ...], bytes]) -> None:
    """Validate generated schema and relative paths without loading user settings or .env."""
    with tempfile.TemporaryDirectory(prefix="clink-setup-validate-") as directory:
        root = Path(directory).resolve(strict=True)
        for parts, content in contents.items():
            path = root.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for path in sorted((root / ".clink" / "cli_clients").glob("*.json")):
            try:
                client = CLIClientConfig.model_validate(_json_object(path.read_bytes(), path))
            except ValidationError:
                raise SetupError(f"Generated CLI template failed validation: {path.name}") from None
            if (path.parent / client.working_dir).resolve(strict=True) != root:
                raise SetupError(f"Generated working_dir is outside the project: {path.name}")
            if client.allowed_roots != ["../.."]:
                raise SetupError(f"Generated allowed_roots are invalid: {path.name}")
            role_maps = [client.roles]
            if client.native_task is not None:
                role_maps.append(client.native_task.roles)
            for roles in role_maps:
                for role in roles.values():
                    if role.prompt_path is None:
                        raise SetupError(f"Generated role has no prompt_path: {path.name}")
                    prompt = (path.parent / role.prompt_path).resolve(strict=True)
                    if not prompt.is_relative_to(root / ".clink" / "prompts") or not prompt.is_file():
                        raise SetupError(f"Generated role prompt is outside the project: {path.name}")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _directory(root_fd: int, parts: tuple[str, ...], root: Path, *, create: bool = False) -> Iterator[int | None]:
    descriptor = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except OSError:
                raise SetupError(
                    f"Expected a real destination directory without symlinks: {root.joinpath(*parts[:index + 1])}"
                ) from None
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _read_destination(root_fd: int, parts: tuple[str, ...], root: Path) -> tuple[bytes | None, os.stat_result | None]:
    path = root.joinpath(*parts)
    with _directory(root_fd, parts[:-1], root) as parent_fd:
        if parent_fd is None:
            return None, None
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            return None, None
        except OSError:
            raise SetupError(f"Expected a regular destination file without symlinks: {path}") from None
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SetupError(f"Expected a regular destination file: {path}")
            content = _bounded_read(stream, path)
            if _identity(os.fstat(stream.fileno())) != _identity(info):
                raise SetupError(f"Destination changed while being read; rerun setup: {path}")
            return content, info


def _registration(project: Path) -> dict[str, Any]:
    # Resolving sys.executable would erase the virtualenv path on symlink-based environments.
    interpreter = os.path.abspath(sys.executable)
    server = PROJECT_ROOT / "server.py"
    if not server.is_file():
        raise SetupError(f"Installed MCP server entrypoint is missing: {server}")
    return {"command": interpreter, "args": [str(server.absolute()), "--project", str(project)]}


def _plan(root_fd: int, root: Path, contents: dict[tuple[str, ...], bytes]) -> list[_PlannedFile]:
    plans: list[_PlannedFile] = []
    # Check registration first so a conflicting operator registration creates no project files.
    parts = (".mcp.json",)
    previous, info = _read_destination(root_fd, parts, root)
    data = _json_object(previous, root / parts[0]) if previous is not None else {}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SetupError(f"Expected mcpServers to be a JSON object: {root / parts[0]}")
    expected = _registration(root)
    if "clink" in servers and servers["clink"] != expected:
        raise SetupError(
            f"Conflicting Clink registration; preserve or edit it explicitly before setup: {root / parts[0]}"
        )
    if "clink" in servers:
        registration_content = previous
    else:
        servers["clink"] = expected
        registration_content = _json_bytes(data)
    if len(registration_content) > MAX_SETUP_FILE_BYTES:
        raise SetupError(f"Merged configuration would exceed {MAX_SETUP_FILE_BYTES} bytes: {root / parts[0]}")
    registration = _PlannedFile(
        parts,
        registration_content,
        previous,
        _identity(info) if info else None,
        stat.S_IMODE(info.st_mode) if info else 0o600,
    )
    # Publish prompt dependencies before manifests, then the registration last.
    for parts, content in sorted(contents.items(), key=lambda item: (item[0][1] != "prompts", item[0])):
        existing, info = _read_destination(root_fd, parts, root)
        if existing is not None and existing != content:
            raise SetupError(
                f"Conflicting project file; preserve or edit it explicitly before setup: {root.joinpath(*parts)}"
            )
        plans.append(_PlannedFile(parts, content, existing, _identity(info) if info else None))
    plans.append(registration)
    return plans


def _check_unchanged(root_fd: int, root: Path, planned: _PlannedFile) -> None:
    current, info = _read_destination(root_fd, planned.parts, root)
    if current != planned.previous or (_identity(info) if info else None) != planned.identity:
        raise SetupError(f"Destination changed during setup; rerun after reviewing it: {root.joinpath(*planned.parts)}")


def _check_parent(root_fd: int, parent_fd: int, root: Path, parts: tuple[str, ...]) -> None:
    root_info = root.lstat()
    held_root = os.fstat(root_fd)
    if (root_info.st_dev, root_info.st_ino) != (held_root.st_dev, held_root.st_ino):
        raise SetupError(f"Project directory changed during setup; review it before rerunning: {root}")
    with _directory(root_fd, parts, root) as current_fd:
        held = os.fstat(parent_fd)
        current = os.fstat(current_fd) if current_fd is not None else None
        if current is None or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise SetupError(
                f"Destination directory changed during setup; review it before rerunning: {root.joinpath(*parts)}"
            )


def _publish(root_fd: int, root: Path, planned: _PlannedFile) -> None:
    with _directory(root_fd, planned.parts[:-1], root, create=True) as parent_fd:
        assert parent_fd is not None
        # Create and fully flush each file before its name becomes visible.
        name = f".clink-setup-{os.urandom(12).hex()}"
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, planned.mode, dir_fd=parent_fd)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(planned.content)
                stream.flush()
                os.fsync(stream.fileno())
            _check_unchanged(root_fd, root, planned)
            _check_parent(root_fd, parent_fd, root, planned.parts[:-1])
            if planned.previous is None:
                try:
                    os.link(name, planned.parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                except FileExistsError:
                    raise SetupError(
                        f"Destination appeared during setup; review it before rerunning: {root.joinpath(*planned.parts)}"
                    ) from None
            else:
                # The directory lock serializes setup processes. Recheck detects editor changes;
                # unrelated editors do not participate in this cooperative lock.
                os.replace(name, planned.parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def setup_project(project_dir: str | os.PathLike[str] = ".", *, cli_names: Sequence[str] | None = None) -> SetupResult:
    """Generate selected project templates and publish the project-bound MCP registration last."""
    try:
        project = Path(project_dir).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise SetupError(f"Project must be an existing directory: {project_dir}") from None
    if not project.is_dir():
        raise SetupError(f"Project must be an existing directory: {project}")
    if os.name != "posix":
        raise SetupError("Project setup currently requires POSIX no-follow file operations")
    import fcntl

    created: list[Path] = []
    try:
        contents = _template_files(cli_names)
        descriptor = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SetupError(f"Another setup is running for this project: {project}") from None
            plans = _plan(descriptor, project, contents)
            for planned in plans:
                _check_unchanged(descriptor, project, planned)
            unchanged: list[Path] = []
            for planned in plans:
                if planned.previous == planned.content:
                    _check_unchanged(descriptor, project, planned)
                    unchanged.append(project.joinpath(*planned.parts))
                else:
                    _publish(descriptor, project, planned)
                    created.append(project.joinpath(*planned.parts))
            return SetupResult(project, tuple(created), tuple(unchanged))
        finally:
            os.close(descriptor)
    except SetupError as exc:
        if created:
            raise SetupError(
                f"{exc}; at least {len(created)} files published, rerun the same setup to resume"
            ) from None
        raise
    except OSError as exc:
        raise SetupError(
            f"Cannot set up project {project} ({type(exc).__name__}); check paths and permissions; "
            f"at least {len(created)} files published, rerun the same setup to resume"
        ) from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=".", help="Existing project directory (default: current directory)")
    parser.add_argument(
        "--cli",
        action="append",
        dest="cli_names",
        metavar="NAME",
        help="Bundled CLI to configure; repeat to select clients (default: all)",
    )
    args = parser.parse_args(argv)
    try:
        result = setup_project(args.project, cli_names=args.cli_names)
    except SetupError as exc:
        print(f"clink setup: {exc}", file=sys.stderr)
        return 1
    print(
        f"Configured Clink for {result.project_dir}: {len(result.created)} files written, {len(result.unchanged)} unchanged"
    )
    for path in (*result.created, *result.unchanged):
        print(f"  {path.relative_to(result.project_dir)}")
    print("Restart the MCP client/server, then run listmodels to check CLI discovery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
