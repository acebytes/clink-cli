"""Validate an external, content-addressed archive for opt-in provenance checks.

This helper is development tooling. It cannot create production admission or
change the resources used by the runtime. The expected digest must come from a
separate private release record, never from the archive being checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "MANIFEST.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
LIVE_HISTORY_REFUSAL = (
    "Live verification is unavailable in the public snapshot; use the original private verifier "
    "and complete consumed-submission history"
)


def refuse_live_verification(live: bool, project: Path | None = None) -> None:
    """Prevent the public snapshot from resetting an archived live-call allowance."""
    if live and (project is None or project.resolve() == Path(__file__).resolve().parents[1]):
        raise ValueError(LIVE_HISTORY_REFUSAL)


def verify_archive(root: Path, expected_sha256: str) -> Path:
    """Verify every immutable archive member against an independently supplied pin."""
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("Private evidence needs an independently pinned manifest SHA-256")
    root = root.expanduser().absolute()
    if root.resolve(strict=True) != root or root.name != expected_sha256:
        raise ValueError("Private evidence must use its canonical content-addressed directory")
    if not root.is_dir() or root.stat().st_mode & 0o222:
        raise ValueError("Private evidence archive must be an immutable directory")
    manifest_path = root / MANIFEST_NAME
    status = manifest_path.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_mode & 0o222 or status.st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Private evidence manifest must be a bounded immutable regular file")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Private evidence manifest differs from the independent pin")
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "scope", "files"}
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["scope"] != "clink-private-release-evidence"
        or not isinstance(manifest["files"], list)
        or not manifest["files"]
        or raw != (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    ):
        raise ValueError("Private evidence manifest must have its canonical supported shape")
    expected = {MANIFEST_NAME}
    total = 0
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise ValueError("Private evidence member has an unsupported shape")
        name = entry["path"]
        if not isinstance(name, str):
            raise ValueError("Private evidence member path must be relative")
        relative = PurePosixPath(name)
        if (
            not name
            or relative.is_absolute()
            or str(relative) != name
            or any(part in ("", ".", "..", ".git") for part in relative.parts)
            or name in expected
            or re.fullmatch(r"[0-9a-f]{64}", str(entry["sha256"])) is None
            or type(entry["size"]) is not int
            or not 0 <= entry["size"] <= MAX_FILE_BYTES
        ):
            raise ValueError("Private evidence member path, digest or size is invalid")
        expected.add(name)
        total += entry["size"]
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError("Private evidence archive exceeds its bounded size")
        member = root / name
        # Resolve only after inspecting each parent: a symlink may never select
        # bytes outside the explicitly pinned archive.
        for parent in member.parents:
            if parent == root:
                break
            parent_status = parent.lstat()
            if not stat.S_ISDIR(parent_status.st_mode) or parent_status.st_mode & 0o222:
                raise ValueError("Private evidence parent must be an immutable directory")
        member_status = member.lstat()
        if (
            not stat.S_ISREG(member_status.st_mode)
            or member_status.st_mode & 0o222
            or member_status.st_size != entry["size"]
            or hashlib.sha256(member.read_bytes()).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Private evidence member differs from its immutable pin")
    actual = set()
    for path in root.rglob("*"):
        member_status = path.lstat()
        if stat.S_ISDIR(member_status.st_mode) and not member_status.st_mode & 0o222:
            continue
        if not stat.S_ISREG(member_status.st_mode):
            raise ValueError("Private evidence archive contains a symlink or unsupported member")
        actual.add(str(path.relative_to(root)))
    if actual != expected:
        raise ValueError("Private evidence archive contains unmanifested members")
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="Manifest pin from the separate private release record")
    args = parser.parse_args()
    try:
        verify_archive(args.root, args.sha256)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("Private evidence archive verified against its independent manifest pin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
