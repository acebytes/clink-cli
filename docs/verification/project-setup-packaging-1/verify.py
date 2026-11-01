"""Build offline and verify setup/stdio discovery from an installed wheel."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path


def installed_smoke(install: Path, work: Path) -> None:
    sys.path.insert(0, str(install))
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from clink import setup

    assert Path(setup.__file__).is_relative_to(install)
    assert "utils.env" not in sys.modules
    assert "server" not in sys.modules
    projects = [work / "project one", work / "project two"]
    for project in projects:
        project.mkdir()
        result = setup.setup_project(project)
        assert len(result.created) == 13
        assert not setup.setup_project(project).created

    async def discover(project):
        registration = json.loads((project / ".mcp.json").read_text())["mcpServers"]["clink"]
        assert Path(registration["args"][0]) == install / "server.py"
        parameters = StdioServerParameters(**registration, cwd=str(work), env=dict(os.environ))
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 18
                response = await session.call_tool("listmodels", {})
                assert not response.isError
                clients = json.loads(response.content[0].text)["clients"]
                assert len(clients) == 5
                assert all(client["allowed_roots"] == [str(project)] for client in clients)
                assert all(not client["installed"] and not client["available"] for client in clients)
        return {"project": project.name, "clients": 5, "tools": 18, "native_cli_calls": 0}

    print(json.dumps(asyncio.run(_discover_projects(projects, discover)), indent=2))


async def _discover_projects(projects, discover):
    return await asyncio.gather(*(discover(project) for project in projects))


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    evidence = Path(__file__).resolve().parent
    scratch = root / "test_output" / "project-setup-packaging-1"
    scratch.mkdir(parents=True, exist_ok=False)
    work = scratch / "work"
    work.mkdir()
    for name in ("home", "empty-path"):
        (work / name).mkdir()
    python = root / ".venv/bin/python"
    environment = {
        "HOME": str(work / "home"),
        "PATH": f"{root / '.venv/bin'}:/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_NO_INDEX": "1",
        "PIP_CONFIG_FILE": "/dev/null",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
        "LOG_LEVEL": "ERROR",
    }
    sandbox = ["/usr/bin/sandbox-exec", "-f", str(root / "docs/baseline/2026-10-31/offline.sb")]
    commands = []

    def run(label, arguments, cwd=root):
        command = sandbox + [str(argument) for argument in arguments]
        result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=180)
        (evidence / f"{label}.txt").write_text(result.stdout + result.stderr)
        commands.append({"step": label, "argv": command, "exit_code": result.returncode})
        (evidence / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if result.returncode:
            raise RuntimeError(f"{label} failed; see retained evidence")
        return result.stdout

    dist = scratch / "dist"
    run("build", [python, "-m", "build", "--no-isolation", "--outdir", dist])
    distributions = json.loads(run("distribution", [python, root / "scripts/verify_distribution.py", dist]))
    sdist = next(dist.glob("*.tar.gz"))
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert any(name.endswith("/integrations/claude/.claude-plugin/plugin.json") for name in names)
        assert any(name.endswith("/integrations/claude/skills/setup/SKILL.md") for name in names)
    install = scratch / "installed"
    wheel = next(dist.glob("*.whl"))
    run(
        "install",
        [python, "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile", "--target", install, wheel],
    )
    environment["PATH"] = str(work / "empty-path")
    smoke = json.loads(run("installed-smoke", [python, __file__, "--smoke", install, work], cwd=work))
    summary = {
        "status": "passed",
        "distributions": distributions,
        "installed_setup_and_stdio": smoke,
        "plugin_in_sdist": True,
        "native_cli_calls": 0,
        "network_blocked": True,
        "synthetic_home": True,
        "dependencies_reused_without_resolution": True,
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--smoke":
        installed_smoke(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        main()
