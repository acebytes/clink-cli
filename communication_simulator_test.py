"""Compatibility entry point for offline MCP communication contract tests.

Provider-backed PAL simulations were retired after the CLI replacement paths
passed; native inference is an explicit opt-in through scripts/smoke_cli.py.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()
    tests = ["tests/test_cli_server.py", "tests/test_cli_stdio.py"]
    if not arguments.quick:
        tests.append("tests/test_consensus_integration.py")
    return subprocess.call(
        [sys.executable, "-m", "pytest", *tests, "-v" if arguments.verbose else "-q"],
        cwd=Path(__file__).resolve().parent,
    )


if __name__ == "__main__":
    raise SystemExit(main())
