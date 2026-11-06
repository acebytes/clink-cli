"""Verify CLI package identity, dependency parity, and installed entrypoint."""

import json
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def project_metadata():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_package_has_clink_identity_entrypoint_and_supported_python_floor():
    from config import __version__

    project = project_metadata()
    assert project["name"] == "clink-cli"
    assert project["version"] == __version__ == "0.2.1"
    assert project["requires-python"] == ">=3.10"
    assert project["scripts"] == {"clink-mcp-server": "server:run", "clink": "clink.cli:main"}


def test_dependencies_match_minimal_requirements_exactly():
    requirements = {
        line.strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert (
        set(project_metadata()["dependencies"])
        == requirements
        == {"mcp>=1.0.0,<2", "pydantic>=2.0.0", "python-dotenv>=1.0.0"}
    )


def test_entrypoint_imports_from_arbitrary_cwd_without_keys_or_cli(tmp_path):
    environment = {
        "PATH": str(tmp_path),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import json,server; print(json.dumps({'callable':callable(server.run)}))"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"callable": True}


def test_optional_dotenv_fallback_preserves_runtime_environment(tmp_path):
    code = """import builtins, json, os
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "dotenv":
        raise ImportError("unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import utils.env
print(json.dumps({"value": utils.env.get_env("CLINK_SYNTHETIC_TEST_VALUE")}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={"PYTHONPATH": str(ROOT), "HOME": str(tmp_path), "CLINK_SYNTHETIC_TEST_VALUE": "retained"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"value": "retained"}
