#!/usr/bin/env python3
"""Check container process/import/config liveness without CLI or network calls.

This is not an MCP round trip or a CLI authentication/readiness check. Docker's
stdio service owns stdin, so the probe never reads or writes that stream.
"""

import importlib
import sys
from pathlib import Path


def check_process(proc_root: Path = Path("/proc")) -> bool:
    """Recognize the exec-form server command at PID 1 on Linux."""
    try:
        argv = (proc_root / "1" / "cmdline").read_bytes().split(b"\0")
    except OSError:
        print("Server process is unavailable", file=sys.stderr)
        return False
    if any(Path(argument.decode(errors="replace")).name in {"clink-mcp-server", "server.py"} for argument in argv):
        return True
    print("PID 1 is not the clink MCP server", file=sys.stderr)
    return False


def check_python_imports() -> bool:
    """Import only the provider-free runtime dependencies."""
    for module in ("mcp", "pydantic", "dotenv"):
        try:
            importlib.import_module(module)
        except Exception as exc:
            print(f"Runtime import failed: {module} ({type(exc).__name__})", file=sys.stderr)
            return False
    return True


def check_configuration() -> bool:
    """Validate registry resources while allowing absent CLI binaries/auth."""
    try:
        from clink.registry import ClinkRegistry

        ClinkRegistry()
    except Exception as exc:
        print(f"CLI manifest validation failed ({type(exc).__name__})", file=sys.stderr)
        return False
    return True


def main() -> int:
    checks = (check_process(), check_python_imports(), check_configuration())
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
