"""Pinned staged source must own execution before any cached or native loader runs."""

import importlib.machinery
import importlib.util
import json
import marshal
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import verify_claude_generation as verifier


def _staged_runtime(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    for package in ("clink", "utils"):
        shutil.copytree(
            verifier.PACKAGE_ROOT / package, staged / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    shutil.copy2(verifier.PACKAGE_ROOT / "config.py", staged / "config.py")
    scripts = staged / "scripts"
    scripts.mkdir()
    for relative in verifier._HARNESS_SOURCES:
        name = Path(relative).name
        shutil.copy2(verifier.SCRIPT_ROOT / name, scripts / name)
    return staged, scripts / "verify_claude_generation.py"


def _import_staged_probe(staged, harness, checksum, marker, module_name, *, observe_extension=False):
    code = """
import importlib, importlib.machinery, json, runpy, sys
from pathlib import Path

harness, staged, checksum, marker, module_name, observe_extension = sys.argv[1:]
if observe_extension == "True":
    original = importlib.machinery.ExtensionFileLoader.create_module

    def observe(self, spec):
        if spec.name == module_name:
            Path(marker).write_text("foreign extension loader invoked")
        return original(self, spec)

    importlib.machinery.ExtensionFileLoader.create_module = observe

sys.argv = [harness, "--package-root", staged, "--package-source-sha256", checksum]
namespace = runpy.run_path(harness, run_name="source_loading_probe")
try:
    module = importlib.import_module(module_name)
except (ImportError, ValueError):
    print(json.dumps({"loaded": False}))
else:
    namespace["_runtime_identity"]()
    print(json.dumps({"loaded": True, "identity": module.SOURCE_IDENTITY, "file": module.__file__}))
"""
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            code,
            str(harness),
            str(staged),
            checksum,
            str(marker),
            module_name,
            str(observe_extension),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize(
    "module_name,relative_path",
    [
        ("clink", "clink/__init__.py"),
        ("clink._source_loading_probe", "clink/_source_loading_probe.py"),
        ("config", "config.py"),
        ("scripts.verify_cli_policy", "scripts/verify_cli_policy.py"),
        ("scripts.claude_generation_review", "scripts/claude_generation_review.py"),
        ("scripts.private_evidence", "scripts/private_evidence.py"),
    ],
    ids=["package_init", "submodule", "config", "pinned_helper", "completion_helper", "history_guard_helper"],
)
def test_staged_source_ignores_valid_timestamp_cache_with_different_code(tmp_path, module_name, relative_path):
    staged, harness = _staged_runtime(tmp_path)
    source = staged / relative_path
    source.write_bytes((source.read_bytes() if source.exists() else b"") + b"\nSOURCE_IDENTITY = 'pinned source'\n")
    original = source.read_bytes()
    _, checksum = verifier._package_sources(staged / "clink")
    marker = tmp_path / "untrusted-bytecode-executed"
    poison = compile(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('untrusted bytecode executed')\n"
        "SOURCE_IDENTITY = 'poisoned cache'\n",
        str(source),
        "exec",
    )
    cached = Path(importlib.util.cache_from_source(str(source)))
    cached.parent.mkdir()
    header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(source.stat().st_mtime), len(original))
    cached.write_bytes(header + marshal.dumps(poison))
    assert source.read_bytes() == original
    assert verifier._package_sources(staged / "clink")[1] == checksum

    completed = _import_staged_probe(staged, harness, checksum, marker, module_name)

    assert not marker.exists(), "Cached code outside the source pin executed"
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"loaded": True, "identity": "pinned source", "file": str(source)}
    assert source.read_bytes() == original


def test_staged_source_prevents_extension_loader_precedence(tmp_path):
    staged, harness = _staged_runtime(tmp_path)
    source = staged / "clink/_source_loading_probe.py"
    source.write_text("SOURCE_IDENTITY = 'pinned source'\n")
    extension = source.with_name(source.stem + importlib.machinery.EXTENSION_SUFFIXES[0])
    extension.write_bytes(b"not a compiled native extension")
    marker = tmp_path / "foreign-extension-loader-invoked"
    _, checksum = verifier._package_sources(staged / "clink")

    completed = _import_staged_probe(
        staged, harness, checksum, marker, "clink._source_loading_probe", observe_extension=True
    )

    assert not marker.exists(), "Foreign extension loader ran before the source boundary refused it"
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed == {"loaded": False} or observed == {
        "loaded": True,
        "identity": "pinned source",
        "file": str(source),
    }


