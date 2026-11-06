"""Bounded regular-file hashing detects context changes without claiming snapshots."""

import hashlib

import pytest

from clink.contracts import BackendError
from utils.consensus_manifest import MAX_CONSENSUS_BYTES, build_manifest, check_manifest


def test_manifest_records_order_logical_canonical_hash_and_size(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    link = tmp_path / "link"
    link.symlink_to(first)
    entries = build_manifest((str(link), str(second)), ((tmp_path,),))
    assert entries == [
        {
            "logical_path": str(link),
            "canonical_path": str(first),
            "sha256": hashlib.sha256(b"first").hexdigest(),
            "size_bytes": 5,
        },
        {
            "logical_path": str(second),
            "canonical_path": str(second),
            "sha256": hashlib.sha256(b"second").hexdigest(),
            "size_bytes": 6,
        },
    ]
    check_manifest(entries, ((tmp_path,),))
    link.unlink()
    link.symlink_to(second)
    with pytest.raises(BackendError) as exc:
        check_manifest(entries, ((tmp_path,),))
    assert exc.value.code == "consensus_invalidated"


@pytest.mark.parametrize("kind", ["directory", "relative", "missing", "outside"])
def test_manifest_rejects_unsafe_paths_before_reading(tmp_path, kind):
    path = tmp_path
    if kind == "relative":
        path = "relative.py"
    elif kind == "missing":
        path = tmp_path / "missing"
    elif kind == "outside":
        path = tmp_path / "context"
        path.write_text("content")
    roots = ((tmp_path / "narrow",),) if kind == "outside" else ((tmp_path,),)
    with pytest.raises(BackendError) as exc:
        build_manifest((str(path),), roots)
    assert exc.value.code == "path_not_allowed"


def test_all_participant_roots_are_enforced(tmp_path):
    path = tmp_path / "context"
    path.write_text("context")
    with pytest.raises(BackendError) as exc:
        build_manifest((str(path),), ((tmp_path,), (tmp_path / "other",)))
    assert exc.value.code == "path_not_allowed"


def test_count_and_total_byte_bounds(tmp_path):
    path = tmp_path / "context"
    path.write_text("context")
    with pytest.raises(BackendError) as exc:
        build_manifest(tuple([str(path)] * 257), ((tmp_path,),))
    assert exc.value.code == "input_limit"
    with path.open("wb") as output:
        output.truncate(MAX_CONSENSUS_BYTES + 1)
    with pytest.raises(BackendError) as exc:
        build_manifest((str(path),), ((tmp_path,),))
    assert exc.value.code == "input_limit"


def test_changed_bytes_or_missing_file_invalidate(tmp_path):
    path = tmp_path / "context"
    path.write_text("one")
    manifest = build_manifest((str(path),), ((tmp_path,),))
    path.write_text("two")
    with pytest.raises(BackendError) as exc:
        check_manifest(manifest, ((tmp_path,),))
    assert exc.value.code == "consensus_invalidated"
    path.unlink()
    with pytest.raises(BackendError) as exc:
        check_manifest(manifest, ((tmp_path,),))
    assert exc.value.code == "consensus_invalidated"


def test_exact_file_and_byte_limits_are_admitted(tmp_path):
    empty = tmp_path / "empty"
    empty.touch()
    assert len(build_manifest(tuple([str(empty)] * 256), ((tmp_path,),))) == 256
    with empty.open("wb") as source:
        source.truncate(MAX_CONSENSUS_BYTES)
    manifest = build_manifest((str(empty),), ((tmp_path,),))
    assert manifest[0]["size_bytes"] == MAX_CONSENSUS_BYTES
    another = tmp_path / "another"
    another.write_bytes(b"x")
    with pytest.raises(BackendError) as error:
        build_manifest((str(empty), str(another)), ((tmp_path,),))
    assert error.value.code == "input_limit"


def test_change_during_initial_hash_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "context"
    path.write_bytes(b"x" * 1024)
    real_sha256 = hashlib.sha256

    class ChangingDigest:
        def __init__(self):
            self.digest = real_sha256()

        def update(self, chunk):
            self.digest.update(chunk)
            path.write_bytes(b"changed size")

        def hexdigest(self):
            return self.digest.hexdigest()

    monkeypatch.setattr("utils.consensus_manifest.hashlib.sha256", ChangingDigest)
    with pytest.raises(BackendError) as error:
        build_manifest((str(path),), ((tmp_path,),))
    assert error.value.code == "consensus_invalidated"


def test_symlink_parent_navigation_hashes_the_same_resolved_file_as_the_backend(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    target = nested / "target"
    target.mkdir()
    (nested / "context").write_bytes(b"actual backend context")
    (tmp_path / "context").write_bytes(b"lexically normalized wrong context")
    link = tmp_path / "link"
    link.symlink_to(target)
    logical = link / ".." / "context"
    manifest = build_manifest((str(logical),), ((tmp_path,),))
    assert manifest[0]["canonical_path"] == str(nested / "context")
    assert manifest[0]["sha256"] == hashlib.sha256(b"actual backend context").hexdigest()
