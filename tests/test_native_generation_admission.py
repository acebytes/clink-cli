"""Production native-generation admission is exact scoped evidence, never candidate promotion."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

import clink.native_generation as module
from clink.contracts import BackendError
from clink.generation_policy import DEFAULT_ADMISSION, RUNTIME_BLOCKERS
from clink.native import NativeRuntime
from clink.native_generation import NativeGenerationService
from clink.native_generation_policy import DEFAULT_NATIVE_ADMISSION, NativeGenerationAdmission

ROOT = Path(__file__).resolve().parent.parent
RESOURCE = ROOT / "clink/resources/native-generation-codex-0.166.0-admission.json"


def test_production_evidence_accepts_three_gates_without_enabling_other_profiles():
    record = json.loads(RESOURCE.read_bytes())
    assert DEFAULT_NATIVE_ADMISSION.verified is True
    assert record["scope"] == "production_native_generation" and record["status"] == "accepted_native_generation"
    assert record["admission"]["independent_evidence_review"] == "accepted"
    assert {gate["check"] for gate in record["gates"]} == {"text", "tool", "cancel"}
    assert all(gate["observation"]["passed"] is True for gate in record["gates"])
    assert record["budget"]["top_level_submissions"] == 3 and record["budget"]["replacements_used"] == 0
    assert all(lineage["admission_reused"] is False for lineage in record["historical_lineages"])
    assert DEFAULT_ADMISSION.verified is False and RUNTIME_BLOCKERS
    assert NativeRuntime._policy("codex").verified is False


def test_default_production_pin_checks_exact_installed_resource_without_spawning(monkeypatch):
    observed = []

    def binary_identity(path):
        observed.append(path)
        return DEFAULT_NATIVE_ADMISSION.executable_sha256

    monkeypatch.setattr(module, "executable_digest", binary_identity)
    service = NativeGenerationService()
    assert (
        service._verify_admission("synthetic-identity-only", DEFAULT_NATIVE_ADMISSION)
        == DEFAULT_NATIVE_ADMISSION.executable_sha256
    )
    assert observed == ["synthetic-identity-only"]


@pytest.mark.parametrize("change", ["missing", "tampered", "candidate_scope", "wrong_profile"])
def test_missing_tampered_or_unrelated_evidence_cannot_match_production_pin(tmp_path, monkeypatch, change):
    path = tmp_path / "admission.json"
    if change != "missing":
        content = RESOURCE.read_bytes()
        if change == "tampered":
            content += b"\n"
        elif change in {"candidate_scope", "wrong_profile"}:
            record = json.loads(content)
            record["scope" if change == "candidate_scope" else "profile"] = (
                "candidate_native_generation" if change == "candidate_scope" else "native_task_v1"
            )
            content = json.dumps(record).encode()
        path.write_bytes(content)
    admission = replace(DEFAULT_NATIVE_ADMISSION, evidence_path=str(path))
    service = NativeGenerationService(admission=admission)
    monkeypatch.setattr(
        module, "executable_digest", lambda _: pytest.fail("unmatched evidence reached binary verification")
    )
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission)
    assert caught.value.code == "policy_unverified"


@pytest.mark.parametrize("field", ["executable_sha256", "recipe_sha256", "parser_sha256"])
def test_production_binary_recipe_and_parser_mismatches_fail_closed(monkeypatch, field):
    admission = replace(DEFAULT_NATIVE_ADMISSION, **{field: "0" * 64})
    service = NativeGenerationService(admission=admission)
    monkeypatch.setattr(module, "executable_digest", lambda _: DEFAULT_NATIVE_ADMISSION.executable_sha256)
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission)
    assert caught.value.code == "policy_unverified"


def test_process_local_candidate_does_not_mutate_any_production_default():
    candidate = NativeGenerationAdmission(version="synthetic-candidate", verified=True)
    service = NativeGenerationService(admission=candidate)
    assert service.admission is candidate and DEFAULT_NATIVE_ADMISSION is not candidate
    assert DEFAULT_NATIVE_ADMISSION.evidence_path == "clink/resources/native-generation-codex-0.166.0-admission.json"
    assert DEFAULT_ADMISSION.verified is False and NativeRuntime._policy("codex").verified is False
