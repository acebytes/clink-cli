"""Explicit native-generation environment names and immutable root provenance."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import clink.registry as module
from clink.models import GenerationNativeConfig
from clink.registry import ClinkRegistry, RegistryLoadError

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "value",
    [
        {"inherit_env": ["PRIVATE_INTEGRATION"], "required_env": []},
        {"inherit_env": ["PRIVATE_INTEGRATION"], "required_env": ["PRIVATE_INTEGRATION"]},
        {},
    ],
)
def test_native_generation_optional_environment_section_validates_names(value):
    profile = GenerationNativeConfig.model_validate(value)
    assert profile.inherit_env == value.get("inherit_env", [])


@pytest.mark.parametrize(
    "value",
    [
        {"inherit_env": ["CLINK_DEPTH"]},
        {"inherit_env": ["INVALID-NAME"]},
        {"inherit_env": ["A", "A"]},
        {"required_env": ["MISSING"]},
        {"enabled": True},
        {"roles": {}},
        {"model": "auto"},
        {"inherit_env": "A"},
    ],
)
def test_environment_section_cannot_grant_authority_or_add_arbitrary_fields(value):
    with pytest.raises(ValidationError):
        GenerationNativeConfig.model_validate(value)


def registry_environment(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    legacy = tmp_path / "legacy"
    for path in (bundled, user, legacy):
        path.mkdir()
    monkeypatch.setattr(module, "CONFIG_DIR", bundled)
    monkeypatch.setattr(module, "USER_CONFIG_DIR", user)
    monkeypatch.setattr(module, "LEGACY_USER_CONFIG_DIR", legacy)
    monkeypatch.delenv("CLI_CLIENTS_CONFIG_PATH", raising=False)
    return bundled, user, legacy


def write_manifest(path, project, *, roots=True, **changes):
    value = {
        "schema_version": 1,
        "name": "codex",
        "adapter": "codex",
        "command": "codex",
        "working_dir": str(project),
        "roles": {"default": {"prompt_path": str(ROOT / "systemprompts/clink/default.txt")}},
        **changes,
    }
    if roots:
        value["allowed_roots"] = [str(project)]
    path.write_text(json.dumps(value))


@pytest.mark.parametrize(
    "source,expected", [("bundled", "bundled"), ("user", "user"), ("legacy", "legacy_user"), ("explicit", "explicit")]
)
def test_registry_captures_configuration_scope_and_explicit_root_marker(tmp_path, monkeypatch, source, expected):
    bundled, user, legacy = registry_environment(tmp_path, monkeypatch)
    paths = {
        "bundled": bundled / "codex.json",
        "user": user / "codex.json",
        "legacy": legacy / "codex.json",
        "explicit": tmp_path / "explicit.json",
    }
    write_manifest(
        paths[source], tmp_path, generation_native={"inherit_env": ["CUSTOM_TOKEN"], "required_env": ["CUSTOM_TOKEN"]}
    )
    if source == "explicit":
        monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", str(paths[source]))
    client = ClinkRegistry().get_client("codex")
    assert client.configuration_scope == expected and client.allowed_roots_explicit is True
    assert client.generation_native.inherit_env == ("CUSTOM_TOKEN",)
    assert client.generation_native.required_env == ("CUSTOM_TOKEN",)


def test_implicit_roots_remain_usable_for_existing_profiles_but_are_marked(tmp_path, monkeypatch):
    bundled, _, _ = registry_environment(tmp_path, monkeypatch)
    write_manifest(bundled / "codex.json", tmp_path, roots=False)
    client = ClinkRegistry().get_client("codex")
    assert client.allowed_roots == (tmp_path,) and client.allowed_roots_explicit is False
    assert client.configuration_scope == "bundled" and client.generation_native is None


def test_project_manifests_are_distinct_from_independent_user_roots(tmp_path, monkeypatch):
    registry_environment(tmp_path, monkeypatch)
    project_config = tmp_path / ".clink/cli_clients"
    project_config.mkdir(parents=True)
    write_manifest(project_config / "codex.json", tmp_path)
    registry = ClinkRegistry(project_dir=tmp_path)
    assert registry.get_client("codex").configuration_scope == "project"
    assert registry.project_dir == tmp_path


def test_native_generation_section_rejects_unimplemented_adapters(tmp_path, monkeypatch):
    bundled, _, _ = registry_environment(tmp_path, monkeypatch)
    write_manifest(bundled / "codex.json", tmp_path, adapter="gemini", generation_native={})
    with pytest.raises(RegistryLoadError, match="Native generation supports only"):
        ClinkRegistry()


def test_claude_native_generation_environment_is_independently_configurable(tmp_path, monkeypatch):
    bundled, _, _ = registry_environment(tmp_path, monkeypatch)
    write_manifest(
        bundled / "claude.json",
        tmp_path,
        name="claude",
        adapter="claude",
        command="claude",
        generation_native={"inherit_env": ["CUSTOM_TOKEN"], "required_env": ["CUSTOM_TOKEN"]},
    )
    client = ClinkRegistry().get_client("claude")
    assert client.adapter == "claude" and client.allowed_roots_explicit is True
    assert client.generation_native.inherit_env == ("CUSTOM_TOKEN",)
    assert client.generation_native.required_env == ("CUSTOM_TOKEN",)
