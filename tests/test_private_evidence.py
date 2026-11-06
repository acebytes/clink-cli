"""Exercise the opt-in archive boundary with synthetic evidence, never historical authority."""

from __future__ import annotations

import hashlib
import json

import pytest

from scripts import private_evidence


@pytest.fixture
def archive(tmp_path):
    content = b"synthetic provenance bytes\n"
    manifest = {
        "schema_version": 1,
        "scope": "clink-private-release-evidence",
        "files": [{"path": "proofs/receipt.json", "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}],
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    checksum = hashlib.sha256(raw).hexdigest()
    root = tmp_path / checksum
    (root / "proofs").mkdir(parents=True)
    (root / "proofs/receipt.json").write_bytes(content)
    (root / private_evidence.MANIFEST_NAME).write_bytes(raw)
    for path in root.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)
    yield root, checksum
    # Restore only synthetic fixture modes so pytest can remove its temporary tree.
    root.chmod(0o755)
    for path in root.rglob("*"):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)


def test_private_archive_requires_an_independent_pin_and_immutable_content(archive):
    root, checksum = archive
    assert private_evidence.verify_archive(root, checksum) == root


@pytest.mark.parametrize("checksum", ["", "not-a-digest", "A" * 64, "1" * 63])
def test_private_archive_rejects_invalid_pins_before_reading_files(tmp_path, checksum):
    with pytest.raises(ValueError, match="independently pinned"):
        private_evidence.verify_archive(tmp_path / "missing", checksum)


def test_private_archive_refuses_missing_inputs(tmp_path):
    with pytest.raises(OSError):
        private_evidence.verify_archive(tmp_path / ("a" * 64), "a" * 64)


@pytest.mark.parametrize("change", ["root_name", "manifest_bytes", "member_bytes", "extra_member"])
def test_private_archive_rejects_changed_or_unmanifested_content(archive, change):
    root, checksum = archive
    if change == "root_name":
        renamed = root.with_name("unbound-archive")
        root.chmod(0o755)
        root.rename(renamed)
        renamed.chmod(0o555)
        try:
            with pytest.raises(ValueError, match="content-addressed"):
                private_evidence.verify_archive(renamed, checksum)
        finally:
            renamed.chmod(0o755)
            renamed.rename(root)
            root.chmod(0o555)
        return
    if change == "manifest_bytes":
        path = root / private_evidence.MANIFEST_NAME
    elif change == "member_bytes":
        path = root / "proofs/receipt.json"
    else:
        root.chmod(0o755)
        path = root / "unmanifested.json"
        path.write_bytes(b"unmanifested bytes")
        root.chmod(0o555)
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b"\n")
    path.chmod(0o444)
    with pytest.raises(ValueError):
        private_evidence.verify_archive(root, checksum)


@pytest.mark.parametrize("relative", [".", "proofs", "proofs/receipt.json", private_evidence.MANIFEST_NAME])
def test_private_archive_rejects_writable_members_and_parents(archive, relative):
    root, checksum = archive
    path = root / relative
    path.chmod(0o755 if path.is_dir() else 0o644)
    with pytest.raises(ValueError, match="immutable"):
        private_evidence.verify_archive(root, checksum)


@pytest.mark.parametrize("kind", ["root", "parent", "member", "manifest"])
def test_private_archive_rejects_links_even_when_target_bytes_match(archive, tmp_path, kind):
    root, checksum = archive
    if kind == "root":
        alias = tmp_path / "alias" / checksum
        alias.parent.mkdir()
        alias.symlink_to(root, target_is_directory=True)
        with pytest.raises(ValueError, match="canonical"):
            private_evidence.verify_archive(alias, checksum)
        return
    relative = {"parent": "proofs", "member": "proofs/receipt.json", "manifest": "MANIFEST.json"}[kind]
    source = root / relative
    parent = source.parent
    parent.chmod(0o755)
    target = tmp_path / ("detached-" + kind)
    if source.is_dir():
        source.chmod(0o755)
    source.rename(target)
    if target.is_dir():
        target.chmod(0o555)
    source.symlink_to(target, target_is_directory=kind == "parent")
    parent.chmod(0o555)
    try:
        with pytest.raises(ValueError):
            private_evidence.verify_archive(root, checksum)
    finally:
        parent.chmod(0o755)
        source.unlink()
        if target.is_dir():
            target.chmod(0o755)
        target.rename(source)
        if source.is_dir():
            source.chmod(0o555)
        parent.chmod(0o555)


def test_private_archive_rejects_unmanifested_directory_links(archive, tmp_path):
    root, checksum = archive
    target = tmp_path / "unmanifested-target"
    target.mkdir()
    root.chmod(0o755)
    link = root / "unmanifested-directory"
    link.symlink_to(target, target_is_directory=True)
    root.chmod(0o555)
    try:
        with pytest.raises(ValueError):
            private_evidence.verify_archive(root, checksum)
    finally:
        root.chmod(0o755)
        link.unlink()
        root.chmod(0o555)
