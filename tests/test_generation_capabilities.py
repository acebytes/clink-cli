"""Read-only generation discovery and exact local executable identity checks."""

import asyncio
import builtins
import errno
import hashlib
import importlib
import json
import os
import stat
from dataclasses import replace
from types import SimpleNamespace

import pytest

from clink.native_generation_policy import NativeGenerationAdmission


@pytest.fixture
def capabilities():
    return importlib.import_module("clink.generation_capabilities")


@pytest.fixture
def admitted(capabilities, tmp_path, monkeypatch):
    executable = tmp_path / "synthetic-native"
    executable.write_bytes(b"synthetic executable; never run")
    executable.chmod(0o700)
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"synthetic":true}')
    admission = NativeGenerationAdmission(
        version="synthetic-1",
        verified=True,
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        recipe_sha256="1" * 64,
        parser_sha256="2" * 64,
        evidence_path=str(evidence),
        evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
    )
    binding = capabilities._FamilyBinding(admission, lambda: "1" * 64, lambda: "2" * 64)
    original = capabilities._family_binding
    monkeypatch.setattr(capabilities, "_family_binding", lambda cli: binding if cli == "codex" else original(cli))
    return executable, evidence, binding


@pytest.mark.asyncio
async def test_closed_inventory_is_bounded_deterministic_and_config_process_independent(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"clink.registry", "clink.native_generation", "dotenv", "utils.env"}:
            pytest.fail(f"capabilities loaded configuration or a runtime: {name}")
        return original_import(name, *args, **kwargs)

    def no_process(*args, **kwargs):
        pytest.fail("capabilities started a process")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", no_process)
    monkeypatch.setattr("subprocess.Popen", no_process)
    module = importlib.import_module("clink.generation_capabilities")
    result = await module.generation_capabilities()
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert len(encoded) <= 65536
    assert result == await module.generation_capabilities()
    assert set(result) == {"schema_version", "generation_schemas", "families", "preflight"}
    assert result["schema_version"] == 1
    assert result["generation_schemas"] == [2, 3]
    assert result["preflight"] is None
    families = result["families"]
    assert [row["id"] for row in families] == ["codex", "claude", "agy", "grok"]
    expected_defaults = {"codex": "gpt-6.1-sol", "claude": "claude-opus-5-5"}
    for row in families:
        assert set(row) == {
            "id",
            "display_name",
            "executable_names",
            "generation_schema",
            "model_selection_kinds",
            "default_model_selection",
            "admission",
        }
        assert row["generation_schema"] == (2 if row["id"] in expected_defaults else 3)
        assert row["executable_names"] == [row["id"]]
        assert set(row["admission"]) == {
            "status",
            "admitted_version",
            "executable_sha256",
            "recipe_sha256",
            "parser_sha256",
            "evidence_sha256",
        }
        if row["id"] in expected_defaults:
            assert row["model_selection_kinds"] == ["explicit"]
            assert row["default_model_selection"] == {"kind": "explicit", "id": expected_defaults[row["id"]]}
        else:
            assert row["model_selection_kinds"] == ["native_default"]
            assert row["default_model_selection"] == {"kind": "native_default"}
        if row["id"] == "agy":
            assert row["admission"] == {
                "status": "unverified",
                "admitted_version": None,
                "executable_sha256": None,
                "recipe_sha256": None,
                "parser_sha256": None,
                "evidence_sha256": None,
            }
        else:
            assert row["admission"]["status"] == "verified"
    result["families"][0]["executable_names"].append("changed")
    assert (await module.generation_capabilities())["families"][0]["executable_names"] == ["codex"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cli,executable", [("codex", None), (None, "/private/secret"), ("unknown", "/private/secret")])
async def test_preflight_requires_a_known_cli_and_paired_arguments(capabilities, cli, executable):
    with pytest.raises(ValueError) as caught:
        await capabilities.generation_capabilities(cli, executable)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_preflight_matches_exact_verified_binary_without_starting_it(capabilities, admitted, monkeypatch):
    executable, _, binding = admitted
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: pytest.fail("unexpected process"))
    result = await capabilities.generation_capabilities("codex", str(executable))
    assert result["preflight"] == {
        "cli": "codex",
        "status": "compatible",
        "executable_sha256": binding.admission.executable_sha256,
    }
    assert str(executable) not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["missing", "changed", "directory", "symlink", "fifo", "not_executable", "owner_not_executable", "relative"],
)
async def test_preflight_rejects_missing_changed_and_nonregular_files(capabilities, admitted, change):
    executable, _, _ = admitted
    if change == "missing":
        executable.unlink()
    elif change == "changed":
        executable.write_bytes(b"replaced executable")
    elif change in {"directory", "symlink", "fifo"}:
        executable.unlink()
        if change == "directory":
            executable.mkdir()
        elif change == "symlink":
            executable.symlink_to(executable.with_name("missing-target"))
        else:
            os.mkfifo(executable)
    elif change == "not_executable":
        executable.chmod(0o600)
    elif change == "owner_not_executable":
        if os.geteuid() == 0:
            pytest.skip("Root can execute this owner-denied file through its other execute bit.")
        executable.chmod(0o401)
        assert not os.access(executable, os.X_OK)
    selected = executable.name if change == "relative" else str(executable)
    result = await capabilities.generation_capabilities("codex", selected)
    preflight = result["preflight"]
    assert preflight["status"] == ("missing" if change == "missing" else "invalid_executable")
    assert preflight["executable_sha256"] == (
        hashlib.sha256(executable.read_bytes()).hexdigest() if change == "changed" else None
    )
    assert selected not in json.dumps(preflight)


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", ["verified", "recipe_sha256", "parser_sha256", "evidence_sha256", "evidence_path"])
async def test_unverified_or_changed_admission_never_reports_compatible(capabilities, admitted, monkeypatch, pin):
    executable, evidence, binding = admitted
    if pin == "verified":
        updated = replace(binding.admission, verified=False)
    elif pin == "evidence_path":
        evidence.unlink()
        updated = binding.admission
    else:
        updated = replace(binding.admission, **{pin: "0" * 64})
    original = capabilities._family_binding
    monkeypatch.setattr(
        capabilities,
        "_family_binding",
        lambda cli: replace(binding, admission=updated) if cli == "codex" else original(cli),
    )
    result = await capabilities.generation_capabilities("codex", str(executable))
    assert result["preflight"]["status"] == "admission_unverified"
    assert result["families"][0]["admission"]["status"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize("cli", ["grok", "agy"])
async def test_explicitly_unverified_native_default_family_refuses_preflight(capabilities, admitted, monkeypatch, cli):
    executable, _, _ = admitted
    original = capabilities._family_binding
    unverified = replace(original(cli), admission=NativeGenerationAdmission())
    monkeypatch.setattr(
        capabilities, "_family_binding", lambda selected: unverified if selected == cli else original(selected)
    )
    result = await capabilities.generation_capabilities(cli, str(executable))
    assert result["preflight"]["status"] == "admission_unverified"
    family = next(row for row in result["families"] if row["id"] == cli)
    assert family["admission"] == {
        "status": "unverified",
        "admitted_version": None,
        "executable_sha256": None,
        "recipe_sha256": None,
        "parser_sha256": None,
        "evidence_sha256": None,
    }


@pytest.mark.asyncio
async def test_agy_native_default_family_remains_unverified(capabilities, admitted):
    executable, _, _ = admitted
    result = await capabilities.generation_capabilities("agy", str(executable))
    assert result["preflight"]["status"] == "admission_unverified"


@pytest.mark.asyncio
async def test_verified_grok_native_default_rejects_an_unrelated_executable(capabilities, tmp_path):
    cli = "grok"
    executable = tmp_path / "synthetic-native"
    executable.write_bytes(b"unrelated executable; never run")
    executable.chmod(0o700)
    result = await capabilities.generation_capabilities(cli, str(executable))
    family = next(row for row in result["families"] if row["id"] == cli)
    assert family["generation_schema"] == 3
    assert family["default_model_selection"] == {"kind": "native_default"}
    assert family["admission"]["status"] == "verified"
    assert result["preflight"] == {
        "cli": cli,
        "status": "invalid_executable",
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }


@pytest.mark.asyncio
async def test_hash_is_byte_time_bounded_and_closes_descriptor_on_cancellation(capabilities, tmp_path, monkeypatch):
    executable = tmp_path / "large-executable"
    executable.write_bytes(b"x" * 32)
    executable.chmod(0o700)
    with pytest.raises(ValueError):
        await capabilities._hash_regular_file(
            str(executable), maximum_bytes=31, deadline=capabilities.time.monotonic() + 1
        )
    with pytest.raises(TimeoutError):
        await capabilities._hash_regular_file(str(executable), maximum_bytes=32, deadline=0)
    descriptors = []
    original_open = capabilities.os.open

    def capture_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(capabilities.os, "open", capture_open)
    monkeypatch.setattr(capabilities, "_HASH_CHUNK_BYTES", 1)
    task = asyncio.create_task(
        capabilities._hash_regular_file(
            str(executable),
            maximum_bytes=32,
            deadline=capabilities.time.monotonic() + 1,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError) as caught:
            os.fstat(descriptor)
        assert caught.value.errno == errno.EBADF


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["content", "replace", "mode"])
async def test_hash_rejects_file_identity_changes_during_read(capabilities, tmp_path, monkeypatch, change):
    executable = tmp_path / "mutable-executable"
    executable.write_bytes(b"one version")
    executable.chmod(0o700)
    original_read = capabilities.os.read
    changed = False

    def mutate(descriptor, count):
        nonlocal changed
        block = original_read(descriptor, count)
        if not changed:
            changed = True
            if change == "content":
                executable.write_bytes(b"new version")
            elif change == "replace":
                executable.rename(executable.with_suffix(".old"))
                executable.write_bytes(b"one version")
                executable.chmod(0o700)
            else:
                executable.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return block

    monkeypatch.setattr(capabilities.os, "read", mutate)
    with pytest.raises(ValueError):
        await capabilities._hash_regular_file(
            str(executable), maximum_bytes=32, deadline=capabilities.time.monotonic() + 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["before_open", "final_symlink", "growth"])
async def test_hash_rejects_races_and_size_growth(capabilities, tmp_path, monkeypatch, change):
    executable = tmp_path / "racing-executable"
    executable.write_bytes(b"initial")
    executable.chmod(0o700)
    original_open = capabilities.os.open
    original_read = capabilities.os.read
    changed = False

    def swap_before_open(*args, **kwargs):
        executable.rename(executable.with_suffix(".old"))
        executable.write_bytes(b"initial")
        executable.chmod(0o700)
        return original_open(*args, **kwargs)

    def mutate_after_read(descriptor, count):
        nonlocal changed
        block = original_read(descriptor, count)
        if not changed:
            changed = True
            if change == "final_symlink":
                retained = executable.with_suffix(".old")
                executable.rename(retained)
                executable.symlink_to(retained)
            elif change == "growth":
                with executable.open("ab") as stream:
                    stream.write(b"exceeds-limit")
        return block

    if change == "before_open":
        monkeypatch.setattr(capabilities.os, "open", swap_before_open)
    else:
        monkeypatch.setattr(capabilities.os, "read", mutate_after_read)
    with pytest.raises(ValueError):
        await capabilities._hash_regular_file(
            str(executable), maximum_bytes=7, deadline=capabilities.time.monotonic() + 1
        )


@pytest.mark.asyncio
async def test_hash_deadline_is_rechecked_after_chunks(capabilities, tmp_path, monkeypatch):
    executable = tmp_path / "deadline-executable"
    executable.write_bytes(b"two chunks")
    monkeypatch.setattr(capabilities, "_HASH_CHUNK_BYTES", 1)
    now = 0

    def monotonic():
        nonlocal now
        now += 1
        return now

    monkeypatch.setattr(capabilities, "time", SimpleNamespace(monotonic=monotonic))
    with pytest.raises(TimeoutError):
        await capabilities._hash_regular_file(str(executable), maximum_bytes=10, deadline=4)
