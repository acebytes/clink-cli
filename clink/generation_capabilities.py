"""Bounded, read-only discovery of generation families and local admission identity."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clink.generation_contracts import canonical_json
from clink.native_generation_policy import NativeGenerationAdmission

_CAPABILITY_BYTES = 65536
_CAPABILITY_SECONDS = 5.0
_EXECUTABLE_BYTES = 512 * 1024 * 1024
_EVIDENCE_BYTES = 1024 * 1024
_HASH_CHUNK_BYTES = 256 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FAMILIES = (
    ("codex", "Codex", "gpt-6.1-sol"),
    ("claude", "Claude Code", "claude-opus-5-5"),
    ("agy", "Antigravity", None),
    ("grok", "Grok", None),
)


@dataclass(frozen=True)
class _FamilyBinding:
    admission: NativeGenerationAdmission
    recipe_digest: Callable[[], str]
    parser_digest: Callable[[], str]


def _family_binding(cli: str) -> _FamilyBinding:
    if cli == "codex":
        from clink.native_generation_policy import DEFAULT_NATIVE_ADMISSION, native_generation_recipe_digest
        from clink.parsers.codex_native_generation import parser_contract_digest

        return _FamilyBinding(DEFAULT_NATIVE_ADMISSION, native_generation_recipe_digest, parser_contract_digest)
    if cli == "claude":
        from clink.native_generation_claude_policy import (
            DEFAULT_CLAUDE_NATIVE_ADMISSION,
            claude_generation_recipe_digest,
        )
        from clink.parsers.claude_native_generation import parser_contract_digest

        return _FamilyBinding(DEFAULT_CLAUDE_NATIVE_ADMISSION, claude_generation_recipe_digest, parser_contract_digest)
    try:
        from clink.native_generation_families import (
            DEFAULT_AGY_NATIVE_ADMISSION,
            DEFAULT_GROK_NATIVE_ADMISSION,
            family_generation_parser_digest,
            family_generation_recipe_digest,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "clink.native_generation_families":
            raise
        return _FamilyBinding(NativeGenerationAdmission(), lambda: "", lambda: "")
    return _FamilyBinding(
        DEFAULT_AGY_NATIVE_ADMISSION if cli == "agy" else DEFAULT_GROK_NATIVE_ADMISSION,
        lambda: family_generation_recipe_digest(cli),
        lambda: family_generation_parser_digest(cli),
    )


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("Local identity verification exceeded its time limit.")


async def _hash_regular_file(
    filename: str, *, maximum_bytes: int, deadline: float, require_executable: bool = False
) -> str:
    """Hash one stable regular inode with bounded reads and cancellation between chunks."""
    _check_deadline(deadline)
    if not isinstance(filename, str) or not os.path.isabs(filename) or ".." in filename.split("/"):
        raise ValueError("Local identity verification requires an absolute regular file.")
    before = os.lstat(filename)
    if not stat.S_ISREG(before.st_mode) or before.st_size < 0 or before.st_size > maximum_bytes:
        raise ValueError("Local identity verification requires a bounded regular file.")
    if require_executable and (
        not before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) or not os.access(filename, os.X_OK)
    ):
        raise ValueError("Local identity verification requires an executable file.")
    _check_deadline(deadline)
    descriptor = os.open(filename, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if _identity(before) != _identity(os.fstat(descriptor)):
            raise ValueError("The local file changed during identity verification.")
        digest = hashlib.sha256()
        total = 0
        while True:
            _check_deadline(deadline)
            block = os.read(descriptor, min(_HASH_CHUNK_BYTES, maximum_bytes - total + 1))
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise ValueError("The local file exceeds its identity limit.")
            digest.update(block)
            await asyncio.sleep(0)
        _check_deadline(deadline)
        after = os.fstat(descriptor)
        current = os.lstat(filename)
        if _identity(before) != _identity(after) or _identity(after) != _identity(current) or total != after.st_size:
            raise ValueError("The local file changed during identity verification.")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


async def _admission_payload(binding: _FamilyBinding, deadline: float) -> dict[str, Any]:
    admission = binding.admission
    empty = {
        "status": "unverified",
        "admitted_version": None,
        "executable_sha256": None,
        "recipe_sha256": None,
        "parser_sha256": None,
        "evidence_sha256": None,
    }
    pins = (admission.executable_sha256, admission.recipe_sha256, admission.parser_sha256, admission.evidence_sha256)
    if (
        not admission.verified
        or not isinstance(admission.version, str)
        or not admission.version
        or len(admission.version) > 128
        or any(not isinstance(pin, str) or _SHA256.fullmatch(pin) is None for pin in pins)
        or not isinstance(admission.evidence_path, str)
        or not admission.evidence_path
    ):
        return empty
    try:
        _check_deadline(deadline)
        if binding.recipe_digest() != admission.recipe_sha256 or binding.parser_digest() != admission.parser_sha256:
            return empty
        evidence_path = Path(admission.evidence_path)
        if not evidence_path.is_absolute():
            evidence_path = Path(__file__).resolve().parent.parent / evidence_path
        observed = await _hash_regular_file(str(evidence_path), maximum_bytes=_EVIDENCE_BYTES, deadline=deadline)
        if observed != admission.evidence_sha256:
            return empty
    except (OSError, ValueError, RuntimeError):
        return empty
    return {
        "status": "verified",
        "admitted_version": admission.version,
        "executable_sha256": admission.executable_sha256,
        "recipe_sha256": admission.recipe_sha256,
        "parser_sha256": admission.parser_sha256,
        "evidence_sha256": admission.evidence_sha256,
    }


async def generation_capabilities(cli: str | None = None, executable: str | None = None) -> dict[str, Any]:
    """Describe supported selectors; optionally compare one exact local executable to admission."""
    if (cli is None) != (executable is None) or (cli is not None and cli not in {row[0] for row in _FAMILIES}):
        raise ValueError("Local preflight requires a supported CLI and an executable together.")
    deadline = time.monotonic() + _CAPABILITY_SECONDS
    families = []
    for family_id, display_name, default_model in _FAMILIES:
        families.append(
            {
                "id": family_id,
                "display_name": display_name,
                "executable_names": [family_id],
                "generation_schema": 2 if default_model else 3,
                "model_selection_kinds": ["explicit"] if default_model else ["native_default"],
                "default_model_selection": (
                    {"kind": "explicit", "id": default_model} if default_model else {"kind": "native_default"}
                ),
                "admission": await _admission_payload(_family_binding(family_id), deadline),
            }
        )
    preflight = None
    if cli is not None:
        observed = None
        try:
            observed = await _hash_regular_file(
                executable, maximum_bytes=_EXECUTABLE_BYTES, deadline=deadline, require_executable=True
            )
        except FileNotFoundError:
            status = "missing"
        except (OSError, ValueError):
            status = "invalid_executable"
        else:
            admission = next(row["admission"] for row in families if row["id"] == cli)
            status = (
                "admission_unverified"
                if admission["status"] != "verified"
                else "compatible" if observed == admission["executable_sha256"] else "invalid_executable"
            )
        preflight = {"cli": cli, "status": status, "executable_sha256": observed}
    result = {"schema_version": 1, "generation_schemas": [2, 3], "families": families, "preflight": preflight}
    if len(canonical_json(result)) > _CAPABILITY_BYTES:
        raise ValueError("Generation capabilities exceed the response limit.")
    return result
