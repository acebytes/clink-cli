"""New CLI families stay within shared selection, discovery and admission contracts."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import clink.backend as backend_module
import clink.registry as registry_module
from clink.backend import CLIBackend
from clink.contracts import BackendError, CLIRequest
from clink.policy import recipe_digest
from clink.registry import ClinkRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry_module, "LEGACY_USER_CONFIG_DIR", tmp_path / "legacy")
    monkeypatch.setattr(registry_module, "USER_CONFIG_DIR", tmp_path / "user")
    monkeypatch.setattr(registry_module, "get_env", lambda *_args: None)
    monkeypatch.setattr(backend_module, "get_env", lambda _key, default=None: default)
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    return ClinkRegistry()


@pytest.mark.parametrize("name", ["agy", "grok"])
def test_bundled_family_preserves_native_models_and_role_selection(registry, name):
    client = registry.get_client(name)
    assert client.adapter == name
    assert client.parser == f"{name}_json"
    assert set(client.roles) == {"default", "planner", "codereviewer"}
    assert client.allowed_roots == (Path.cwd(),)
    backend = CLIBackend(registry)
    target = backend.resolve(cli_name=name, role="codereviewer", model="native-model.v1")
    assert target.cli_name == name
    assert target.role.name == "codereviewer"
    assert target.effective_model == "native-model.v1"
    assert target.default_origin == "request"
    assert backend.resolve(cli_name=name, model="auto").effective_model is None


def test_all_clients_are_discoverable_without_installation_or_inference(registry, monkeypatch):
    monkeypatch.setattr(backend_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *_args, **_kwargs: pytest.fail("unexpected launch"))
    records = CLIBackend(registry).describe()
    assert {entry["cli_name"] for entry in records} == {"codex", "claude", "gemini", "agy", "grok"}
    assert all(entry["available"] is False and entry["authentication"] == "unknown" for entry in records)


@pytest.mark.parametrize("name", ["agy", "grok"])
@pytest.mark.asyncio
async def test_unverified_family_never_reaches_process_launch(registry, monkeypatch, tmp_path, name):
    executable = tmp_path / name
    executable.write_text("synthetic executable; must never run")
    executable.chmod(0o700)
    monkeypatch.setattr(backend_module.shutil, "which", lambda _name: str(executable))
    # Even after admission is added, an unverified family must remain fail-closed.
    monkeypatch.setattr(backend_module, "_VERIFIED_POLICIES", {})
    backend = CLIBackend(registry)
    execute = AsyncMock(side_effect=AssertionError("unverified process launched"))
    monkeypatch.setattr(backend.pool, "execute", execute)
    with pytest.raises(BackendError) as caught:
        await backend.run(CLIRequest(prompt="Synthetic request", target=backend.resolve(cli_name=name)))
    assert caught.value.code == "policy_unverified"
    execute.assert_not_called()


def test_additional_families_do_not_change_codex_admission_recipe():
    evidence = Path(__file__).resolve().parents[1] / backend_module._VERIFIED_POLICIES["codex"].evidence
    admission = json.loads(evidence.read_text())
    assert recipe_digest("codex") == "4bf9a7fe0491e12965d5c36dd7ccc9d197dd271cdf57df378c68fc1b9e0dae16"
    assert backend_module._VERIFIED_POLICIES["codex"].verified
    assert admission
