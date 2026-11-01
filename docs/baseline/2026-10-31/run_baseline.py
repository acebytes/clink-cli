"""Reproduce the credential-isolated, network-blocked macOS audit baseline.

This is audit tooling, not part of the MCP server or its test suite. The adjacent
Seatbelt profile is specific to the audited checkout. Run from the repo root.
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label", choices=("pristine", "mcp1", "mcp1-home"))
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parent
    root = evidence.parents[2]
    python = root / ".venv/bin/python"
    command_prefix = ["/usr/bin/sandbox-exec", "-f", str(evidence / "offline.sb"), str(python)]
    # No inherited credentials, provider URLs, user-controlled Python paths, or
    # package/plugin environment. Do not repoint HOME or copy login material.
    environment = {
        "PATH": f"{root / '.venv/bin'}:/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
    }
    if args.label == "mcp1-home":
        # Preserve the existing HOME value for scripts that require it. The
        # Seatbelt profile still denies reading credentials/startup files there.
        environment["HOME"] = os.environ["HOME"]
    assert not (root / ".env").exists(), "Do not baseline a credential-bearing checkout"
    probe = """
import errno, json, socket
from pathlib import Path
checks = {}
try:
    with socket.socket() as s:
        s.connect(('127.0.0.1', 9))
except OSError as exc:
    checks['network_denied'] = exc.errno in (errno.EPERM, errno.EACCES)
else:
    checks['network_denied'] = False
try:
    Path('/Users/d/Tools/pal-mcp-server/README.md').read_bytes()
except PermissionError:
    checks['outside_home_read_denied'] = True
else:
    checks['outside_home_read_denied'] = False
checks['checkout_readable'] = Path('server.py').is_file()
print(json.dumps(checks, sort_keys=True))
raise SystemExit(0 if all(checks.values()) else 1)
"""
    preflight = subprocess.run(
        command_prefix + ["-c", probe],
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    (evidence / f"{args.label}-isolation.txt").write_text(preflight.stdout)
    if preflight.returncode:
        raise SystemExit(f"Isolation preflight failed: {preflight.stdout}")

    packages = subprocess.run(
        [str(python), "-m", "pip", "freeze", "--all"],
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    (evidence / f"{args.label}-packages.txt").write_text(packages.stdout)
    command = command_prefix + [
        "-m",
        "pytest",
        "tests/",
        "-v",
        "-m",
        "not integration",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "pytest_mock",
        f"--junitxml={evidence / (args.label + '-junit.xml')}",
    ]
    start = time.monotonic()
    with (evidence / f"{args.label}-pytest.txt").open("w") as output:
        result = subprocess.run(command, cwd=root, env=environment, stdout=output, stderr=subprocess.STDOUT)
    summary = {
        "label": args.label,
        "command": command,
        "cwd": str(root),
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - start, 3),
        "environment_keys": sorted(environment),
        "python": str(python),
        "network_blocked": True,
        "home_reads_restricted_to_checkout": True,
        "plugin_autoload": False,
        "plugins": ["pytest_asyncio.plugin", "pytest_mock"],
    }
    (evidence / f"{args.label}-run.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