def test_library_import_preserves_host_resolution_and_scopes_strict_source_guard():
    code = """
import importlib.util, sys
before_path, before_meta = list(sys.path), list(sys.meta_path)
from scripts import verify_claude_generation as verifier

assert sys.path == before_path and sys.meta_path == before_meta
assert importlib.util.find_spec('server').origin == str(verifier.SOURCE_PROJECT / 'server.py')
assert importlib.util.find_spec('tools').origin == str(verifier.SOURCE_PROJECT / 'tools/__init__.py')
with verifier._source_imports():
    try:
        importlib.util.find_spec('server')
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError('Unpinned production module was permitted during verification')
assert importlib.util.find_spec('server').origin == str(verifier.SOURCE_PROJECT / 'server.py')
assert importlib.util.find_spec('tools').origin == str(verifier.SOURCE_PROJECT / 'tools/__init__.py')
assert sys.path == before_path and sys.meta_path == before_meta
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=verifier.SOURCE_PROJECT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("entrypoint", ["_runtime_identity", "verify", "run_check"])
def test_cached_source_a_cannot_establish_pinned_source_b_execution_provenance(tmp_path, entrypoint):
    staged, harness = _staged_runtime(tmp_path)
    source = staged / "clink/native_generation_claude_policy.py"
    source.write_bytes(source.read_bytes() + b"\nPROVENANCE_VALUE = 'A'\n")
    marker = tmp_path / "authoritative-work-reached"
    code = """
import asyncio, hashlib, importlib, json, runpy, sys
from pathlib import Path

harness, staged_name, marker_name, entrypoint = sys.argv[1:]
staged, marker = Path(staged_name), Path(marker_name)
sys.path.insert(0, staged_name)
cached = importlib.import_module("clink.native_generation_claude_policy")
source = staged / "clink/native_generation_claude_policy.py"
assert Path(cached.__file__) == source and cached.PROVENANCE_VALUE == "A"
source_a = source.read_bytes()
source_b = source_a.replace(b"PROVENANCE_VALUE = 'A'", b"PROVENANCE_VALUE = 'B'")
assert source_a != source_b
source.write_bytes(source_b)
assert b"PROVENANCE_VALUE = 'B'" in source.read_bytes() and cached.PROVENANCE_VALUE == "A"
paths = [path for name in ("clink", "utils") for path in (staged / name).rglob("*.py")]
paths.append(staged / "config.py")
manifest = {
    "schema_version": 2,
    "files": [
        {"path": str(path.relative_to(staged)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(paths)
    ],
}
checksum = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
before_path, before_meta = list(sys.path), list(sys.meta_path)
sys.argv = [harness, "--package-root", staged_name, "--package-source-sha256", checksum]
namespace = runpy.run_path(harness, run_name="scripts.verify_claude_generation")
assert namespace["PACKAGE_SOURCE_SHA256"] == checksum
assert sys.modules["clink.native_generation_claude_policy"] is cached and cached.PROVENANCE_VALUE == "A"
assert source.read_bytes() == source_b
assert sys.path == before_path and sys.meta_path == before_meta

async def forbidden_work(*args):
    marker.write_text("Unproven cached code reached authoritative work")
    raise AssertionError("Unproven cached code reached authoritative work")

function = namespace[entrypoint]
if entrypoint == "verify":
    function.__globals__["_verify"] = forbidden_work
elif entrypoint == "run_check":
    function.__globals__["_run_check"] = forbidden_work
try:
    if entrypoint == "_runtime_identity":
        function()
    elif entrypoint == "verify":
        asyncio.run(function(object()))
    else:
        asyncio.run(function("text", "never-launched", staged, {}, staged / "never-written.json", 1))
except ValueError:
    refused = True
else:
    refused = False
assert refused, "Pinned source B was accepted as the identity of already-executed source A"
assert not marker.exists()
assert sys.modules["clink.native_generation_claude_policy"] is cached and cached.PROVENANCE_VALUE == "A"
assert sys.path == before_path and sys.meta_path == before_meta
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code, str(harness), str(staged), str(marker), entrypoint],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
