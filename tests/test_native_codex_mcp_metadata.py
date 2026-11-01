"""Verify Codex canary metadata against its synthetic local side effects."""

import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

from scripts import native_codex_fixture as codex
from scripts import native_mcp_fixture as mcp

ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
SHARED_PROGRAM_SHA256 = "2424778ea57b8a9f8c3ef42b417074801a0ba4ac499c04df01d6a93aeca3cdcd"
SENTINEL_NAME = "NATIVE_FIXTURE_SENTINEL"
SENTINEL_VALUE = "synthetic-native-environment-v1"


@pytest.fixture
def project(tmp_path):
    root = (tmp_path / "synthetic Codex MCP fixture").resolve()
    root.mkdir()
    (root / ".clink-native-verification-fixture").write_text("synthetic-native-adapter-fixture-v1\n")
    codex.create_codex_fixture(root)
    return root


def run_peer(root, requests, *, sentinel=None, program=None):
    script = root / "codex_mcp_server.py"
    if program is not None:
        script = root / "shared_mcp_server.py"
        script.write_text(program)
    environment = {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if sentinel is not None:
        environment[SENTINEL_NAME] = sentinel
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        env=environment,
        input="".join(json.dumps(request) + "\n" for request in requests),
        text=True,
        capture_output=True,
        timeout=3,
        check=True,
    )
    assert result.stderr == ""
    assert len(result.stdout.encode()) < 8192
    return [json.loads(line) for line in result.stdout.splitlines()]


def list_request():
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def canary_request():
    return {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "canary", "arguments": {"private": "PRIVATE_SYNTHETIC_ARGUMENT"}},
    }


def test_codex_tool_metadata_describes_the_local_marker_write_and_closed_world(project):
    messages = run_peer(
        project,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            list_request(),
        ],
    )
    assert [message["id"] for message in messages] == [1, 2]
    assert messages[-1]["result"]["tools"] == [
        {
            "name": "canary",
            "description": "Verify synthetic inherited environment.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": ANNOTATIONS,
        }
    ]
    assert not (project / "mcp-called.json").exists()
    assert (project / "codex_mcp_server.py").read_text() == mcp.mcp_server_program(
        tool_annotations=ANNOTATIONS, create_only_observation=True
    )


@pytest.mark.parametrize("sentinel", [SENTINEL_VALUE, None, "wrong-synthetic-sentinel"])
def test_codex_canary_requires_matching_inherited_sentinel_and_writes_only_boolean_proof(project, sentinel):
    messages = run_peer(project, [canary_request()], sentinel=sentinel)
    matched = sentinel == SENTINEL_VALUE
    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "content": [{"type": "text", "text": "MCP_CANARY_OK" if matched else "MCP_CANARY_FAILED"}],
                "isError": not matched,
            },
        }
    ]
    marker = project / "mcp-called.json"
    assert json.loads(marker.read_bytes()) == {"inherited_sentinel_matches": matched}
    assert marker.stat().st_size <= mcp.MAX_OBSERVATION_BYTES
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert mcp.mcp_environment_observed(project) is matched
    exported = json.dumps(messages) + marker.read_text()
    assert SENTINEL_VALUE not in exported
    assert "wrong-synthetic-sentinel" not in exported
    assert "PRIVATE_SYNTHETIC_ARGUMENT" not in exported


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_codex_canary_refuses_linked_observation_files_without_touching_external_bytes(project, tmp_path, kind):
    external = tmp_path / "synthetic-external-sentinel"
    original = b"PRIVATE_SYNTHETIC_EXTERNAL_BYTES\n"
    external.write_bytes(original)
    external.chmod(0o600)
    marker = project / "mcp-called.json"
    if kind == "symlink":
        marker.symlink_to(external)
    else:
        os.link(external, marker)
    messages = run_peer(project, [canary_request()], sentinel=SENTINEL_VALUE)
    assert messages[0]["result"] == {
        "content": [{"type": "text", "text": "MCP_CANARY_FAILED"}],
        "isError": True,
    }
    assert external.read_bytes() == original
    assert mcp.mcp_environment_observed(project) is False


@pytest.mark.parametrize(
    "original",
    [
        b"PRIVATE_SYNTHETIC_EXISTING_OBSERVATION\n",
        b'{"inherited_sentinel_matches": true}',
        b'{"inherited_sentinel_matches": false}',
    ],
)
def test_codex_canary_refuses_existing_owned_regular_observation_without_replacement(project, original):
    marker = project / "mcp-called.json"
    marker.write_bytes(original)
    marker.chmod(0o600)
    before = marker.stat()
    messages = run_peer(project, [canary_request()], sentinel=SENTINEL_VALUE)
    assert messages[0]["result"] == {
        "content": [{"type": "text", "text": "MCP_CANARY_FAILED"}],
        "isError": True,
    }
    assert marker.read_bytes() == original
    after = marker.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)


def test_codex_canary_creates_once_and_repeat_call_fails_without_persistent_changes(project):
    first = run_peer(project, [canary_request()], sentinel=SENTINEL_VALUE)
    assert first[0]["result"]["isError"] is False
    marker = project / "mcp-called.json"
    original = marker.read_bytes()
    before = marker.stat()
    repeated = run_peer(project, [canary_request()], sentinel=SENTINEL_VALUE)
    assert repeated[0]["result"] == {
        "content": [{"type": "text", "text": "MCP_CANARY_FAILED"}],
        "isError": True,
    }
    assert marker.read_bytes() == original
    after = marker.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)
    assert mcp.mcp_environment_observed(project) is True


def test_shared_default_program_bytes_and_unannotated_tool_contract_remain_unchanged(tmp_path):
    program = mcp.mcp_server_program()
    assert program == mcp.MCP_SERVER
    assert mcp.mcp_server_program(create_only_observation=False) == mcp.MCP_SERVER
    assert hashlib.sha256(program.encode()).hexdigest() == SHARED_PROGRAM_SHA256
    messages = run_peer(tmp_path, [list_request()], program=program)
    assert messages[0]["result"]["tools"] == [
        {
            "name": "canary",
            "description": "Verify synthetic inherited environment.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    ]
    assert not (tmp_path / "mcp-called.json").exists()


@pytest.mark.parametrize(
    "annotations",
    [
        {},
        {**ANNOTATIONS, "unsupportedHint": False},
        {**ANNOTATIONS, "readOnlyHint": 0},
        {**ANNOTATIONS, "readOnlyHint": True},
    ],
)
def test_program_factory_rejects_incomplete_unknown_or_untruthful_annotations(annotations):
    with pytest.raises(ValueError):
        mcp.mcp_server_program(tool_annotations=annotations, create_only_observation=True)


@pytest.mark.parametrize("create_only", [None, 1])
def test_program_factory_rejects_nonboolean_observation_mode(create_only):
    with pytest.raises(ValueError):
        mcp.mcp_server_program(tool_annotations=ANNOTATIONS, create_only_observation=create_only)


def test_program_factory_refuses_nondestructive_metadata_for_a_replacing_observation_writer():
    with pytest.raises(ValueError):
        mcp.mcp_server_program(tool_annotations=ANNOTATIONS, create_only_observation=False)
