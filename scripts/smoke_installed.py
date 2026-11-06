"""Check an installed release from outside the checkout, without native CLI calls."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

EXPECTED_TOOLS = {
    "chat",
    "clink",
    "thinkdeep",
    "planner",
    "consensus",
    "codereview",
    "precommit",
    "debug",
    "secaudit",
    "docgen",
    "analyze",
    "refactor",
    "tracer",
    "testgen",
    "challenge",
    "apilookup",
    "listmodels",
    "version",
}


def isolated_environment(home: Path) -> dict[str, str]:
    """Expose the installed entrypoints and a disposable, empty configuration home."""
    home.mkdir(parents=True, exist_ok=True)
    temporary = home / "tmp"
    temporary.mkdir()
    return {
        "PATH": str(Path(sys.executable).parent),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "TMPDIR": str(temporary),
        "LANG": "C.UTF-8",
        "CI": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
    }


async def handshake(command: Path, environment: dict[str, str], cwd: Path, version: str) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=str(command), args=[], env=environment, cwd=str(cwd))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
            initialized = await session.initialize()
            if initialized.serverInfo.name != "clink-mcp-server" or initialized.serverInfo.version != version:
                raise ValueError("MCP initialization did not report the installed release identity")
            if initialized.capabilities.tools is None or initialized.capabilities.prompts is None:
                raise ValueError("MCP initialization omitted required capabilities")
            tools = await session.list_tools()
            if len(tools.tools) != len(EXPECTED_TOOLS) or {tool.name for tool in tools.tools} != EXPECTED_TOOLS:
                raise ValueError("Installed MCP tool discovery differs from the supported tool set")
            result = await session.call_tool("version", arguments={})
            if result.isError or len(result.content) != 1 or result.content[0].type != "text":
                raise ValueError("Installed MCP version call failed")
            payload = json.loads(result.content[0].text)
            if payload.get("status") != "success" or payload.get("version") != version:
                raise ValueError("Installed MCP version call reported an unexpected release")
            return {"initialized": True, "tool_count": len(tools.tools), "version_call": "success"}


def smoke(source_root: Path) -> dict:
    source_root = source_root.resolve()
    prefix = Path(sys.prefix).resolve()
    if (
        sys.prefix == sys.base_prefix
        or prefix.is_relative_to(source_root)
        or Path.cwd().resolve().is_relative_to(source_root)
    ):
        raise ValueError("Use a fresh virtual environment and working directory outside the checkout")
    # Resolve package identity before exercising either executable; source imports
    # can otherwise make a broken wheel appear healthy.
    for name in ("clink", "config", "server"):
        origin = Path(importlib.util.find_spec(name).origin).resolve()
        if not origin.is_relative_to(prefix) or origin.is_relative_to(source_root):
            raise ValueError("Smoke check imported a checkout module instead of the installed release")
    distribution = importlib.metadata.distribution("clink-cli")
    version = distribution.version
    scripts = {entry.name: entry.value for entry in distribution.entry_points if entry.group == "console_scripts"}
    if scripts != {"clink": "clink.cli:main", "clink-mcp-server": "server:run"}:
        raise ValueError("Installed console entrypoints differ from the release contract")
    with tempfile.TemporaryDirectory(prefix="clink-installed-smoke-") as directory:
        work = Path(directory)
        environment = isolated_environment(work / "home")
        if any(shutil.which(name, path=environment["PATH"]) for name in ("codex", "claude", "grok", "agy", "gemini")):
            raise ValueError("Installed smoke environment must not contain native CLI agents")
        commands = {name: Path(sys.executable).parent / name for name in scripts}
        for command in commands.values():
            result = subprocess.run(
                [str(command), "--help"], cwd=work, env=environment, capture_output=True, timeout=20, check=True
            )
            if not result.stdout or len(result.stdout) > 65536:
                raise ValueError("Installed entrypoint help was empty or exceeded its bound")
        result = subprocess.run(
            [str(commands["clink"]), "generation-capabilities", "--json"],
            cwd=work,
            env=environment,
            capture_output=True,
            timeout=20,
            check=True,
        )
        if len(result.stdout) > 65536:
            raise ValueError("Installed generation capabilities exceeded their bound")
        capabilities = json.loads(result.stdout)
        families = capabilities.get("families", [])
        if (
            capabilities.get("schema_version") != 1
            or capabilities.get("generation_schemas") != [2, 3]
            or capabilities.get("preflight") is not None
            or len(families) != 4
            or {family["id"] for family in families} != {"codex", "claude", "agy", "grok"}
            or any(family["admission"]["status"] not in {"verified", "unverified"} for family in families)
        ):
            raise ValueError("Installed generation capability discovery returned an unexpected contract")
        protocol = asyncio.run(
            asyncio.wait_for(handshake(commands["clink-mcp-server"], environment, work, version), 30)
        )
    return {
        "version": version,
        "entrypoints": sorted(scripts),
        "generation_families": sorted(family["id"] for family in families),
        "mcp": protocol,
        "imports_from_installed_package": True,
        "native_cli_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path, help="Checkout that must not supply runtime imports")
    args = parser.parse_args()
    print(json.dumps(smoke(args.source_root), indent=2))


if __name__ == "__main__":
    main()
