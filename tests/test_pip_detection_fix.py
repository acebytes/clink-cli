"""The launcher only installs on explicit setup and never mutates native configuration."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace with spaces"
    root.mkdir()
    shutil.copy2(Path(__file__).parents[1] / "run-server.sh", root / "run-server.sh")
    (root / "server.py").write_text("# Synthetic server, never executed.\n")
    (root / "requirements.txt").write_text("mcp>=1,<2\n")
    return root


def python_stub(workspace, probe_status=0):
    executable = workspace / ".venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then\n'
        f'  case "$2" in *find_spec*) exit {probe_status};; *) exit 0;; esac\n'
        "fi\n"
        'printf "cwd=%s\\n" "$PWD"\n'
        'printf "arg=%s\\n" "$@"\n'
    )
    executable.chmod(0o700)


def run(workspace, *args):
    return subprocess.run(
        ["/bin/sh", str(workspace / "run-server.sh"), *args],
        cwd=workspace.parent,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"]},
        capture_output=True,
        text=True,
        check=False,
    )


def test_run_server_script_syntax_valid():
    result = subprocess.run(["/bin/sh", "-n", "run-server.sh"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_help_requires_no_environment_and_creates_nothing(workspace):
    before = sorted(path.name for path in workspace.iterdir())
    result = run(workspace, "--help")
    assert result.returncode == 0
    assert "--setup" in result.stdout
    assert before == sorted(path.name for path in workspace.iterdir())


def test_missing_virtualenv_does_not_install(workspace):
    result = run(workspace)
    assert result.returncode != 0
    assert "--setup" in result.stderr
    assert not (workspace / ".venv").exists()
    assert not (workspace / ".env").exists()


def test_ordinary_start_uses_absolute_python_and_repo_cwd(workspace):
    python_stub(workspace)
    result = run(workspace)
    assert result.returncode == 0, result.stderr
    assert f"cwd={workspace}" in result.stdout
    assert f"arg={workspace / 'server.py'}" in result.stdout
    assert "pip" not in result.stdout
    assert not (workspace / ".env").exists()


def test_missing_dependencies_do_not_trigger_install(workspace):
    python_stub(workspace, probe_status=1)
    result = run(workspace)
    assert result.returncode != 0
    assert "--setup" in result.stderr
    assert "arg=" not in result.stdout


def test_explicit_setup_installs_through_the_selected_python(workspace):
    python_stub(workspace)
    result = run(workspace, "--setup")
    assert result.returncode == 0, result.stderr
    assert "arg=-m\narg=pip\narg=install" in result.stderr
    assert f"arg={workspace / 'requirements.txt'}" in result.stderr
    assert not (workspace / ".env").exists()


def test_legacy_options_fail_with_usage_without_mutation(workspace):
    result = run(workspace, "--configure")
    assert result.returncode == 2
    assert "--setup" in result.stderr
    assert not (workspace / ".venv").exists()
