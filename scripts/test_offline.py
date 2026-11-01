"""Run tests with a clean environment and macOS network/home-file isolation.

Evidence is immutable per label. This does not launch installed model CLIs.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="Unique evidence directory name")
    parser.add_argument("tests", nargs="*", default=["tests/"])
    args = parser.parse_args()
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("Use only letters, numbers, underscores and hyphens in the label")
    root = Path(__file__).resolve().parent.parent
    evidence = root / "docs" / "verification" / args.label
    evidence.mkdir(parents=True, exist_ok=False)
    if sys.platform != "darwin":
        raise SystemExit(
            "This isolation harness requires macOS Seatbelt; use an equivalent isolated container on Linux."
        )
    environment = {
        "PATH": f"{root / '.venv/bin'}:/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "LANG": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
    }
    prefix = [
        "/usr/bin/sandbox-exec",
        "-f",
        str(root / "docs/baseline/2026-10-31/offline.sb"),
        str(root / ".venv/bin/python"),
    ]
    probe = """import socket,errno
from pathlib import Path
try:
 s=socket.socket();s.connect(('127.0.0.1',9))
except OSError as e:
 assert e.errno in (errno.EPERM,errno.EACCES)
else:
 raise AssertionError('network not denied')
try:
 Path('/Users/d/Tools/pal-mcp-server/README.md').read_bytes()
except PermissionError:
 pass
else:
 raise AssertionError('home-file isolation absent')
print('Network and unrelated home reads denied')
"""
    preflight = subprocess.run(prefix + ["-c", probe], cwd=root, env=environment, capture_output=True, text=True)
    (evidence / "isolation.txt").write_text(preflight.stdout + preflight.stderr)
    if preflight.returncode:
        raise SystemExit("Isolation preflight failed; see evidence")
    command = prefix + [
        "-m",
        "pytest",
        *args.tests,
        "-v",
        "-m",
        "not integration",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "pytest_mock",
        f"--junitxml={evidence / 'junit.xml'}",
    ]
    start = time.monotonic()
    with (evidence / "pytest.txt").open("w") as stream:
        result = subprocess.run(command, cwd=root, env=environment, stdout=stream, stderr=subprocess.STDOUT)
    summary = {
        "command": command,
        "cwd": str(root),
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - start, 3),
        "environment_keys": sorted(environment),
        "network_blocked": True,
        "unrelated_home_reads_denied": True,
    }
    (evidence / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = (evidence / "pytest.txt").read_text().splitlines()
    print("\n".join(lines[-10:]))
    print(f"Evidence: {evidence}")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
