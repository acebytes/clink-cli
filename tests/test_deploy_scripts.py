"""Run the retained Docker helpers against a recording executable."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def helper_checkout(tmp_path):
    project = tmp_path / "checkout with spaces"
    scripts = project / "docker/scripts"
    scripts.mkdir(parents=True)
    for name in ("build.sh", "deploy.sh"):
        shutil.copyfile(ROOT / "docker/scripts" / name, scripts / name)
    (project / "docker-compose.yml").write_text("services: {}\n")
    # Loading this file as shell code would fail and leave an observable marker.
    (project / ".env").write_text("touch sourced-env-marker; exit 99\n")
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    recorder = executable_dir / "docker"
    recorder.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORDED_ARGS"\n'
        'if [ "$1" = compose ] && [ "$6" = run ]; then cat; fi\n'
        'exit "${FAKE_DOCKER_STATUS:-0}"\n'
    )
    recorder.chmod(0o755)
    environment = {
        "PATH": str(executable_dir) + os.pathsep + "/usr/bin:/bin",
        "RECORDED_ARGS": str(tmp_path / "argv.txt"),
    }
    return project, environment


@pytest.mark.parametrize(
    "helper,tail",
    [("build.sh", ["build", "clink-mcp"]), ("deploy.sh", ["run", "--rm", "--no-deps", "-T", "clink-mcp"])],
)
def test_helpers_preserve_project_paths_and_do_not_load_env(helper_checkout, helper, tail, tmp_path):
    project, environment = helper_checkout
    protocol = json.dumps({"jsonrpc": "2.0", "id": 1}) + "\n"
    result = subprocess.run(
        ["/bin/sh", str(project / "docker/scripts" / helper)],
        cwd=tmp_path,
        env=environment,
        input=protocol,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    argv = Path(environment["RECORDED_ARGS"]).read_text().splitlines()
    assert argv == ["compose", "--project-directory", str(project), "-f", str(project / "docker-compose.yml"), *tail]
    assert result.stdout == (protocol if helper == "deploy.sh" else "")
    assert not (project / "sourced-env-marker").exists()
    assert not (tmp_path / "sourced-env-marker").exists()
    assert not (project / "logs").exists()


@pytest.mark.parametrize("helper", ["build.sh", "deploy.sh"])
def test_helpers_propagate_docker_exit_status_without_retries(helper_checkout, helper, tmp_path):
    project, environment = helper_checkout
    environment["FAKE_DOCKER_STATUS"] = "42"
    result = subprocess.run(
        ["/bin/sh", str(project / "docker/scripts" / helper)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 42


@pytest.mark.parametrize("helper", ["build.ps1", "deploy.ps1"])
def test_powershell_helpers_keep_the_same_stdio_contract(helper):
    text = (ROOT / "docker/scripts" / helper).read_text()
    assert "& docker compose --project-directory $ProjectDir" in text
    assert "exit $LASTEXITCODE" in text
    for obsolete in (".env", "API_KEY", "up -d", "Write-Host", "Write-Output", "Start-Sleep", "& docker-compose "):
        assert obsolete not in text.replace(
            "# Build locally without reading .env or changing native CLI authentication.", ""
        )
    if helper == "deploy.ps1":
        assert "run --rm --no-deps -T clink-mcp" in text
