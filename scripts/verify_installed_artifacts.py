"""Install and smoke-test each wheel/sdist offline in a separate clean environment.

Supply a predownloaded wheelhouse containing runtime dependencies, setuptools
80.9.0 and wheel 0.45.1. Network isolation is supplied by the CI container.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

BUILD_TOOLS = ("setuptools==80.9.0", "wheel==0.45.1")


def verify(directory: Path, wheelhouse: Path, source_root: Path) -> list[dict]:
    directory, wheelhouse, source_root = directory.resolve(), wheelhouse.resolve(), source_root.resolve()
    wheels, sources = sorted(directory.glob("*.whl")), sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution")
    if not wheelhouse.is_dir() or not any(wheelhouse.glob("*.whl")):
        raise ValueError("Provide a populated offline dependency wheelhouse")
    helper = Path(__file__).with_name("smoke_installed.py").resolve()
    reports = []
    for artifact in wheels + sources:
        with tempfile.TemporaryDirectory(prefix="clink-artifact-install-") as temporary:
            work = Path(temporary).resolve()
            if work.is_relative_to(source_root):
                raise ValueError("Artifact installation must run outside the checkout; choose an external TMPDIR")
            prefix = work / "venv"
            venv.EnvBuilder(with_pip=True, clear=True).create(prefix)
            python = prefix / "bin" / "python"
            home = work / "home"
            home.mkdir()
            environment = {
                "PATH": str(prefix / "bin"),
                "HOME": str(home),
                "TMPDIR": str(work),
                "LANG": "C.UTF-8",
                "CI": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PIP_CONFIG_FILE": os.devnull,
                "PIP_NO_INDEX": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
            }
            install = [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-cache-dir",
                "--find-links",
                str(wheelhouse),
                "--no-build-isolation",
            ]
            for packages in (list(BUILD_TOOLS), [str(artifact)]):
                subprocess.run(
                    install + packages, cwd=work, env=environment, check=True, timeout=180, stdout=sys.stderr
                )
            subprocess.run(
                [str(python), "-m", "pip", "check"],
                cwd=work,
                env=environment,
                check=True,
                timeout=30,
                stdout=sys.stderr,
            )
            result = subprocess.run(
                [str(python), "-I", str(helper), "--source-root", str(source_root)],
                cwd=work,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
            report = json.loads(result.stdout)
            reports.append({"artifact": artifact.name, **report})
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory with exactly one wheel and sdist")
    parser.add_argument("--wheelhouse", required=True, type=Path, help="Predownloaded dependency and build-tool wheels")
    parser.add_argument("--source-root", required=True, type=Path, help="Checkout excluded from runtime imports")
    args = parser.parse_args()
    print(json.dumps(verify(args.directory, args.wheelhouse, args.source_root), indent=2))


if __name__ == "__main__":
    main()
