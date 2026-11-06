"""Bounded change detection for explicitly declared consensus context files.

Hashes describe observations at checks. They are not persistent source snapshots,
read isolation, or proof that a file was unchanged between two checks.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from clink.contracts import BackendError

MAX_CONSENSUS_FILES = 256
MAX_CONSENSUS_BYTES = 64 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def build_manifest(paths: tuple[str, ...], root_sets: tuple[tuple[Path, ...], ...]) -> list[dict[str, Any]]:
    """Hash regular files incrementally, checking limits, roots, symlinks, and races."""
    if len(paths) > MAX_CONSENSUS_FILES:
        raise BackendError(
            "input_limit", "Consensus accepts at most 256 declared files and images.", "Declare fewer files."
        )
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    for raw_path in paths:
        # Keep '..' until resolve: lexical normalization before symlink resolution
        # can hash a different file than the native CLI will read.
        logical = Path(raw_path)
        if not logical.is_absolute():
            raise BackendError(
                "path_not_allowed", "Consensus context paths must be absolute.", "Use an approved absolute path."
            )
        try:
            canonical = logical.resolve(strict=True)
            if not root_sets or any(
                not _inside(logical, roots) or not _inside(canonical, roots) for roots in root_sets
            ):
                raise BackendError(
                    "path_not_allowed",
                    "Declared context is outside a participant's approved roots.",
                    "Choose files inside every participant's configured allowed_roots.",
                )
            before = canonical.stat()
            if not stat.S_ISREG(before.st_mode):
                raise BackendError(
                    "path_not_allowed",
                    "Consensus accepts explicit regular files only.",
                    "Declare files instead of directories or special files.",
                )
            if before.st_size > MAX_CONSENSUS_BYTES - total_bytes:
                raise BackendError("input_limit", "Consensus context exceeds 64 MiB.", "Declare less context.")
            descriptor = os.open(canonical, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                opened = os.fstat(source.fileno())
                if not stat.S_ISREG(opened.st_mode) or _signature(opened) != _signature(before):
                    raise BackendError(
                        "consensus_invalidated",
                        "A context file changed during admission.",
                        "Start a new consensus with stable files.",
                    )
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = source.read(_HASH_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_CONSENSUS_BYTES - total_bytes:
                        raise BackendError("input_limit", "Consensus context exceeds 64 MiB.", "Declare less context.")
                    digest.update(chunk)
                after = os.fstat(source.fileno())
            if (
                _signature(before) != _signature(after)
                or _signature(after) != _signature(canonical.stat())
                or logical.resolve(strict=True) != canonical
                or size != after.st_size
            ):
                raise BackendError(
                    "consensus_invalidated",
                    "A context file changed while being hashed.",
                    "Start a new consensus with stable files.",
                )
        except BackendError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise BackendError(
                "path_not_allowed",
                "A declared consensus file is unavailable.",
                "Declare readable regular files in approved roots.",
            ) from exc
        total_bytes += size
        entries.append(
            {
                "logical_path": str(logical),
                "canonical_path": str(canonical),
                "sha256": digest.hexdigest(),
                "size_bytes": size,
            }
        )
    return entries


def check_manifest(manifest: list[dict[str, Any]], root_sets: tuple[tuple[Path, ...], ...]) -> None:
    """Invalidate when any declared file changes, disappears, or becomes inaccessible."""
    try:
        observed = build_manifest(tuple(entry["logical_path"] for entry in manifest), root_sets)
    except BackendError as exc:
        raise BackendError(
            "consensus_invalidated",
            "Declared consensus context changed or became unavailable.",
            "Start a new consensus; previous responses are invalid.",
        ) from exc
    if observed != manifest:
        raise BackendError(
            "consensus_invalidated",
            "Declared consensus context changed.",
            "Start a new consensus; previous responses are invalid.",
        )
