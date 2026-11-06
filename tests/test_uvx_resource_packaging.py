"""Check the runtime resources that must survive wheel/uvx installation."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest

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
            "resources/codex-0.166.0-models-generation.json",
            "resources/native-claude-2.1.304-admission.json",
            "resources/native-grok-1.0.70-admission.json",
            "resources/native-agy-1.4.12-admission.json",
            "resources/native-generation-codex-0.166.0-admission.json",
            "resources/native-generation-claude-2.1.304-admission.json",
            "resources/native-generation-grok-1.0.70-admission.json",
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


def test_generation_entrypoint_and_separate_resource_survive_package_installation():
    from clink.generation_policy import CATALOG_SHA256

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["scripts"]["clink"] == "clink.cli:main"
    resource = files("clink").joinpath("resources/codex-0.166.0-models-generation.json")
    content = resource.read_bytes()
    assert len(content) == 756743
    assert hashlib.sha256(content).hexdigest() == CATALOG_SHA256
    models = json.loads(content)["models"]
    assert len(models) == 12
    for model in models:
        assert model["shell_type"] == "disabled"
        assert model["apply_patch_tool_type"] is None
        assert model["experimental_supported_tools"] == []
        assert model["supports_search_tool"] is False


@pytest.mark.parametrize(
    "adapter,filename",
    [
        ("claude", "native-claude-2.1.304-admission.json"),
        ("grok", "native-grok-1.0.70-admission.json"),
        ("agy", "native-agy-1.4.12-admission.json"),
    ],
)
def test_native_admission_is_packaged_and_matches_its_pinned_policy(adapter, filename):
    from clink.native import _VERIFIED_NATIVE_POLICIES
    from clink.native_policy import native_recipe_digest

    policy = _VERIFIED_NATIVE_POLICIES[adapter]
    content = files("clink").joinpath(f"resources/{filename}").read_bytes()
    assert hashlib.sha256(content).hexdigest() == policy.evidence_sha256
    assert policy.evidence == f"clink/resources/{filename}"
    assert policy.recipe_sha256 == native_recipe_digest(adapter)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "archive_path,filename",
    [
        ("docs/verification/native-claude-2.1.304-admission.json", "native-claude-2.1.304-admission.json"),
        ("docs/verification/native-grok-1.0.70-admission.json", "native-grok-1.0.70-admission.json"),
        ("docs/verification/native-agy-1.4.12-admission.json", "native-agy-1.4.12-admission.json"),
        (
            "docs/verification/native-generation-codex-0.166.0-admission.json",
            "native-generation-codex-0.166.0-admission.json",
        ),
        (
            "docs/verification/native-generation-claude-2.1.304-admission.json",
            "native-generation-claude-2.1.304-admission.json",
        ),
        (
            "docs/verification/family-generation/grok/native-generation-grok-1.0.70-admission.json",
            "native-generation-grok-1.0.70-admission.json",
        ),
    ],
)
def test_packaged_admission_matches_immutable_private_mirror(private_evidence_root, archive_path, filename):
    content = files("clink").joinpath(f"resources/{filename}").read_bytes()
    assert content == (private_evidence_root / archive_path).read_bytes()


def test_codex_resource_retains_license_notice_and_modification_provenance():
    directory = files("clink").joinpath("resources")
    license_text = directory.joinpath("CODEX-LICENSE").read_text()
    notice = directory.joinpath("CODEX-NOTICE").read_text()
    provenance = directory.joinpath("PROVENANCE.md").read_text()
    assert "Apache License" in license_text
    assert "OpenAI" in notice
    assert "tool_mode" in provenance
    assert "9e01cd50ec8c4ba6d782413d1154cb2dbe1da63e014d622631b0603bc19659d6" in provenance


def test_native_generation_production_admission_is_packaged_with_exact_scope_and_identity():
    from clink.native_generation_files import final_file_contract_digest
    from clink.native_generation_policy import DEFAULT_NATIVE_ADMISSION, native_generation_recipe_digest
    from clink.parsers.codex_native_generation import parser_contract_digest

    admission = DEFAULT_NATIVE_ADMISSION
    resource = files("clink").joinpath("resources/native-generation-codex-0.166.0-admission.json")
    content = resource.read_bytes()
    record = json.loads(content)
    assert len(content) == 13044 and hashlib.sha256(content).hexdigest() == admission.evidence_sha256
    assert record["scope"] == "production_native_generation" and record["profile"] == "generation_native_v1"
    assert record["schema_version"] == 1 and record["adapter"] == "codex"
    assert record["admission"]["production_enabled"] is admission.verified is True
    assert record["executable_sha256"] == admission.executable_sha256
    assert record["version"] == admission.version
    assert record["recipe_sha256"] == admission.recipe_sha256 == native_generation_recipe_digest()
    assert record["parser_sha256"] == admission.parser_sha256 == parser_contract_digest()
    assert record["final_file_sha256"] == final_file_contract_digest()
    assert record["actual_model"] is None and record["fulfillment_verified"] is False


def test_claude_generation_observations_are_packaged_with_exact_identity():
    from clink.native_generation_claude_policy import (
        DEFAULT_CLAUDE_NATIVE_ADMISSION,
        claude_generation_recipe_digest,
    )
    from clink.parsers.claude_native_generation import parser_contract_digest

    admission = DEFAULT_CLAUDE_NATIVE_ADMISSION
    filename = "native-generation-claude-2.1.304-admission.json"
    content = files("clink").joinpath(f"resources/{filename}").read_bytes()
    record = json.loads(content)
    assert len(content) <= 65536 and hashlib.sha256(content).hexdigest() == admission.evidence_sha256
    assert record["scope"] == "claude_native_generation_observations"
    assert record["profile"] == "generation_native_v1" and record["adapter"] == "claude"
    assert record["schema_version"] == 2 and admission.verified is True
    assert admission.evidence_path == f"clink/resources/{filename}"
    assert record["observed_version"] == admission.version
    assert record["executable_sha256"] == admission.executable_sha256
    assert record["recipe_sha256"] == admission.recipe_sha256 == claude_generation_recipe_digest()
    assert record["parser_sha256"] == admission.parser_sha256 == parser_contract_digest()
    assert [gate["observation"]["actual_model"] for gate in record["gates"]] == [
        record["tested_candidate"]["requested_model"],
        record["tested_candidate"]["requested_model"],
        None,
    ]
    assert record["limitations"]["candidate_only_live_coverage"] is True
    assert record["limitations"]["fulfillment_verified"] is False


def test_grok_generation_observations_are_packaged_with_exact_mixed_candidate_identity():
    from clink.native_generation_families import (
        DEFAULT_GROK_NATIVE_ADMISSION,
        family_generation_parser_digest,
        family_generation_recipe_digest,
    )

    admission = DEFAULT_GROK_NATIVE_ADMISSION
    filename = "native-generation-grok-1.0.70-admission.json"
    content = files("clink").joinpath(f"resources/{filename}").read_bytes()
    record = json.loads(content)
    assert len(content) <= 65536 and hashlib.sha256(content).hexdigest() == admission.evidence_sha256
    assert record["scope"] == "grok_native_generation_observations"
    assert record["profile"] == "generation_native_v1" and record["adapter"] == "grok"
    assert record["schema_version"] == 3 and admission.verified is True
    assert admission.evidence_path == f"clink/resources/{filename}"
    assert record["observed_version"] == admission.version
    assert record["executable_sha256"] == admission.executable_sha256
    assert record["recipe_sha256"] == admission.recipe_sha256 == family_generation_recipe_digest("grok")
    assert record["parser_sha256"] == admission.parser_sha256 == family_generation_parser_digest("grok")
    original, corrected = record["tested_candidates"]
    assert original["candidate_sha256"] != corrected["candidate_sha256"]
    for candidate in (original, corrected):
        assert candidate["model_selection"] == {"kind": "native_default"}
        assert candidate["requested_model"] is None and candidate["effective_model"] is None
    assert [gate["check"] for gate in record["gates"]] == ["text", "action", "permission", "cancel"]
    assert [gate["ordinal"] for gate in record["gates"]] == [1, 2, 3, 5]
    assert [gate["observation"]["actual_model"] for gate in record["gates"]] == [
        "grok-4.7-build",
        "grok-4.7-build",
        None,
        None,
    ]
    assert [row["ordinal"] for row in record["submissions"]] == [1, 2, 3, 4, 5]
    assert [row["candidate_sha256"] for row in record["submissions"]] == [
        original["candidate_sha256"],
    ] * 4 + [corrected["candidate_sha256"]]
    assert record["submissions"][3]["passed"] is True
    assert record["submissions"][4]["passed"] is True
    assert record["reconciliation"]["excluded_ordinal"] == 4
    assert record["budget"]["consumed"] == 5 and record["budget"]["replacements_used"] == 1
    assert record["limitations"]["candidate_only_live_coverage"] is True
    assert record["limitations"]["native_default_identifies_actual_model"] is False
    assert record["limitations"]["fulfillment_verified"] is False


def test_unverified_agy_generation_has_no_packaged_admission_and_refuses_launch(monkeypatch):
    import clink.native_generation as generation
    from clink.native_generation_families import DEFAULT_AGY_NATIVE_ADMISSION
    from clink.native_generation_policy import NativeGenerationAdmission

    filename = "native-generation-agy-1.4.12-admission.json"
    assert not files("clink").joinpath(f"resources/{filename}").is_file()
    assert DEFAULT_AGY_NATIVE_ADMISSION == NativeGenerationAdmission()
    assert DEFAULT_AGY_NATIVE_ADMISSION.verified is False
    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: pytest.fail("unexpected native process"))
    service = generation.NativeGenerationService()
    with pytest.raises(generation.BackendError) as caught:
        service._verify_admission("synthetic-identity-only", DEFAULT_AGY_NATIVE_ADMISSION, cli="agy")
    assert caught.value.code == "policy_unverified"


@pytest.mark.private_evidence
def test_rejected_agy_generation_proposal_preserves_private_history(private_evidence_root):
    from clink.native_generation_families import (
        family_generation_parser_digest,
        family_generation_recipe_digest,
    )

    filename = "native-generation-agy-1.4.12-admission.json"
    content = (private_evidence_root / "docs/verification/family-generation/agy" / filename).read_bytes()
    record = json.loads(content)
    assert len(content) == 17379
    assert hashlib.sha256(content).hexdigest() == "cb58ac88396c57f60eaf41d2c596234da12bf333bf6dc11dbc4c6b48327ac64c"
    assert record["schema_version"] == 4 and record["scope"] == "agy_native_generation_observations"
    assert record["profile"] == "generation_native_v1" and record["adapter"] == "agy"
    assert record["observed_version"] == "1.4.12"
    assert record["executable_sha256"] == "34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3"
    assert record["recipe_sha256"] == family_generation_recipe_digest("agy")
    assert record["parser_sha256"] == family_generation_parser_digest("agy")
    original, corrected, diagnostic = record["tested_candidates"]
    assert len({candidate["candidate_sha256"] for candidate in record["tested_candidates"]}) == 3
    for candidate in (original, corrected, diagnostic):
        assert candidate["model_selection"] == {"kind": "native_default"}
        assert candidate["requested_model"] is None and candidate["effective_model"] is None
    assert [gate["check"] for gate in record["gates"]] == ["text", "action", "permission", "cancel"]
    assert [gate["ordinal"] for gate in record["gates"]] == [1, 2, 3, 6]
    assert [gate["observation"]["actual_model"] for gate in record["gates"]] == [None] * 4
    assert [row["ordinal"] for row in record["submissions"]] == [1, 2, 3, 4, 5, 6]
    assert [row["passed"] for row in record["submissions"]] == [True, True, True, False, False, True]
    assert [row["candidate_sha256"] for row in record["submissions"]] == [
        original["candidate_sha256"],
    ] * 4 + [corrected["candidate_sha256"], diagnostic["candidate_sha256"]]
    sixth = record["submissions"][5]
    assert sixth["diagnostic_only"] is True and sixth["diagnostic_purpose"] == "cleanup_diagnostic_only"
    assert sixth["diagnostic_observation_complete"] is True
    assert sixth["diagnostic_publication"]["published"] is True
    assert sixth["diagnostic_publication"]["sidecar_sha256"] == sixth["diagnostic_sidecar_sha256"]
    assert [row["ordinal"] for row in record["replacement_authorizations"]] == [5, 6]
    assert record["reconciliation"]["excluded_failed_ordinals"] == [4, 5]
    assert record["budget"] == {
        "automatic_retries": 0,
        "consumed": 6,
        "max_per_gate": {"text": 2, "action": 2, "permission": 2, "cancel": 3},
        "max_replacements": 2,
        "max_total": 6,
        "replacements_used": 2,
    }
    assert record["limitations"]["prior_cleanup_failure_cause_known"] is False
    assert record["limitations"]["prior_cleanup_failure_resolved"] is False
    assert record["limitations"]["diagnostic_child_identity_known"] is False
    assert record["limitations"]["native_default_identifies_actual_model"] is False
    assert record["limitations"]["fulfillment_verified"] is False


def test_grok_generation_pin_checks_relocated_package_resource_without_native_calls(tmp_path, monkeypatch):
    import clink.native_generation as generation
    from clink.native_generation_families import DEFAULT_GROK_NATIVE_ADMISSION

    cli = "grok"
    admission = DEFAULT_GROK_NATIVE_ADMISSION
    resource = files("clink").joinpath(admission.evidence_path.removeprefix("clink/"))
    relocated_root = tmp_path / "installed"
    evidence = relocated_root / admission.evidence_path
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(resource.read_bytes())
    monkeypatch.setattr(generation, "__file__", str(relocated_root / "clink/native_generation.py"))
    monkeypatch.chdir(tmp_path)
    observed = []

    def binary_identity(executable):
        observed.append(executable)
        return admission.executable_sha256

    monkeypatch.setattr(generation, "executable_digest", binary_identity)
    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: pytest.fail("unexpected native process"))
    service = generation.NativeGenerationService()
    assert service._verify_admission("synthetic-identity-only", admission, cli=cli) == admission.executable_sha256
    assert observed == ["synthetic-identity-only"]
    assert evidence.read_bytes() == resource.read_bytes()

    evidence.write_bytes(evidence.read_bytes() + b"\n")
    with pytest.raises(generation.BackendError) as caught:
        service._verify_admission("synthetic-identity-only", admission, cli=cli)
    assert caught.value.code == "policy_unverified"
    assert observed == ["synthetic-identity-only"]
