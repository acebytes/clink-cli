"""Check the runtime resources that must survive wheel/uvx installation."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

from clink.models import CLIClientConfig
from clink.registry import ClinkRegistry

ROOT = Path(__file__).resolve().parent.parent


def test_cli_manifests_are_package_resources():
    directory = files("conf").joinpath("cli_clients")
    manifests = sorted(path for path in directory.iterdir() if path.name.endswith(".json"))
    assert {path.name for path in manifests} >= {"codex.json", "claude.json", "gemini.json", "agy.json", "grok.json"}
    for manifest in manifests:
        config = CLIClientConfig.model_validate(json.loads(manifest.read_text()))
        assert config.schema_version == 1
        assert config.env == {}


def test_role_prompt_resources_resolve_relative_to_installed_manifests(monkeypatch, tmp_path):
    import clink.registry

    monkeypatch.setattr(clink.registry, "USER_CONFIG_DIR", tmp_path / "user")
    monkeypatch.setattr(clink.registry, "LEGACY_USER_CONFIG_DIR", tmp_path / "legacy")
    monkeypatch.delenv("CLI_CLIENTS_CONFIG_PATH", raising=False)
    registry = ClinkRegistry()
    assert "codex" in registry.list_clients()
    for name in registry.list_clients():
        client = registry.get_client(name)
        roles = list(client.roles.values())
        if client.native_task is not None:
            roles.extend(client.native_task.roles.values())
        for role in roles:
            assert role.prompt_path.is_file()
            assert role.prompt_path.read_text().strip()
    assert files("systemprompts").joinpath("clink/default.txt").read_text().strip()


def test_distribution_explicitly_excludes_provider_packages_and_catalogs():
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packages = metadata["tool"]["setuptools"]["packages"]["find"]
    assert "providers*" in packages["exclude"]
    assert "providers*" not in packages["include"]
    assert metadata["tool"]["setuptools"]["include-package-data"] is False
    assert metadata["tool"]["setuptools"]["package-data"] == {
        "clink": [
            "resources/codex-0.166.0-models-direct.json",
            "resources/native-claude-2.1.304-admission.json",
            "resources/PROVENANCE.md",
            "resources/CODEX-LICENSE",
            "resources/CODEX-NOTICE",
        ],
        "conf": ["cli_clients/*.json"],
        "systemprompts": ["clink/*.txt"],
    }
    assert "data-files" not in metadata["tool"]["setuptools"]
    manifest = (ROOT / "MANIFEST.in").read_text()
    assert "prune providers" in manifest
    assert "recursive-exclude conf *_models.json" in manifest


def test_pinned_codex_runtime_resource_survives_package_installation():
    resource = files("clink").joinpath("resources/codex-0.166.0-models-direct.json")
    content = resource.read_bytes()
    assert len(content) == 757141
    assert hashlib.sha256(content).hexdigest() == "69d3e68d0e78a684c56b1818f1351149902fc61c398e4b69d371dfdea08e4e12"
    catalog = json.loads(content)
    assert set(catalog) == {"models"}
    assert len(catalog["models"]) == 12
    assert all(model["tool_mode"] == "direct" for model in catalog["models"])


def test_native_admission_is_packaged_and_matches_its_pinned_receipt():
    from clink.native import _VERIFIED_NATIVE_POLICIES
    from clink.native_policy import native_recipe_digest

    policy = _VERIFIED_NATIVE_POLICIES["claude"]
    content = files("clink").joinpath("resources/native-claude-2.1.304-admission.json").read_bytes()
    assert hashlib.sha256(content).hexdigest() == policy.evidence_sha256
    assert policy.evidence == "clink/resources/native-claude-2.1.304-admission.json"
    assert policy.recipe_sha256 == native_recipe_digest()
    assert content == (ROOT / "docs/verification/native-claude-2.1.304-admission.json").read_bytes()


def test_codex_resource_retains_license_notice_and_modification_provenance():
    directory = files("clink").joinpath("resources")
    license_text = directory.joinpath("CODEX-LICENSE").read_text()
    notice = directory.joinpath("CODEX-NOTICE").read_text()
    provenance = directory.joinpath("PROVENANCE.md").read_text()
    assert "Apache License" in license_text
    assert "OpenAI" in notice
    assert "tool_mode" in provenance
    assert "9e01cd50ec8c4ba6d782413d1154cb2dbe1da63e014d622631b0603bc19659d6" in provenance
