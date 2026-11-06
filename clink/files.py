"""Open declared context through anchored descriptors without following symlinks."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from clink.contracts import BackendError


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_context(path: Path, roots: tuple[Path, ...], limit: int) -> bytes:
    """Read a bounded regular file, refusing swapped leaf/parent links and special files."""
    if os.name != "posix":
        raise BackendError("unsupported_platform", "Safe context admission requires verified POSIX descriptors.")
    if not path.is_absolute() or not any(path == root or root in path.parents for root in roots):
        raise BackendError("path_not_allowed", "The context file is outside the approved roots.")
    descriptors: list[int] = []
    parents: list[tuple[Path, int]] = []
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open("/", flags | os.O_DIRECTORY)
        descriptors.append(descriptor)
        current = Path("/")
        parents.append((current, descriptor))
        for component in path.parts[1:-1]:
            descriptor = os.open(component, flags | os.O_DIRECTORY, dir_fd=descriptor)
            descriptors.append(descriptor)
            current /= component
            parents.append((current, descriptor))
        descriptor = os.open(path.name, flags, dir_fd=descriptor)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BackendError("path_not_allowed", "Declared inline context must be a regular file.")
        chunks = []
        remaining = limit
        while remaining > 0:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        leaf = path.lstat()
        if (
            not stat.S_ISREG(leaf.st_mode)
            or _identity(before) != _identity(after)
            or _identity(after) != _identity(leaf)
        ):
            raise BackendError("path_not_allowed", "The context file changed during admission.")
        for parent, opened in parents:
            observed, retained = parent.lstat(), os.fstat(opened)
            if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
                retained.st_dev,
                retained.st_ino,
            ):
                raise BackendError("path_not_allowed", "A context directory changed during admission.")
        return b"".join(chunks)
    except BackendError:
        raise
    except (OSError, ValueError) as exc:
        raise BackendError("path_not_allowed", "The context file is unavailable or changed during admission.") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
