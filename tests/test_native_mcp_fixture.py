"""Exercise synthetic MCP stdio and deny unsafe observation-file reads/writes."""

import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import native_mcp_fixture as fixture

SENTINEL_NAME = "NATIVE_FIXTURE_SENTINEL"
SENTINEL_VALUE = "synthetic-native-environment-v1"


def run_server(root, requests, *, inherited=True):
    script = root / "mcp_server.py"
    script.write_text(fixture.MCP_SERVER)
    result = subprocess.run(
        [sys.executable, str(script)],
        input="".join(json.dumps(request) + "\n" for request in requests),
        text=True,
        capture_output=True,
        timeout=3,
        check=True,
        env={SENTINEL_NAME: SENTINEL_VALUE} if inherited else {},
    )
    assert result.stderr == ""
    return [json.loads(line) for line in result.stdout.splitlines()]


def canary_request():
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "canary", "arguments": {"private": "must-not-be-exported"}},
    }


def marker(root):
    return root / "mcp-called.json"


def read_observation_bounded(root):
    code = (
        "from pathlib import Path\n"
        "import sys\n"
        "from scripts.native_mcp_fixture import mcp_environment_observed\n"
        "print(mcp_environment_observed(Path(sys.argv[1])))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(root)],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        timeout=3,
        check=True,
    )
    assert result.stderr == ""
    assert result.stdout.strip() in {"True", "False"}
    return result.stdout.strip() == "True"


@pytest.mark.parametrize("inherited", [True, False])
def test_complete_stdio_protocol_and_synthetic_environment_canary(tmp_path, inherited):
    messages = run_server(
        tmp_path,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            canary_request(),
            {"jsonrpc": "2.0", "id": 3, "method": "unknown"},
        ],
        inherited=inherited,
    )
    assert messages == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "native-fixture", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "canary",
                        "description": "Verify synthetic inherited environment.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                ]
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {
                "content": [{"type": "text", "text": "MCP_CANARY_OK" if inherited else "MCP_CANARY_FAILED"}],
                "isError": not inherited,
            },
        },
        {"jsonrpc": "2.0", "id": 3, "result": {}},
    ]
    assert json.loads(marker(tmp_path).read_text()) == {"inherited_sentinel_matches": inherited}
    assert fixture.mcp_environment_observed(tmp_path) is inherited
    assert stat.S_IMODE(marker(tmp_path).stat().st_mode) == 0o600
    serialized = json.dumps(messages) + marker(tmp_path).read_text()
    assert SENTINEL_VALUE not in serialized
    assert "must-not-be-exported" not in serialized


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "group_writable"])
def test_unsafe_marker_cannot_truncate_external_files_or_block_stdio(tmp_path, kind):
    root = tmp_path / "fixture"
    root.mkdir()
    external = tmp_path / "external-sentinel"
    original = b"private external sentinel must survive\n"
    external.write_bytes(original)
    if kind == "symlink":
        marker(root).symlink_to(external)
    elif kind == "hardlink":
        os.link(external, marker(root))
    elif kind == "fifo":
        os.mkfifo(marker(root))
    elif kind == "directory":
        marker(root).mkdir()
    else:
        marker(root).write_bytes(b"private existing marker\n")
        marker(root).chmod(0o620)
    result = run_server(root, [canary_request()])[0]
    assert result["result"] == {"content": [{"type": "text", "text": "MCP_CANARY_FAILED"}], "isError": True}
    assert external.read_bytes() == original
    assert read_observation_bounded(root) is False
    if kind == "group_writable":
        assert marker(root).read_bytes() == b"private existing marker\n"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory"])
def test_observation_reader_never_follows_links_or_blocks_on_special_files(tmp_path, kind):
    external = tmp_path / "external-positive-marker"
    external.write_text('{"inherited_sentinel_matches": true}')
    root = tmp_path / "fixture"
    root.mkdir()
    if kind == "symlink":
        marker(root).symlink_to(external)
    elif kind == "hardlink":
        os.link(external, marker(root))
    elif kind == "fifo":
        os.mkfifo(marker(root))
    else:
        marker(root).mkdir()
    assert read_observation_bounded(root) is False
    assert external.read_text() == '{"inherited_sentinel_matches": true}'


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b'{"inherited_sentinel_matches": true}', True),
        (b'{"inherited_sentinel_matches": false}', False),
        (b'{"inherited_sentinel_matches": 1}', False),
        (b'{"inherited_sentinel_matches": "true"}', False),
        (b'{"inherited_sentinel_matches": null}', False),
        (b'{"inherited_sentinel_matches": false, "inherited_sentinel_matches": true}', False),
        (b'{"inherited_sentinel_matches": true, "private": "unrelated"}', False),
        (b"[true]", False),
        (b"private malformed JSON", False),
        (b"\xff", False),
        (b" " * (fixture.MAX_OBSERVATION_BYTES + 1), False),
    ],
)
def test_observation_is_bounded_and_strictly_boolean(tmp_path, raw, expected):
    marker(tmp_path).write_bytes(raw)
    assert fixture.mcp_environment_observed(tmp_path) is expected
    assert marker(tmp_path).read_bytes() == raw


def test_missing_observation_is_false(tmp_path):
    assert fixture.mcp_environment_observed(tmp_path) is False
    assert not marker(tmp_path).exists()


def test_owned_regular_marker_is_replaced_without_trailing_old_bytes(tmp_path):
    marker(tmp_path).write_text("old observation" * 100)
    run_server(tmp_path, [canary_request()], inherited=False)
    assert marker(tmp_path).read_text() == '{"inherited_sentinel_matches": false}'


def test_foreign_owned_marker_is_neither_read_nor_truncated(tmp_path, monkeypatch):
    target = marker(tmp_path)
    original = b'{"inherited_sentinel_matches": true}'
    target.write_bytes(original)
    inode = target.stat().st_ino
    original_fstat = os.fstat

    def foreign_fstat(fd):
        info = original_fstat(fd)
        if info.st_ino == inode:
            fields = list(info)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "fstat", foreign_fstat)
    assert fixture.mcp_environment_observed(tmp_path) is False
    namespace = {"__file__": str(tmp_path / "mcp_server.py")}
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO()))
    exec(compile(fixture.MCP_SERVER, namespace["__file__"], "exec"), namespace)
    assert namespace["write_observation"](True) is False
    assert target.read_bytes() == original


def test_fifo_with_reader_still_cannot_receive_observation_bytes(tmp_path):
    target = marker(tmp_path)
    os.mkfifo(target)
    fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
    try:
        response = run_server(tmp_path, [canary_request()])[0]
        assert response["result"]["isError"] is True
        assert os.read(fd, 4096) == b""
    finally:
        os.close(fd)
