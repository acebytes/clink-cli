"""Share synthetic MCP canaries with bounded, no-follow observation-file access."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

MAX_OBSERVATION_BYTES = 4096

MCP_SERVER = '''"""Synthetic stdio MCP server; marker files never follow external links."""
import json
import os
import pathlib
import stat
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAXIMUM_INPUT = 1024 * 1024

def write_observation(matched):
    encoded = json.dumps({"inherited_sentinel_matches": matched}).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(ROOT / "mcp-called.json", flags, 0o600)
        with os.fdopen(fd, "wb") as output:
            info = os.fstat(output.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                return False
            if info.st_mode & 0o022:
                return False
            output.truncate(0)
            output.write(encoded)
        return True
    except OSError:
        return False

while True:
    line = sys.stdin.buffer.readline(MAXIMUM_INPUT + 1)
    if not line:
        break
    if len(line) > MAXIMUM_INPUT:
        break
    try:
        request = json.loads(line)
    except (ValueError, UnicodeError, RecursionError):
        continue
    if not isinstance(request, dict) or "id" not in request:
        continue
    identity = request["id"]
    if isinstance(identity, bool) or not isinstance(identity, (int, str)):
        continue
    method = request.get("method")
    params = request.get("params")
    params = params if isinstance(params, dict) else {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}}, "serverInfo": {"name": "native-fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "canary", "description": "Verify synthetic inherited environment.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
    elif method == "tools/call" and params.get("name") == "canary":
        matched = os.environ.get("NATIVE_FIXTURE_SENTINEL") == "synthetic-native-environment-v1"
        observed = write_observation(matched)
        successful = matched and observed
        result = {"content": [{"type": "text", "text": "MCP_CANARY_OK" if successful else "MCP_CANARY_FAILED"}],
            "isError": not successful}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": identity, "result": result}), flush=True)
'''


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate synthetic observation field")
        result[name] = value
    return result


def mcp_environment_observed(root: Path) -> bool:
    """Read only the bounded, owner-owned regular canary and return a strict boolean."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(root / "mcp-called.json", flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                return False
            if info.st_mode & 0o022:
                return False
            raw = stream.read(MAX_OBSERVATION_BYTES + 1)
        if len(raw) > MAX_OBSERVATION_BYTES:
            return False
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return False
    return (
        isinstance(value, dict)
        and set(value) == {"inherited_sentinel_matches"}
        and value["inherited_sentinel_matches"] is True
    )
