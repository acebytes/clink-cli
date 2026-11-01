"""Reproduce the offline Agy-admission packaging check without native CLI calls."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib
import importlib.metadata
import importlib.resources
import json
import os
import platform
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def native_entries(content: str) -> dict[str, str]:
    tree = ast.parse(content)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_VERIFIED_NATIVE_POLICIES"
    )
    return {
        key.value: ast.dump(value) for key, value in zip(assignment.value.keys, assignment.value.values, strict=True)
    }


def installed_smoke(install: Path, work: Path) -> None:
    """Use installed modules and actual MCP handlers under process/network guards."""
    sys.path.insert(0, str(install))
    forbidden = []
    process_events = {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork", "os.forkpty"}
    network_events = {"socket.connect", "socket.connect_ex", "socket.bind", "socket.sendto", "socket.getaddrinfo"}

    def audit(event, _arguments):
        if event in process_events or event in network_events:
            forbidden.append(event)
            raise AssertionError(f"Forbidden discovery action: {event}")

    sys.addaudithook(audit)
    assert Path.cwd() == work
    assert Path(os.environ["HOME"]).is_relative_to(work)
    assert os.environ["PATH"] == str(work / "empty-path")
    assert not (install / ".env").exists()
    import server
    from clink.backend import _VERIFIED_POLICIES, get_backend
    from clink.native import _VERIFIED_NATIVE_POLICIES
    from clink.native_policy import native_recipe_digest
    from clink.policy import CODEX_CATALOG_SHA256, recipe_digest

    module_names = (
        "server",
        "clink.native",
        "clink.native_adapters",
        "clink.parsers.grok_native",
        "clink.parsers.agy_native",
        "clink.parsers.codex_native",
    )
    for name in module_names:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(install)
    assert sorted(_VERIFIED_POLICIES) == ["codex"]
    codex = _VERIFIED_POLICIES["codex"]
    assert codex.evidence_sha256 == "43e1700703aba5b3034612eceed2ee04c5d135be45c99ca17458dbec909ceb69"
    assert (
        codex.recipe_sha256
        == recipe_digest("codex")
        == "4bf9a7fe0491e12965d5c36dd7ccc9d197dd271cdf57df378c68fc1b9e0dae16"
    )
    catalog = importlib.resources.files("clink").joinpath("resources/codex-0.166.0-models-direct.json")
    assert sha256(catalog.read_bytes()) == CODEX_CATALOG_SHA256
    assert sorted(_VERIFIED_NATIVE_POLICIES) == ["agy", "claude", "grok"]
    receipts = {}
    for adapter, policy in sorted(_VERIFIED_NATIVE_POLICIES.items()):
        resource = install / policy.evidence
        assert resource.is_file()
        assert sha256(resource.read_bytes()) == policy.evidence_sha256
        assert native_recipe_digest(adapter) == policy.recipe_sha256
        receipts[adapter] = {
            "resource": policy.evidence,
            "evidence_sha256": policy.evidence_sha256,
            "recipe_sha256": policy.recipe_sha256,
            "executable_sha256": policy.executable_sha256,
            "version": policy.version,
        }

    async def discover():
        tools = await server.handle_list_tools()
        assert len(tools) == 18
        local = {}
        for name in ("listmodels", "version"):
            response = await server.handle_call_tool(name, {})
            local[name] = json.loads(response[0].text)
            assert local[name]["status"] == "success"
        await get_backend().close()
        return tools, local

    tools, local = asyncio.run(discover())
    discovery = local["listmodels"]
    clients = discovery["clients"]
    profiles = {item["cli_name"]: item for item in discovery["native_task_clients"]}
    assert len(clients) == 5
    assert set(profiles) == {"agy", "claude", "codex", "grok"}
    assert all(not item["available"] and item["authentication"] == "unknown" for item in clients)
    assert all(not item["installed"] and not item["available"] for item in profiles.values())
    for adapter in ("agy", "claude", "grok"):
        assert profiles[adapter]["enabled"]
        assert profiles[adapter]["verification_status"] == "verified_native_lifecycle"
    assert not profiles["codex"]["enabled"]
    assert profiles["codex"]["verification_status"] == "unverified"
    assert not forbidden
    print(
        json.dumps(
            {
                "status": "passed",
                "runtime": "installed_wheel_target_with_managed_dependencies",
                "platform": [platform.system(), platform.machine()],
                "modules_from_installed_wheel": list(module_names),
                "tool_count": len(tools),
                "tools": sorted(tool.name for tool in tools),
                "client_count": len(clients),
                "clients": [
                    {
                        key: item[key]
                        for key in ("cli_name", "adapter", "available", "authentication", "verification_status")
                    }
                    for item in clients
                ],
                "native_profile_count": len(profiles),
                "native_profiles": {
                    name: {
                        key: item[key]
                        for key in ("enabled", "installed", "available", "authentication", "verification_status")
                    }
                    for name, item in sorted(profiles.items())
                },
                "native_admissions": receipts,
                "catalog_sha256": CODEX_CATALOG_SHA256,
                "advisory_admissions": sorted(_VERIFIED_POLICIES),
                "local_calls": sorted(local),
                "installed_clis": 0,
                "provider_credentials_supplied": False,
                "native_cli_calls": 0,
                "model_calls": 0,
                "authentication_probes": 0,
                "subprocess_calls": 0,
                "network_calls": 0,
                "forbidden_audit_events": forbidden,
            },
            indent=2,
            sort_keys=True,
        )
    )


def main(attempt: int) -> None:
    root = Path(__file__).resolve().parents[3]
    evidence_root = Path(__file__).resolve().parent
    evidence = evidence_root if attempt == 1 else evidence_root / f"attempt{attempt}"
    if attempt != 1:
        evidence.mkdir(exist_ok=False)
    suffix = "" if attempt == 1 else f"-attempt{attempt}"
    scratch = root / f"test_output/native-adapters-packaging2{suffix}"
    scratch.mkdir(parents=True, exist_ok=False)
    source = scratch / "source"
    source.mkdir()
    work = scratch / "work"
    work.mkdir()
    (work / "home").mkdir()
    (work / "empty-path").mkdir()
    environment = {
        "HOME": str(work / "home"),
        "PATH": f"{root / '.venv/bin'}:/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PAL_MCP_FORCE_ENV_OVERRIDE": "false",
        "LOG_LEVEL": "ERROR",
    }
    python = str(root / ".venv/bin/python")
    sandbox = ["/usr/bin/sandbox-exec", "-f", str(root / "docs/baseline/2026-10-31/offline.sb")]
    commands = []

    def run(step, arguments, cwd=root, record_output=True):
        started = time.monotonic()
        result = subprocess.run(arguments, cwd=cwd, env=environment, capture_output=True, text=True, timeout=180)
        commands.append(
            {
                "step": step,
                "argv": arguments,
                "cwd": str(cwd),
                "exit_code": result.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        )
        if record_output:
            (evidence / f"{step}.txt").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"{step} failed; inspect its retained evidence")
        return result.stdout

    started_at = datetime.now(timezone.utc).isoformat()
    commit = run("source-commit", ["git", "rev-parse", "HEAD"]).strip()
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    paths = {"LICENSE", "README.md", "MANIFEST.in", "pyproject.toml", "requirements.txt", "server.py", "config.py"}
    for package in ("clink", "tools", "utils", "systemprompts", "conf"):
        paths.update(str(path.relative_to(root)) for path in (root / package).rglob("*.py"))
    for package, patterns in metadata["tool"]["setuptools"]["package-data"].items():
        for pattern in patterns:
            paths.update(str(path.relative_to(root)) for path in (root / package).glob(pattern))
    source_hashes = {}
    for relative in sorted(paths):
        original = root / relative
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = original.read_bytes()
        source_hashes[relative] = sha256(content)
        destination.write_bytes(content)
    input_hashes = dict(source_hashes)
    for relative in (
        ".dockerignore",
        "scripts/verify_distribution.py",
        "tests/test_uvx_resource_packaging.py",
        "tests/test_docker_implementation.py",
    ):
        input_hashes[relative] = sha256((root / relative).read_bytes())
    prior = json.loads((root / "docs/verification/native-adapters-packaging/summary.json").read_text())
    unchanged_paths = [
        "clink/backend.py",
        "clink/policy.py",
        "clink/native_policy.py",
        "conf/cli_clients/codex.json",
        "conf/cli_clients/claude.json",
        "conf/cli_clients/grok.json",
        "clink/resources/codex-0.166.0-models-direct.json",
        "clink/resources/native-claude-2.1.304-admission.json",
        "clink/resources/native-grok-1.0.70-admission.json",
    ]
    for relative in unchanged_paths:
        assert source_hashes[relative] == prior["source_files_sha256"][relative], relative
    baseline_native = run("baseline-native-policies", ["git", "show", "270d1e5:clink/native.py"], record_output=False)
    entries = native_entries((root / "clink/native.py").read_text())
    baseline_entries = native_entries(baseline_native)
    assert set(baseline_entries) == {"claude", "grok"}
    assert {key: entries[key] for key in baseline_entries} == baseline_entries
    for adapter, filename in (
        ("claude", "native-claude-2.1.304-admission.json"),
        ("grok", "native-grok-1.0.70-admission.json"),
        ("agy", "native-agy-1.4.12-admission.json"),
    ):
        assert (root / "clink/resources" / filename).read_bytes() == (
            root / "docs/verification" / filename
        ).read_bytes(), adapter
    run("build", sandbox + [python, "-m", "build", "--no-isolation", "--outdir", str(scratch / "dist"), str(source)])
    standard = json.loads(
        run(
            "distribution-verifier",
            sandbox + [python, str(root / "scripts/verify_distribution.py"), str(scratch / "dist")],
        )
    )
    artifacts = []
    for artifact in sorted((scratch / "dist").iterdir()):
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                members = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
            wheel_metadata = next(content for name, content in members.items() if name.endswith(".dist-info/METADATA"))
            assert (root / "README.md").read_bytes().strip() in wheel_metadata
        else:
            with tarfile.open(artifact) as archive:
                members = {
                    str(
                        PurePosixPath(member.name).relative_to(PurePosixPath(member.name).parts[0])
                    ): archive.extractfile(member).read()
                    for member in archive.getmembers()
                    if member.isfile()
                }
        matched = {}
        for relative, content in members.items():
            if relative in source_hashes:
                assert sha256(content) == source_hashes[relative], relative
                matched[relative] = source_hashes[relative]
        for name in source_hashes:
            if name.endswith(".py") or name.startswith(
                ("conf/cli_clients/", "clink/resources/", "systemprompts/clink/")
            ):
                assert name in members, name
        artifacts.append(
            {
                "artifact": str(artifact.relative_to(root)),
                "sha256": sha256(artifact.read_bytes()),
                "member_count": len(members),
                "source_matched_members_sha256": matched,
            }
        )
    wheel = next((scratch / "dist").glob("*.whl"))
    install = scratch / "install"
    run(
        "install-wheel",
        sandbox
        + [
            python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--no-compile",
            "--target",
            str(install),
            str(wheel),
        ],
    )
    environment["PATH"] = str(work / "empty-path")
    installed = json.loads(
        run(
            "installed-wheel-smoke",
            sandbox + [python, "-I", "-B", str(Path(__file__).resolve()), "--smoke", str(install), str(work)],
            cwd=work,
        )
    )
    assert all(sha256((root / relative).read_bytes()) == digest for relative, digest in input_hashes.items())
    summary = {
        "schema_version": 1,
        "attempt": attempt,
        "status": "passed",
        "purpose": "Offline wheel/sdist and guarded installed-wheel discovery after accepted exact Agy native admission integration",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": commit,
        "source_commit_describes_head_only": True,
        "source_snapshot_kind": "frozen working tree copied into isolated build source",
        "source_files_sha256": input_hashes,
        "source_tree_sha256": sha256(json.dumps(input_hashes, sort_keys=True, separators=(",", ":")).encode()),
        "source_stable_during_verification": True,
        "artifacts": artifacts,
        "standard_distribution_verification": standard,
        "installed_wheel": installed,
        "commands": commands,
        "prior_packaging_source_files_unchanged": unchanged_paths,
        "prior_native_policies_unchanged": sorted(baseline_entries),
        "native_cli_calls": 0,
        "model_calls": 0,
        "authentication_probes": 0,
        "network_calls": 0,
        "production_configuration_mutated_by_verification": False,
        "full_test_suite_run": False,
        "verification_script_sha256": sha256(Path(__file__).read_bytes()),
        "limitations": [
            "Runtime dependencies reused from managed environment; no fresh dependency resolution.",
            "Actual MCP discovery handlers executed in-process from installed wheel modules; no separate MCP stdio transport test.",
            "No Docker image built or pulled; Docker resource allowlist is covered by focused regression tests.",
            "Discovery does not independently establish native execution, authentication, fulfillment, or admission.",
        ],
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": "passed",
                "artifacts": [item["artifact"] for item in artifacts],
                "tools": installed["tool_count"],
                "clients": installed["client_count"],
                "native_profiles": installed["native_profile_count"],
                "evidence": str(evidence / "summary.json"),
            }
        )
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke":
        installed_smoke(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--attempt", type=int, choices=range(1, 10), default=1)
        arguments = parser.parse_args()
        main(arguments.attempt)
