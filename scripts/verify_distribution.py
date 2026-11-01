"""Verify built wheel/sdist resources without installing or launching CLI agents."""

from __future__ import annotations

import argparse
import configparser
import fnmatch
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

CATALOG = "clink/resources/codex-0.166.0-models-direct.json"
CATALOG_SHA256 = "69d3e68d0e78a684c56b1818f1351149902fc61c398e4b69d371dfdea08e4e12"
REQUIRED_RESOURCES = (
    CATALOG,
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


def verify(path: Path) -> dict:
    """Inspect archive members directly; never extract untrusted archive paths."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    else:
        with tarfile.open(path) as archive:
            members = {}
            for member in archive.getmembers():
                if member.isfile():
                    relative = str(PurePosixPath(member.name).relative_to(PurePosixPath(member.name).parts[0]))
                    members[relative] = archive.extractfile(member).read()
    for name in members:
        parts = PurePosixPath(name).parts
        if "providers" in parts or any(part.startswith(".env") for part in parts):
            raise ValueError(f"Forbidden provider or environment file in {path.name}: {name}")
        if name.startswith("conf/") and fnmatch.fnmatch(PurePosixPath(name).name, "*_models.json"):
            raise ValueError(f"Provider model catalog in {path.name}: {name}")
    for name in REQUIRED_RESOURCES:
        if not members.get(name):
            raise ValueError(f"Missing resource in {path.name}: {name}")
    if hashlib.sha256(members[CATALOG]).hexdigest() != CATALOG_SHA256:
        raise ValueError(f"Pinned Codex resource hash mismatch in {path.name}")
    catalog = json.loads(members[CATALOG])
    if len(catalog["models"]) != 12 or any(model["tool_mode"] != "direct" for model in catalog["models"]):
        raise ValueError(f"Unexpected pinned Codex resource in {path.name}")
    entries = [name for name in members if name.endswith("/entry_points.txt")]
    if len(entries) != 1:
        raise ValueError(f"Missing or ambiguous entrypoint metadata in {path.name}")
    config = configparser.ConfigParser()
    config.read_string(members[entries[0]].decode())
    if dict(config["console_scripts"]) != {"clink-mcp-server": "server:run"}:
        raise ValueError(f"Unexpected console entrypoint in {path.name}")
    if not any(name.endswith("LICENSE") and b"Beehive Innovations" in value for name, value in members.items()):
        raise ValueError(f"Missing PAL license attribution in {path.name}")
    return {
        "artifact": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "members": len(members),
        "resource_sha256": CATALOG_SHA256,
        "provider_packages_or_catalogs": False,
        "entrypoint": "clink-mcp-server=server:run",
        "native_cli_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing the locally built wheel and sdist")
    arguments = parser.parse_args()
    wheels = sorted(arguments.directory.glob("*.whl"))
    sources = sorted(arguments.directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error("Expected exactly one wheel and one source distribution")
    print(json.dumps([verify(path) for path in wheels + sources], indent=2))


if __name__ == "__main__":
    main()
