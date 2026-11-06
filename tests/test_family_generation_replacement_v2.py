"""Verify reviewed schema-2 replacements without changing original accounting or making native calls."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts import verify_family_generation as verifier
from tests import test_family_generation_verifier as v1

isolated = v1.isolated
revision_check = v1.revision_check

ORIGINAL = {
    "grok": {
        "observed_version": "grok 1.0.70 (f50904eaee6c)",
        "executable_sha256": "5c8a688251bf61e35370b828b6a353b0a20fcc3f64000de8a91d426039f29cec",
        "recipe_sha256": "0bc8f09a9a4f068ba3e3b690e8dca9437db1b209f0de370bbd66eb52d49fadf1",
        "parser_sha256": "9effe99b6ebe6e9f5ec6276ed0a149b7b8a2920263f4523ac50978c7349f15f3",
    },
    "agy": {
        "observed_version": "1.4.12",
        "executable_sha256": "34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3",
        "recipe_sha256": "ded7cec0c01a2970ac1b15ef98d1f58c475b916b773b94db8aea31e98da47b00",
        "parser_sha256": "9d43a93edbfcd0061ed848ca3240bf29b8dde55cf4fe6e9432c82e8e49122a6b",
    },
}


def write(path, value):
    path.write_bytes(verifier.canonical_json(value))


def read(path):
    return json.loads(path.read_bytes())


def reference(path):
    return {"path": str(path), "sha256": verifier.digest(path.read_bytes())}


def reseal(context):
    """Simulate matching review transport after intentional fixture mutations."""
    root = context["root"]
    packet = read(context["packet"])
    reviewed = read(root / "input.json")
    reviewed["compatibility_binding_sha256"] = verifier.digest((root / "compatibility.json").read_bytes())
    write(root / "input.json", reviewed)
    review = read(root / "review.json")
    review["review"] = (
        "Synthetic semantic approval.\n\n"
        + verifier.APPROVAL_MARKER
        + verifier.digest((root / "input.json").read_bytes())
    )
    write(root / "review.json", review)
    runtime = read(root / "runtime.json")
    runtime["receipt_sha256"] = verifier.digest((root / "review.json").read_bytes())
    write(root / "runtime.json", runtime)
    binding = read(root / "source-binding.json")
    binding["receipt_sha256"] = runtime["receipt_sha256"]
    binding["runtime_attestation_sha256"] = verifier.digest((root / "runtime.json").read_bytes())
    write(root / "source-binding.json", binding)
    for key, filename in (
        ("diagnosis", "diagnosis.md"),
        ("correction", "correction.diff"),
        ("reviewed_input", "input.json"),
        ("independent_review", "review.json"),
        ("runtime_attestation", "runtime.json"),
        ("source_binding", "source-binding.json"),
        ("compatibility_binding", "compatibility.json"),
    ):
        packet[key] = reference(root / filename)
    write(context["packet"], packet)
    context["checksum"] = verifier.digest(context["packet"].read_bytes())


def prepare_v2(root, monkeypatch, family="grok", *, early_failure=False):
    original = ORIGINAL[family]
    monkeypatch.setattr(verifier, "installed_binding", lambda _: (str(root / "synthetic-cli"), original))
    monkeypatch.setattr(verifier, "implementation_digest", lambda: verifier.ORIGINAL_IMPLEMENTATION_SHA256)

    async def run(_family, check, *_args):
        return {"check": check, "passed": False if early_failure else check != "cancel" or family == "grok"}

    monkeypatch.setattr(verifier, "run_check", run)
    prior_path = None
    for check in (("text",) if early_failure else verifier.CHECKS):
        result = asyncio.run(
            verifier.verify(v1.arguments(family, label=check, check=check, live=True, prior_evidence=prior_path))
        )
        prior_path = v1.receipt(family, check)
    history = verifier.authoritative_history(family)
    old_manifest = read(Path(__file__).parent / "fixtures/family_generation_original_manifest.json")
    assert verifier.digest(verifier.canonical_json(old_manifest)) == verifier.ORIGINAL_IMPLEMENTATION_SHA256
    corrected_manifest = copy.deepcopy(old_manifest)
    for item in corrected_manifest["files"]:
        if item["path"] == "scripts/verify_family_generation.py":
            item["sha256"] = "a" * 64
    implementation = verifier.digest(verifier.canonical_json(corrected_manifest))
    monkeypatch.setattr(verifier, "implementation_digest", lambda: implementation)
    candidate = verifier.candidate_digest(
        {"family": family, **original, "implementation_source_sha256": implementation}
    )
    packet, _, review_repo = v1.replacement_packet(
        root, family=family, prior=verifier.digest(prior_path.read_bytes()), candidate=candidate
    )
    basis = "invalidated_cleanup_evidence" if family == "grok" else "failed_gate"
    fixed = {
        "schema_version": 2,
        "profile": verifier.PROFILE,
        "family": family,
        "prior_receipt_sha256": verifier.digest(prior_path.read_bytes()),
        "prior_ordinal": len(history),
        "check": history[-1]["entry"]["check"],
        "replacement_basis": basis,
    }
    test_evidence = root / "synthetic-tests.json"
    write(test_evidence, {"synthetic": True, "passed": True})
    equivalence = root / "equivalence.json"
    old_file = next(
        item["sha256"] for item in old_manifest["files"] if item["path"] == "scripts/verify_family_generation.py"
    )
    write(
        equivalence,
        {
            "schema_version": 1,
            "scope": "family_generation_production_equivalence",
            "original_source_commit": verifier.ORIGINAL_SOURCE_COMMIT,
            "original_implementation_source_sha256": verifier.ORIGINAL_IMPLEMENTATION_SHA256,
            "corrected_implementation_source_sha256": implementation,
            "original_components": old_manifest,
            "corrected_components": corrected_manifest,
            "delta": [
                {
                    "path": "scripts/verify_family_generation.py",
                    "original_sha256": old_file,
                    "corrected_sha256": "a" * 64,
                }
            ],
            "tests": [reference(test_evidence)],
        },
    )
    counterexample = root / "counterexample.json"
    write(counterexample, {"synthetic": True, "old_cancel_false_positive": True})
    non_cancel = root / "non-cancellation-proof.md"
    non_cancel.write_text("Synthetic reviewed control-flow proof; no historical event is manufactured.\n")
    compatibility = {
        "schema_version": 1,
        "scope": "family_generation_observer_compatibility",
        "profile": verifier.PROFILE,
        "family": family,
        "replacement_basis": basis,
        "prior_ordinal": len(history),
        "original_source_commit": verifier.ORIGINAL_SOURCE_COMMIT,
        "original_candidate_sha256": result["candidate_sha256"],
        **{key: value for key, value in original.items() if key.endswith("sha256")},
        "implementation_source_sha256": verifier.ORIGINAL_IMPLEMENTATION_SHA256,
        "original_records": [
            {
                "ordinal": item["entry"]["ordinal"],
                "check": item["entry"]["check"],
                "reported_passed": item["entry"]["passed"],
                "retained_eligible": item["entry"]["passed"] and item["entry"]["check"] != "cancel",
                "preflight": reference(item["directory"] / "preflight.json"),
                "reservation": reference(item["directory"] / "submitted.json"),
                "terminal": reference(item["directory"] / "receipt.json"),
            }
            for item in history
        ],
        "production_equivalence": reference(equivalence),
        "observer_counterexample": reference(counterexample),
        "non_cancellation_proof": reference(non_cancel),
    }
    write(root / "compatibility.json", compatibility)
    reviewed = read(root / "input.json")
    write(
        root / "input.json",
        {
            **fixed,
            "scope": "family_generation_replacement_input",
            "diagnosis_sha256": reviewed["diagnosis_sha256"],
            "correction_sha256": reviewed["correction_sha256"],
            "candidate_sha256": candidate,
            "source_revision": reviewed["source_revision"],
            "compatibility_binding_sha256": verifier.digest((root / "compatibility.json").read_bytes()),
        },
    )
    binding = read(root / "source-binding.json")
    binding.update({key: value for key, value in original.items() if key.endswith("sha256")})
    binding["implementation_source_sha256"] = implementation
    write(root / "source-binding.json", binding)
    write(packet, {**fixed, "scope": "family_generation_replacement_review"})
    context = {
        "root": root,
        "family": family,
        "packet": packet,
        "review_repo": review_repo,
        "prior_path": prior_path,
        "prior_sha256": verifier.digest(prior_path.read_bytes()),
        "ledger": result["submission_ledger"],
        "check": fixed["check"],
    }
    reseal(context)
    return context


def authorize(context):
    return verifier.submission_authorization(
        context["ledger"],
        context["check"],
        context["prior_sha256"],
        context["packet"],
        context["checksum"],
        context["family"],
        context["review_repo"],
    )


def reserve_fifth(context, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": context["check"], "passed": True}))
    return asyncio.run(
        verifier.verify(
            v1.arguments(
                context["family"],
                label="fifth",
                check=context["check"],
                live=True,
                prior_evidence=context["prior_path"],
                replacement_review=context["packet"],
                replacement_review_sha256=context["checksum"],
                review_repo=context["review_repo"],
            )
        )
    )


@pytest.mark.parametrize("family", verifier.FAMILIES)
def test_schema2_replacement_retains_original_bytes_and_exact_candidate_aggregation(
    isolated, monkeypatch, revision_check, family
):
    context = prepare_v2(isolated, monkeypatch, family)
    history = verifier.authoritative_history(family)
    originals = {path: path.read_bytes() for item in history for path in item["directory"].glob("*.json")}
    result = reserve_fifth(context, monkeypatch)
    assert result["gate_passed"] is True
    assert result["schema_version"] == 2
    assert result["submissions_consumed_total"] == 5
    assert result["all_family_generation_gates_passed"] is False
    assert result["submission_ledger"][:4] == context["ledger"]
    assert all(len(item) == 5 for item in result["submission_ledger"][:4])
    assert result["submission_ledger"][4]["replacement_basis"] == (
        "invalidated_cleanup_evidence" if family == "grok" else "failed_gate"
    )
    assert sum(item["replacement"] for item in result["submission_ledger"]) == 1
    assert all(path.read_bytes() == value for path, value in originals.items())
    revision_check.reset_mock()
    assert len(verifier.authoritative_history(family)) == 5
    revision_check.assert_not_called()
    with pytest.raises(ValueError, match="five"):
        verifier.submission_authorization(result["submission_ledger"], "cancel", None, None, None, family)


@pytest.mark.parametrize("family", verifier.FAMILIES)
def test_schema2_missing_fifth_terminal_is_spent_and_retained_proof_is_reconstructed(
    isolated, monkeypatch, revision_check, family
):
    context = prepare_v2(isolated, monkeypatch, family)
    reserve_fifth(context, monkeypatch)
    v1.receipt(family, "fifth").unlink()
    revision_check.reset_mock()
    ledger, _ = verifier.load_history(verifier.family_root(family) / "fifth/preflight.json", family)
    assert len(ledger) == 5 and ledger[-1]["passed"] is False
    assert ledger[-1]["replacement"] is True and "replacement_basis" in ledger[-1]
    revision_check.assert_not_called()
    with pytest.raises(ValueError, match="five"):
        verifier.submission_authorization(ledger, "cancel", None, None, None, family)


@pytest.mark.parametrize(
    "key,value",
    [
        ("family", "agy"),
        ("prior_ordinal", 3),
        ("prior_ordinal", True),
        ("replacement_basis", "another_purpose"),
        ("original_source_commit", "f" * 40),
        ("original_candidate_sha256", "f" * 64),
        ("implementation_source_sha256", "f" * 64),
        ("unexpected", True),
    ],
)
def test_reviewed_compatibility_cannot_change_closed_original_authority(
    isolated, monkeypatch, revision_check, key, value
):
    context = prepare_v2(isolated, monkeypatch)
    path = isolated / "compatibility.json"
    compatibility = read(path)
    compatibility[key] = value
    write(path, compatibility)
    reseal(context)
    with pytest.raises(ValueError):
        authorize(context)
    assert len(verifier.authoritative_history("grok")) == 4


@pytest.mark.parametrize(
    "change",
    [
        "unknown_packet_key",
        "schema1_basis",
        "wrong_basis",
        "wrong_ordinal",
        "wrong_check",
        "missing_marker",
        "stale_compatibility",
        "stale_source",
        "missing_original",
        "modified_prefix",
        "missing_reservation",
    ],
)
def test_schema2_rejects_stale_fabricated_or_missing_authority(isolated, monkeypatch, revision_check, change):
    context = prepare_v2(isolated, monkeypatch)
    packet = read(context["packet"])
    if change == "unknown_packet_key":
        packet["caller_verdict"] = "SHIP"
    elif change == "schema1_basis":
        packet["schema_version"] = 1
    elif change == "wrong_basis":
        packet["replacement_basis"] = "failed_gate"
    elif change == "wrong_ordinal":
        packet["prior_ordinal"] = 3
    elif change == "wrong_check":
        packet["check"] = "text"
    elif change == "missing_marker":
        review = read(isolated / "review.json")
        review["review"] = "Synthetic text without semantic approval."
        write(isolated / "review.json", review)
        packet["independent_review"] = reference(isolated / "review.json")
    elif change == "stale_compatibility":
        (isolated / "compatibility.json").write_text("changed after review")
    elif change == "stale_source":
        (isolated / "source-binding.json").write_text("changed after review")
    elif change == "missing_original":
        v1.receipt("grok", "cancel").unlink()
    elif change == "modified_prefix":
        context["ledger"][0]["passed"] = False
    elif change == "missing_reservation":
        (verifier.family_root("grok") / "permission/submitted.json").unlink()
    write(context["packet"], packet)
    context["checksum"] = verifier.digest(context["packet"].read_bytes())
    with pytest.raises(ValueError):
        authorize(context)


@pytest.mark.parametrize(
    "change",
    ["second_component", "different_paths", "wrong_manifest_digest", "wrong_delta", "missing_tests", "changed_tests"],
)
def test_equivalence_cannot_hide_production_or_evidence_changes(isolated, monkeypatch, revision_check, change):
    context = prepare_v2(isolated, monkeypatch)
    proof = read(isolated / "equivalence.json")
    if change == "second_component":
        proof["corrected_components"]["files"][0]["sha256"] = "f" * 64
    elif change == "different_paths":
        proof["corrected_components"]["files"].pop()
    elif change == "wrong_manifest_digest":
        proof["original_implementation_source_sha256"] = "f" * 64
    elif change == "wrong_delta":
        proof["delta"] = []
    elif change == "missing_tests":
        proof["tests"] = []
    elif change == "changed_tests":
        (isolated / "synthetic-tests.json").write_text("changed")
    if change in {"second_component", "different_paths"}:
        implementation = verifier.digest(verifier.canonical_json(proof["corrected_components"]))
        proof["corrected_implementation_source_sha256"] = implementation
        binding = read(isolated / "source-binding.json")
        binding["implementation_source_sha256"] = implementation
        candidate = verifier.candidate_digest(
            {"family": "grok", **ORIGINAL["grok"], "implementation_source_sha256": implementation}
        )
        binding["candidate_sha256"] = candidate
        write(isolated / "source-binding.json", binding)
        reviewed = read(isolated / "input.json")
        reviewed["candidate_sha256"] = candidate
        write(isolated / "input.json", reviewed)
        if change == "second_component":
            original_files = {item["path"]: item["sha256"] for item in proof["original_components"]["files"]}
            proof["delta"] = sorted(
                [
                    {
                        "path": item["path"],
                        "original_sha256": original_files[item["path"]],
                        "corrected_sha256": item["sha256"],
                    }
                    for item in proof["corrected_components"]["files"]
                    if original_files[item["path"]] != item["sha256"]
                ],
                key=lambda item: item["path"],
            )
    write(isolated / "equivalence.json", proof)
    compatibility = read(isolated / "compatibility.json")
    compatibility["production_equivalence"] = reference(isolated / "equivalence.json")
    write(isolated / "compatibility.json", compatibility)
    reseal(context)
    with pytest.raises(ValueError):
        authorize(context)


@pytest.mark.parametrize("change", ["proof", "summary", "basis", "prefix"])
def test_authoritative_reconstruction_rejects_changed_retained_proof(isolated, monkeypatch, revision_check, change):
    context = prepare_v2(isolated, monkeypatch)
    reserve_fifth(context, monkeypatch)
    if change == "proof":
        (isolated / "compatibility.json").write_text("changed after reservation")
    else:
        path = v1.receipt("grok", "fifth")
        record = read(path)
        if change == "summary":
            record["replacement_review"]["compatibility_binding_sha256"] = "f" * 64
        elif change == "basis":
            record["submission_ledger"][-1]["replacement_basis"] = "failed_gate"
        elif change == "prefix":
            record["submission_ledger"][0]["passed"] = False
        path.chmod(0o600)
        write(path, record)
    with pytest.raises(ValueError):
        verifier.authoritative_history("grok")


def test_passed_cancel_needs_new_semantic_proof_and_fake_snapshot_basis_is_not_authority(
    isolated, monkeypatch, revision_check
):
    context = prepare_v2(isolated, monkeypatch)
    with pytest.raises(ValueError, match="passed"):
        verifier.submission_authorization(context["ledger"], "cancel", None, None, None, "grok")
    fake = copy.deepcopy(context["ledger"])
    fake.append(
        {
            "ordinal": 5,
            "check": "cancel",
            "passed": False,
            "replacement": True,
            "reservation_sha256": "f" * 64,
            "replacement_basis": "invalidated_cleanup_evidence",
        }
    )
    verifier.validate_ledger(fake)
    record = read(context["prior_path"])
    record.update(schema_version=2, submission_ledger=fake, submissions_consumed_total=5)
    context["prior_path"].chmod(0o600)
    write(context["prior_path"], record)
    with pytest.raises(ValueError):
        verifier.load_history(context["prior_path"], "grok")


def test_schema1_proof_retains_exact_summary_and_packet_semantics(tmp_path, revision_check):
    packet, checksum, review_repo = v1.replacement_packet(tmp_path)
    before = packet.read_bytes()
    proof = verifier.submission_authorization([v1.entry()], "text", "a" * 64, packet, checksum, "grok", review_repo)
    assert set(proof) == {
        "packet_sha256",
        "failed_ordinal",
        "candidate_sha256",
        "diagnosis_sha256",
        "correction_sha256",
        "review_sha256",
        "source_binding_sha256",
        "runtime_attestation_sha256",
        "executable_sha256",
        "recipe_sha256",
        "parser_sha256",
        "implementation_source_sha256",
        "review_runtime_model_verified",
        "provenance",
    }
    assert packet.read_bytes() == before
    value = read(packet)
    value["compatibility_binding"] = reference(tmp_path / "diagnosis.md")
    write(packet, value)
    with pytest.raises(ValueError):
        verifier.submission_authorization(
            [v1.entry()], "text", "a" * 64, packet, verifier.digest(packet.read_bytes()), "grok", review_repo
        )


def test_schema2_basis_does_not_weaken_duplicate_replacement_or_gate_limits():
    prefix = [
        v1.entry(1, "text", True),
        v1.entry(2, "action", True),
        v1.entry(3, "permission", True),
        v1.entry(4, "cancel", True),
    ]
    replacement = {**v1.entry(5, "cancel", False, True), "replacement_basis": "invalidated_cleanup_evidence"}
    verifier.validate_ledger(prefix + [replacement])
    with pytest.raises(ValueError, match="five"):
        verifier.validate_ledger(
            prefix + [replacement, {**v1.entry(6, "cancel", False, True), "replacement_basis": "failed_gate"}]
        )
    with pytest.raises(ValueError, match="replacement|failed"):
        verifier.validate_ledger(
            [
                v1.entry(),
                {**v1.entry(2, passed=True, replacement=True), "replacement_basis": "failed_gate"},
                v1.entry(3, "action"),
                {**v1.entry(4, "action", True, True), "replacement_basis": "failed_gate"},
            ]
        )
    with pytest.raises(ValueError, match="Invalid|replacement"):
        verifier.validate_ledger(prefix + [{**replacement, "replacement_basis": "caller_approved"}])


@pytest.mark.parametrize("component", ["executable_sha256", "recipe_sha256", "parser_sha256"])
def test_semantic_review_cannot_authorize_a_different_execution_component(
    isolated, monkeypatch, revision_check, component
):
    context = prepare_v2(isolated, monkeypatch)
    binding = read(isolated / "source-binding.json")
    binding[component] = "f" * 64
    write(isolated / "source-binding.json", binding)
    reseal(context)
    with pytest.raises(ValueError, match="compatibility"):
        authorize(context)


def test_schema2_failed_gate_keeps_ordinary_continuation_semantics(isolated, monkeypatch, revision_check):
    context = prepare_v2(isolated, monkeypatch, "agy", early_failure=True)
    result = reserve_fifth(context, monkeypatch)
    assert result["submissions_consumed_total"] == 2
    assert result["submission_ledger"][0]["passed"] is False
    assert result["submission_ledger"][1]["replacement_basis"] == "failed_gate"

    async def run(_family, check, *_args):
        return {"check": check, "passed": True}

    monkeypatch.setattr(verifier, "run_check", run)
    prior = v1.receipt("agy", "fifth")
    for check in ("action", "permission", "cancel"):
        result = asyncio.run(
            verifier.verify(v1.arguments("agy", label="after-" + check, check=check, live=True, prior_evidence=prior))
        )
        prior = v1.receipt("agy", "after-" + check)
    assert result["submissions_consumed_total"] == 5
    assert result["all_family_generation_gates_passed"] is True
    assert len(verifier.authoritative_history("agy")) == 5
