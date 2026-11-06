"""Verify built wheel/sdist resources without installing or launching CLI agents."""

from __future__ import annotations

import argparse
import configparser
import fnmatch
import hashlib
import json
import re
import stat
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

CATALOG = "clink/resources/codex-0.166.0-models-direct.json"
CATALOG_SHA256 = "69d3e68d0e78a684c56b1818f1351149902fc61c398e4b69d371dfdea08e4e12"
GENERATION_CATALOG = "clink/resources/codex-0.166.0-models-generation.json"
GENERATION_CATALOG_SHA256 = "1ec578b60a5d17ca1849c9227f92bedb72e4916333ab62a3e7e9dbff094a59b9"
CONSOLE_SCRIPTS = {"clink-mcp-server": "server:run", "clink": "clink.cli:main"}
NATIVE_GENERATION_ADMISSION = "clink/resources/native-generation-codex-0.166.0-admission.json"
NATIVE_GENERATION_ADMISSION_SHA256 = "f25df0fef075ad476e966e731d6a0b9661dbd45d73c25e4473edb7b40cec26a1"
CLAUDE_NATIVE_GENERATION_ADMISSION = "clink/resources/native-generation-claude-2.1.304-admission.json"
CLAUDE_NATIVE_GENERATION_ADMISSION_SHA256 = "73c3b2d1e03f1bcca1ccf78f8fd30e2a56a6f4f2ed16a7632d41f37eda9be6b6"
GROK_NATIVE_GENERATION_ADMISSION = "clink/resources/native-generation-grok-1.0.70-admission.json"
GROK_NATIVE_GENERATION_ADMISSION_SHA256 = "b7ec35215ce042ba4b128e4ec003a71a33e71d8bd825545e3fce85a365f49df5"
REQUIRED_RESOURCES = (
    CATALOG,
    GENERATION_CATALOG,
    NATIVE_GENERATION_ADMISSION,
    CLAUDE_NATIVE_GENERATION_ADMISSION,
    GROK_NATIVE_GENERATION_ADMISSION,
    "clink/resources/native-claude-2.1.304-admission.json",
    "clink/resources/native-grok-1.0.70-admission.json",
    "clink/resources/native-agy-1.4.12-admission.json",
    "clink/resources/CODEX-LICENSE",
    "clink/resources/CODEX-NOTICE",
    "clink/resources/PROVENANCE.md",
    "conf/cli_clients/codex.json",
    "conf/cli_clients/claude.json",
    "conf/cli_clients/gemini.json",
    "conf/cli_clients/agy.json",
    "conf/cli_clients/grok.json",
    "systemprompts/clink/default.txt",
    "systemprompts/clink/default_planner.txt",
    "systemprompts/clink/default_codereviewer.txt",
    "systemprompts/clink/codex_codereviewer.txt",
    "systemprompts/clink/native_default.txt",
    "systemprompts/clink/native_planner.txt",
    "systemprompts/clink/native_codereviewer.txt",
)
MAX_MEMBERS = 10000
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
PRIVATE_DIRECTORIES = {
    ".git",
    ".github",
    ".agents",
    ".codex",
    ".claude",
    ".clink",
    ".flow",
    ".venv",
    ".pal_venv",
    "__pycache__",
    ".pytest_cache",
    "logs",
    "test_output",
    "providers",
    "tests",
    "simulator_tests",
}
PRIVATE_FILES = {".mcp.json", "CLAUDE.local.md", "credentials.json", "auth.json", "settings.local.json"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{40,}\b"),
    re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
)


def _safe_name(name: str) -> str:
    parts = name.rstrip("/").split("/")
    if not name or "\\" in name or "\x00" in name or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Unsafe archive member path")
    if ":" in parts[0]:
        raise ValueError("Unsafe archive member path")
    return "/".join(parts)


def _read_members(path: Path) -> dict[str, bytes]:
    """Bound archive reads and reject links, duplicate paths and multiple sdist roots."""
    members: dict[str, bytes] = {}
    seen = set()
    total = 0

    def add(name: str, size: int, read, *, directory: bool = False) -> None:
        nonlocal total
        name = _safe_name(name)
        if name in seen or len(seen) >= MAX_MEMBERS:
            raise ValueError("Duplicate archive member or excessive member count")
        seen.add(name)
        if directory:
            return
        total += size
        if size < 0 or size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Archive member or total size exceeds the release limit")
        content = read()
        if len(content) != size:
            raise ValueError("Archive member size mismatch")
        members[name] = content

    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                    raise ValueError("Archive links and special files are forbidden")
                add(
                    member.filename, member.file_size, lambda item=member: archive.read(item), directory=member.is_dir()
                )
    else:
        root = None
        root_seen = False
        with tarfile.open(path) as archive:
            for count, member in enumerate(archive, 1):
                if count > MAX_MEMBERS:
                    raise ValueError("Excessive archive member count")
                name = _safe_name(member.name)
                parts = PurePosixPath(name).parts
                if root is None:
                    root = parts[0]
                if parts[0] != root or (len(parts) < 2 and not member.isdir()):
                    raise ValueError("Source distribution must have one enclosing directory")
                if not member.isfile() and not member.isdir():
                    raise ValueError("Archive links and special files are forbidden")
                if len(parts) == 1:
                    if root_seen:
                        raise ValueError("Duplicate archive member")
                    root_seen = True
                    continue
                relative = str(PurePosixPath(*parts[1:]))

                def read(item=member):
                    stream = archive.extractfile(item)
                    if stream is None:
                        raise ValueError("Unreadable archive member")
                    with stream:
                        return stream.read(MAX_MEMBER_BYTES + 1)

                add(relative, member.size, read, directory=member.isdir())
    return members


