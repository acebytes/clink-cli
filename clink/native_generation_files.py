"""Owned final-message resources survive native process supervision and safe extraction."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from clink.contracts import BackendError
from clink.generation_contracts import canonical_json, sha256
from clink.runner import STREAM_BYTES

FINAL_FILE_CONTRACT = (
    "native-generation-final-file/v1;directory=0700,file=0600;precreated-empty-exclusive;"
    "no-follow,regular,one-link,stable-device-inode-size-mtime;parent-stable;"
    "nonblank-valid-utf8,no-nul;capture-limit=10485760;read-after-shared-process-cleanup;"
    "stream-equality-not-required;cleanup=identity-checked-owned-directory-only;identity-binds-this-source"
)


def final_file_contract_digest() -> str:
    with Path(__file__).open("rb") as stream:
        content = stream.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise BackendError("policy_unverified", "The final-file implementation exceeds its bounded identity limit.")
    return sha256(
        canonical_json(
            {"contract": FINAL_FILE_CONTRACT, "source_sha256": sha256(content), "capture_bytes": STREAM_BYTES}
        )
    )


@dataclass(frozen=True)
class NativeFinalOutput:
    """An empty private inode is the only permitted native final-output destination."""

    path: Path
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]

    def _validate_directory(self) -> None:
        current = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o700
            or (current.st_dev, current.st_ino) != self.directory_identity
        ):
            raise BackendError("protocol_error", "The owned final-output directory changed.")

    def _validate_file(self, current: os.stat_result, *, empty: bool = False) -> None:
        if (
            not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != self.file_identity
            or (empty and current.st_size != 0)
        ):
            raise BackendError("protocol_error", "The owned final-output file changed or is unsafe.")

    def validate_prelaunch(self) -> None:
        try:
            self._validate_directory()
            self._validate_file(self.path.lstat(), empty=True)
        except OSError:
            raise BackendError("protocol_error", "The owned final-output resource is unavailable.") from None

    def read(self) -> str:
        """Require fresh bounded native output; never derive final text from operational streams."""
        try:
            self._validate_directory()
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                self._validate_file(before)
                if before.st_size > STREAM_BYTES:
                    raise BackendError("output_limit", "The native final-message file exceeded its capture limit.")
                content = stream.read(STREAM_BYTES + 1)
                after = os.fstat(stream.fileno())
            current = self.path.lstat()
            self._validate_file(after)
            self._validate_file(current)

            def fields(entry: os.stat_result) -> tuple[int, int, int, int]:
                return entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns

            if fields(before) != fields(after) or fields(after) != fields(current) or len(content) != after.st_size:
                raise BackendError("protocol_error", "The native final-message file changed during extraction.")
            self._validate_directory()
        except OSError:
            raise BackendError("protocol_error", "The native final-message file could not be safely read.") from None
        if not content or b"\x00" in content:
            raise BackendError("protocol_error", "The native final-message file is empty or contains NUL.")
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            raise BackendError("protocol_error", "The native final-message file is not valid UTF-8.") from None
        if not text.strip():
            raise BackendError("protocol_error", "The native final-message file contains no nonblank text.")
        return text


@contextmanager
def prepare_native_final_output() -> Iterator[NativeFinalOutput]:
    """Remove only this private resource after controlled cleanup has finished."""
    parent = Path(tempfile.mkdtemp(prefix="clink-native-generation-"))
    os.chmod(parent, 0o700)
    directory_identity = parent.lstat()
    final = NativeFinalOutput(parent / "final.txt", (directory_identity.st_dev, directory_identity.st_ino), (0, 0))
    try:
        path = parent / "final.txt"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            file_identity = os.fstat(stream.fileno())
        final = NativeFinalOutput(
            path,
            (directory_identity.st_dev, directory_identity.st_ino),
            (file_identity.st_dev, file_identity.st_ino),
        )
        yield final
    finally:
        _cleanup_owned_output(final)


def _cleanup_owned_output(final: NativeFinalOutput) -> None:
    """Never recurse into a replaced directory or remove unrelated contents."""
    descriptor = None
    try:
        descriptor = os.open(final.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        parent = os.fstat(descriptor)
        if not stat.S_ISDIR(parent.st_mode) or (parent.st_dev, parent.st_ino) != final.directory_identity:
            raise OSError
        try:
            os.unlink(final.path.name, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        if os.listdir(descriptor):
            raise OSError
        current = final.path.parent.lstat()
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != final.directory_identity:
            raise OSError
        os.rmdir(final.path.parent)
    except OSError:
        raise BackendError(
            "cleanup_unconfirmed", "The owned native final-output resource could not be safely removed."
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
