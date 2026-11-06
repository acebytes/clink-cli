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


def isolation_policy(root: Path, user_home: Path, interpreter_root: Path) -> str:
    """Bind Seatbelt isolation to this checkout and interpreter without host-specific paths."""

    def quoted(path: Path) -> str:
        return json.dumps(str(path.resolve()))

    return "\n".join(
        (
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            f"(deny file-read* (subpath {quoted(user_home)}))",
            f"(allow file-read-metadata (subpath {quoted(user_home)}))",
            f"(allow file-read* (subpath {quoted(root)}) (subpath {quoted(interpreter_root)}))",
            f"(deny file-write* (subpath {quoted(user_home)}))",
            f"(allow file-write* (subpath {quoted(root)}))",
            "",
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="Unique evidence directory name")
    parser.add_argument("tests", nargs="*", default=["tests/"])
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="Development Python interpreter")
    parser.add_argument("--output-dir", type=Path, help="Durable ignored evidence parent; default: test_output/offline")
    args = parser.parse_args()
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("Use only letters, numbers, underscores and hyphens in the label")
    root = Path(__file__).resolve().parent.parent
    evidence = (args.output_dir or root / "test_output/offline") / args.label
    evidence.mkdir(parents=True, exist_ok=False)
    if sys.platform != "darwin":
        raise SystemExit(
            "This isolation harness requires macOS Seatbelt; use an equivalent isolated container on Linux."
        )
    environment = {
        "PATH": f"{args.python.absolute().parent}:/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "LANG": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
    }
    policy_path = evidence / "offline.sb"
    policy_path.write_text(isolation_policy(root, Path.home(), Path(sys.prefix)))
    prefix = [
        "/usr/bin/sandbox-exec",
        "-f",
        str(policy_path),
        str(args.python.absolute()),
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
 next(Path.home().iterdir(),None)
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
