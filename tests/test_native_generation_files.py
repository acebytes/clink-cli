"""Independent private native final-file validation and cleanup, without model execution."""

import os
import stat
from pathlib import Path

import pytest

from clink.contracts import BackendError
from clink.native_generation_files import final_file_contract_digest, prepare_native_final_output
from clink.runner import STREAM_BYTES


def test_private_final_file_is_owned_empty_and_removed_after_context():
    with prepare_native_final_output() as final:
        path = final.path
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_bytes() == b""
        final.validate_prelaunch()
        text = "  café 🦉\r\nfinal\u2028message\u2029"
        path.write_bytes(text.encode())
        assert final.read() == text
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("content", [b"", b" \t\r\n", b"\xff", b"valid\x00invalid"])
def test_empty_invalid_utf8_and_nul_never_establish_final_output(content):
    with prepare_native_final_output() as final:
        final.path.write_bytes(content)
        with pytest.raises(BackendError) as caught:
            final.read()
        assert caught.value.code == "protocol_error"


@pytest.mark.parametrize("change", ["replace", "symlink", "hardlink", "mode", "directory_mode"])
def test_unsafe_final_resources_are_rejected_without_reading_other_files(tmp_path, change):
    external = tmp_path / "unrelated"
    external.write_text("private unrelated content")
    with prepare_native_final_output() as final:
        if change == "replace":
            replacement = final.path.with_name("replacement")
            replacement.write_text("replacement")
            replacement.chmod(0o600)
            replacement.replace(final.path)
        elif change == "symlink":
            final.path.unlink()
            final.path.symlink_to(external)
        elif change == "hardlink":
            os.link(final.path, final.path.with_name("other-link"))
        elif change == "mode":
            final.path.write_text("answer")
            final.path.chmod(0o644)
        else:
            final.path.write_text("answer")
            final.path.parent.chmod(0o755)
        with pytest.raises(BackendError) as caught:
            final.read()
        assert caught.value.code == "protocol_error"
        assert "private unrelated" not in str(caught.value)
        if change == "hardlink":
            final.path.with_name("other-link").unlink()
    assert external.read_text() == "private unrelated content"


def test_prelaunch_rejects_stale_contents_or_missing_file():
    with prepare_native_final_output() as final:
        final.path.write_text("stale text")
        with pytest.raises(BackendError):
            final.validate_prelaunch()
        final.path.unlink()
        with pytest.raises(BackendError):
            final.validate_prelaunch()


def test_oversized_file_is_bounded_before_read():
    with prepare_native_final_output() as final:
        with final.path.open("wb") as stream:
            stream.truncate(STREAM_BYTES + 1)
        with pytest.raises(BackendError) as caught:
            final.read()
        assert caught.value.code == "output_limit"


def test_final_file_identity_digest_binds_source_and_contract():
    assert len(final_file_contract_digest()) == 64
    assert final_file_contract_digest() == final_file_contract_digest()


def test_controlled_exception_removes_private_resources():
    with pytest.raises(RuntimeError), prepare_native_final_output() as final:
        path = final.path
        raise RuntimeError("synthetic")
    assert not Path(path).exists() and not Path(path).parent.exists()


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_replaced_parent_is_never_cleaned_as_an_owned_directory(tmp_path, monkeypatch, replacement):
    import clink.native_generation_files as module

    owned = tmp_path / "owned"
    owned.mkdir()
    moved = tmp_path / "moved-owned"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "must-survive"
    sentinel.write_text("unrelated content")
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **_: str(owned))
    with pytest.raises(BackendError) as cleanup:
        with prepare_native_final_output() as final:
            owned.rename(moved)
            if replacement == "directory":
                owned.mkdir()
                (owned / "foreign-content").write_text("preserve replacement")
            else:
                owned.symlink_to(foreign, target_is_directory=True)
            with pytest.raises(BackendError) as read_error:
                final.read()
            assert read_error.value.code == "protocol_error"
    assert cleanup.value.code == "cleanup_unconfirmed"
    assert sentinel.read_text() == "unrelated content"
    if replacement == "directory":
        assert (owned / "foreign-content").read_text() == "preserve replacement"
    assert (moved / "final.txt").exists()


def test_cleanup_refuses_unrelated_extra_entries_and_preserves_them(tmp_path, monkeypatch):
    import clink.native_generation_files as module

    owned = tmp_path / "owned"
    owned.mkdir()
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **_: str(owned))
    with pytest.raises(BackendError) as caught:
        with prepare_native_final_output() as final:
            extra = final.path.parent / "unrelated"
            extra.write_text("preserve")
    assert caught.value.code == "cleanup_unconfirmed" and extra.read_text() == "preserve"
