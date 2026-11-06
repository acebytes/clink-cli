"""Run tests with a clean environment and macOS network/home-file isolation.

Evidence is immutable per label. This does not launch installed model CLIs.
"""

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def interpreter_read_roots(executable: Path, environment: dict[str, str]) -> tuple[Path, ...]:
    """Locate the selected Python's own environment and base runtime, never the harness's."""
    selected = executable.expanduser().absolute()
    probe = subprocess.run(
        [
            str(selected),
            "-I",
            "-S",
            "-c",
            "import json,sys; print(json.dumps({'base_prefix':sys.base_prefix}))",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    if len(probe.stdout) > 16384:
        raise ValueError("Selected Python metadata exceeds its bound")
    metadata = json.loads(probe.stdout)
    if not isinstance(metadata, dict) or set(metadata) != {"base_prefix"}:
        raise ValueError("Selected Python must report its base runtime")
    roots = []
    user_home = Path(environment["HOME"]).resolve(strict=True)
    selected_bin = selected.parent.resolve(strict=True)
    configuration = selected_bin.parent / "pyvenv.cfg"
    if selected_bin.name == "bin" and (configuration.exists() or configuration.is_symlink()):
        if not stat.S_ISREG(configuration.lstat().st_mode):
            raise ValueError("Selected Python environment needs a regular pyvenv.cfg")
        roots.append(selected_bin.parent)
    roots.append(metadata["base_prefix"])
    canonical_roots = []
    for value in roots:
        value = str(value) if isinstance(value, Path) else value
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("Selected Python roots must be absolute directories")
        path = Path(value).resolve(strict=True)
        if not path.is_dir() or path == user_home or path in user_home.parents:
            raise ValueError("Selected Python must not grant a broad home ancestor")
        canonical_roots.append(path)
    base_prefix = canonical_roots[-1]
    if base_prefix.is_relative_to(user_home) and not selected.resolve(strict=True).is_relative_to(base_prefix):
        raise ValueError("Selected Python base runtime must not grant an unrelated home directory")
    return tuple(dict.fromkeys(canonical_roots))


def isolation_policy(root: Path, user_home: Path, interpreter_roots: tuple[Path, ...]) -> str:
    """Bind Seatbelt isolation to this checkout and interpreter without host-specific paths."""

    def quoted(path: Path) -> str:
        return json.dumps(str(path.resolve()))

    read_roots = " ".join(f"(subpath {quoted(path)})" for path in dict.fromkeys((root, *interpreter_roots)))
    return "\n".join(
        (
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            f"(deny file-read* (subpath {quoted(user_home)}))",
            f"(allow file-read-metadata (subpath {quoted(user_home)}))",
            f"(allow file-read* {read_roots})",
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
    try:
        interpreter_roots = interpreter_read_roots(args.python, environment)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error("Selected Python isolation metadata refused: " + type(error).__name__)
    policy_path = evidence / "offline.sb"
    policy_path.write_text(isolation_policy(root, Path.home(), interpreter_roots))
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
        "not integration and not real_cli and not private_evidence",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "pytest_mock",
        f"--junitxml={evidence / 'junit.xml'}",
    ]
    start = time.monotonic()
    with (
        tempfile.TemporaryDirectory(prefix="clink-offline-home-") as test_home,
        (evidence / "pytest.txt").open("w") as stream,
    ):
        test_env = {**environment, "HOME": test_home}
        result = subprocess.run(command, cwd=root, env=test_env, stdout=stream, stderr=subprocess.STDOUT)
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
