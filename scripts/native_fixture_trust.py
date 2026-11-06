"""Temporary trust for an explicitly selected, disposable native verification repo.

This helper never changes permission modes or authentication. It grants only the
new fixture's exact canonical Git root, and removes only its unchanged grant.
Unrelated TOML bytes survive publication and cleanup. Production never uses it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Development dependency on Python 3.10.
    import tomli as tomllib

MAX_STORE_BYTES = 1024 * 1024


class FixtureTrustError(RuntimeError):
    """A bounded technical failure without configuration values or project paths."""


def _document(raw: bytes) -> dict:
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise FixtureTrustError("Native trust configuration is not valid UTF-8 TOML.") from None


def _check_file(path: Path, *, missing: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return
        raise FixtureTrustError("Native trust file disappeared.") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise FixtureTrustError("Native trust files must be singly linked, owner-owned regular files.")
    if info.st_mode & 0o022:
        raise FixtureTrustError("Native trust files must not be writable by other users.")


def _read(path: Path) -> bytes:
    _check_file(path, missing=True)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b""
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o022:
            raise FixtureTrustError("Native trust file identity changed during read.")
        data = stream.read(MAX_STORE_BYTES + 1)
    if len(data) > MAX_STORE_BYTES:
        raise FixtureTrustError("Native trust configuration exceeds the verification limit.")
    return data


def _lock(fd: int) -> None:
    until = time.monotonic() + 5
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= until:
                raise FixtureTrustError("Native trust coordination lock timed out.") from None
            time.sleep(0.02)


def _publish(path: Path, before: bytes, after: bytes) -> None:
    if len(after) > MAX_STORE_BYTES:
        raise FixtureTrustError("Native trust configuration exceeds the verification limit.")
    if _read(path) != before:
        raise FixtureTrustError("Native trust configuration changed without its coordination lock.")
    fd, name = tempfile.mkstemp(prefix=".clink-fixture-trust-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fixture_root(project: Path) -> Path:
    if not project.is_absolute() or project != project.resolve() or not (project / ".git").is_dir():
        raise FixtureTrustError("Fixture trust requires a canonical standalone Git repository.")
    if (project / ".git").is_symlink() or project in {Path.home(), Path(project.anchor)}:
        raise FixtureTrustError("Fixture trust requires a disposable standalone repository.")
    if not (project / ".clink-native-verification-fixture").is_file():
        raise FixtureTrustError("Fixture trust requires the verifier's disposable-project marker.")
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "HOME": str(project), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode or result.stdout.strip() != str(project):
        raise FixtureTrustError("Fixture trust must not include an enclosing repository or worktree.")
    return project


@contextlib.contextmanager
def grok_fixture_trust(project: Path, grok_home: Path) -> Iterator[dict[str, bool]]:
    """Grant only an absent fixture key; retain any concurrent unrelated entries.

    The caller must expose explicit opt-in. The stable native flock sidecar is
    never unlinked. Existing records are never replaced, even if untrusted.
    """
    project = _fixture_root(project)
    if not grok_home.is_absolute() or grok_home != grok_home.resolve() or not grok_home.is_dir():
        raise FixtureTrustError("Native configuration home must be a canonical existing directory.")
    if grok_home.stat().st_uid != os.getuid() or grok_home.stat().st_mode & 0o022:
        raise FixtureTrustError("Native configuration home must be private to its owner.")
    path = grok_home / "trusted_folders.toml"
    lock = grok_home / "trusted_folders.toml.lock"
    _check_file(lock, missing=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    key = str(project)
    value = {"trusted": True, "decided_at": int(time.time())}
    header = "[folders." + json.dumps(key, ensure_ascii=False) + "]"
    block = f"{header}\ntrusted = true\ndecided_at = {value['decided_at']}\n".encode()
    receipt = {"fixture_trust_prepared": False, "fixture_trust_removed": False}
    try:
        _check_file(lock)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o022:
            raise FixtureTrustError("Native trust coordination lock identity changed.")
        _lock(fd)
        before = _read(path)
        document = _document(before)
        folders = document.get("folders", {})
        if not isinstance(folders, dict) or key in folders:
            raise FixtureTrustError("Fixture trust key already exists or the trust map is invalid.")
        expected = {**document, "folders": {**folders, key: value}}
        addition = (b"\n" if before and not before.endswith(b"\n") else b"") + block
        after = before + addition
        if _document(after) != expected:
            raise FixtureTrustError("Fixture trust cannot be added without changing unrelated settings.")
        _publish(path, before, after)
        receipt["fixture_trust_prepared"] = True
        fcntl.flock(fd, fcntl.LOCK_UN)
        try:
            yield receipt
        finally:
            _lock(fd)
            current = _read(path)
            document = _document(current)
            folders = document.get("folders", {})
            if not isinstance(folders, dict) or folders.get(key) != value:
                raise FixtureTrustError("Fixture trust grant changed; cleanup refused to replace it.")
            # Remove only our exact lines, preserving adjacent unrelated comments.
            # Native writers may move the table; retain their unrelated bytes.
            if current.count(addition) == 1:
                cleaned, count = current.replace(addition, b"", 1), 1
            else:
                pattern = re.compile(rb"^" + re.escape(block), re.M)
                cleaned, count = pattern.subn(b"", current)
            if count != 1:
                raise FixtureTrustError("Fixture trust formatting changed; precise cleanup is unavailable.")
            expected = {**document, "folders": {name: entry for name, entry in folders.items() if name != key}}
            parsed = _document(cleaned)
            # Removing the only folder table can also remove its implicit parent.
            parsed.setdefault("folders", {})
            if parsed != expected:
                raise FixtureTrustError("Fixture trust cleanup would change unrelated settings.")
            _publish(path, current, cleaned)
            receipt["fixture_trust_removed"] = True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
