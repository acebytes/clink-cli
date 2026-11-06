"""Capability CLI never reads consumer configuration or starts a native binary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_native_generation_v3 import wire

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("cli,status", [(None, None), ("grok", "invalid_executable"), ("agy", "admission_unverified")])
def test_capability_cli_is_bounded_and_read_only_before_consumer_setup(tmp_path, cli, status):
    marker = tmp_path / "must-not-run"
    executable = tmp_path / "synthetic-native"
    executable.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n")
    executable.chmod(0o700)
    args = [
        sys.executable,
        "-m",
        "clink.cli",
        "--consumer-config-dir",
        "missing-relative-config",
        "generation-capabilities",
        "--json",
    ]
    if cli is not None:
        args.extend(["--cli", cli, "--executable", str(executable)])
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env={**os.environ, "CLI_CLIENTS_CONFIG_PATH": "/missing/config"},
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0 and completed.stderr == b""
    assert len(completed.stdout) <= 65536 and len(completed.stdout.splitlines()) == 1
    result = json.loads(completed.stdout)
    assert set(result) == {"schema_version", "generation_schemas", "families", "preflight"}
    assert [row["generation_schema"] for row in result["families"]] == [2, 2, 3, 3]
    admissions = {row["id"]: row["admission"]["status"] for row in result["families"]}
    assert admissions["grok"] == "verified" and admissions["agy"] == "unverified"
    if cli is not None:
        assert result["preflight"]["cli"] == cli and result["preflight"]["status"] == status
    else:
        assert result["preflight"] is None
    assert not marker.exists()
    assert set(tmp_path.iterdir()) == {executable}


def test_invalid_schema_three_selection_returns_schema_three_before_configuration(tmp_path):
    body = wire(tmp_path, selection={"kind": "native_default", "id": "PRIVATE_BAD_MODEL"})
    completed = subprocess.run(
        [sys.executable, "-m", "clink.cli", "generate", "--request-json", "-"],
        cwd=ROOT,
        input=json.dumps(body).encode(),
        capture_output=True,
        timeout=10,
        env={**os.environ, "CLI_CLIENTS_CONFIG_PATH": "/missing/config"},
    )
    assert completed.returncode == 2 and completed.stderr == b""
    result = json.loads(completed.stdout)
    assert result["schema_version"] == 3 and result["execution"]["model_selection"] is None
    assert result["error"]["code"] == "invalid_request"
    assert b"PRIVATE_BAD_MODEL" not in completed.stdout