def verify(path: Path, *, forbidden_text: Iterable[str] = ()) -> dict:
    """Inspect archive members directly; never extract untrusted archive paths."""
    members = _read_members(path)
    forbidden = tuple(value.casefold() for value in forbidden_text if value)
    for name, content in members.items():
        parts = PurePosixPath(name).parts
        if (
            PRIVATE_DIRECTORIES.intersection(parts)
            or PRIVATE_FILES.intersection(parts)
            or any(part.startswith(".env") or part.endswith((".pem", ".key", ".pyc", ".pyo")) for part in parts)
        ):
            raise ValueError(f"Forbidden private, provider or environment file in {path.name}: {name}")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Possible credential material in {path.name}: {name}")
        if any(
            value in name.casefold() or value in content.decode("utf-8", errors="replace").casefold()
            for value in forbidden
        ):
            raise ValueError("Forbidden release text in archive (literal and member name withheld)")
        if name.startswith("conf/") and fnmatch.fnmatch(PurePosixPath(name).name, "*_models.json"):
            raise ValueError(f"Provider model catalog in {path.name}: {name}")
    for name in REQUIRED_RESOURCES:
        if not members.get(name):
            raise ValueError(f"Missing resource in {path.name}: {name}")
    if hashlib.sha256(members[CATALOG]).hexdigest() != CATALOG_SHA256:
        raise ValueError(f"Pinned Codex resource hash mismatch in {path.name}")
    if hashlib.sha256(members[GENERATION_CATALOG]).hexdigest() != GENERATION_CATALOG_SHA256:
        raise ValueError(f"Pinned generation catalog hash mismatch in {path.name}")
    if hashlib.sha256(members[NATIVE_GENERATION_ADMISSION]).hexdigest() != NATIVE_GENERATION_ADMISSION_SHA256:
        raise ValueError(f"Native generation admission hash mismatch in {path.name}")
    if (
        hashlib.sha256(members[CLAUDE_NATIVE_GENERATION_ADMISSION]).hexdigest()
        != CLAUDE_NATIVE_GENERATION_ADMISSION_SHA256
    ):
        raise ValueError(f"Claude native generation admission hash mismatch in {path.name}")
    if hashlib.sha256(members[GROK_NATIVE_GENERATION_ADMISSION]).hexdigest() != GROK_NATIVE_GENERATION_ADMISSION_SHA256:
        raise ValueError(f"Grok native generation admission hash mismatch in {path.name}")
    catalog = json.loads(members[CATALOG])
    if len(catalog["models"]) != 12 or any(model["tool_mode"] != "direct" for model in catalog["models"]):
        raise ValueError(f"Unexpected pinned Codex resource in {path.name}")
    entries = [name for name in members if name.endswith("/entry_points.txt")]
    if len(entries) != 1:
        raise ValueError(f"Missing or ambiguous entrypoint metadata in {path.name}")
    config = configparser.ConfigParser()
    config.read_string(members[entries[0]].decode())
    if dict(config["console_scripts"]) != CONSOLE_SCRIPTS:
        raise ValueError(f"Unexpected console entrypoint in {path.name}")
    if not any(name.endswith("LICENSE") and b"Beehive Innovations" in value for name, value in members.items()):
        raise ValueError(f"Missing PAL license attribution in {path.name}")
    return {
        "artifact": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "members": len(members),
        "resource_sha256": CATALOG_SHA256,
        "generation_catalog_sha256": GENERATION_CATALOG_SHA256,
        "native_generation_admission_sha256": NATIVE_GENERATION_ADMISSION_SHA256,
        "claude_native_generation_admission_sha256": CLAUDE_NATIVE_GENERATION_ADMISSION_SHA256,
        "grok_native_generation_admission_sha256": GROK_NATIVE_GENERATION_ADMISSION_SHA256,
        "provider_packages_or_catalogs": False,
        "private_files_or_detected_credentials": False,
        "forbidden_text_checks": len(forbidden),
        "entrypoints": CONSOLE_SCRIPTS.copy(),
        "native_cli_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing the locally built wheel and sdist")
    parser.add_argument(
        "--forbid-text", action="append", default=[], help="Reject a private literal (repeatable; never echoed)"
    )
    arguments = parser.parse_args()
    wheels = sorted(arguments.directory.glob("*.whl"))
    sources = sorted(arguments.directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error("Expected exactly one wheel and one source distribution")
    print(json.dumps([verify(path, forbidden_text=arguments.forbid_text) for path in wheels + sources], indent=2))


if __name__ == "__main__":
    main()
