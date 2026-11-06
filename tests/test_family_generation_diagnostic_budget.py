"""Exercise the one-shot diagnostic budget using synthetic, retained family evidence only."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import verify_family_generation as verifier
from tests import test_family_generation_replacement_v2 as v2
from tests import test_family_generation_verifier as v1

isolated = v1.isolated
revision_check = v1.revision_check

read = v2.read
write = v2.write
reference = v2.reference
SYNTHETIC_RUN_ID = "11111111-2222-3333-4444-555555555555"
SYNTHETIC_SERVICE_RESULT = {"synthetic": True, "status": "cancelled"}
SYNTHETIC_RESULT_SHA256 = verifier.digest(verifier.canonical_json(SYNTHETIC_SERVICE_RESULT))


def reseal(context):
    """Reseal deliberately synthetic review transport after a targeted fixture edit."""
    root = context["root"]
    packet = read(context["packet"])
    reviewed = read(root / "input.json")
    reviewed["compatibility_binding_sha256"] = verifier.digest((root / "compatibility.json").read_bytes())
    reviewed["user_authorization_sha256"] = verifier.digest((root / "user-authorization.json").read_bytes())
    write(root / "input.json", reviewed)
    review = read(root / "review.json")
    review["review"] = (
        "Synthetic diagnostic approval; no genuine user or review evidence.\n\n"
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
        ("user_authorization", "user-authorization.json"),
    ):
        packet[key] = reference(root / filename)
    write(context["packet"], packet)
    context["checksum"] = verifier.digest(context["packet"].read_bytes())


def prepare_five(root, monkeypatch):
    """Retain four original candidates and a failed schema-2 corrected cancellation."""
    context = v2.prepare_v2(root, monkeypatch, "agy")
    history = verifier.authoritative_history("agy")
    originals = {path: path.read_bytes() for item in history for path in item["directory"].glob("*.json")}
    legacy_proof = context["packet"].read_bytes()
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "cancel", "passed": False}))
    result = asyncio.run(
        verifier.verify(
            v1.arguments(
                "agy",
                label="fifth",
                check="cancel",
                live=True,
                prior_evidence=context["prior_path"],
                replacement_review=context["packet"],
                replacement_review_sha256=context["checksum"],
                review_repo=context["review_repo"],
            )
        )
    )
    assert result["schema_version"] == 2
    assert result["gate_passed"] is False
    assert result["submissions_consumed_total"] == 5
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert context["packet"].read_bytes() == legacy_proof
    history = verifier.authoritative_history("agy")
    assert len(history) == 5
    assert history[-1]["entry"]["replacement_basis"] == "failed_gate"
    assert history[-1]["entry"]["passed"] is False
    assert history[0]["preflight"]["candidate_sha256"] != history[-1]["preflight"]["candidate_sha256"]
    return {
        **context,
        "legacy_packet": context["packet"],
        "legacy_checksum": context["checksum"],
        "prior_path": v1.receipt("agy", "fifth"),
        "prior_sha256": verifier.digest(v1.receipt("agy", "fifth").read_bytes()),
        "ledger": result["submission_ledger"],
        "result": result,
        "history": history,
        "original_bytes": originals,
    }


def prepare_diagnostic(root, monkeypatch):
    """Build synthetic schema-3 authority without modifying the retained schema-2 packet."""
    previous = prepare_five(root, monkeypatch)
    directory = root / "sixth-review"
    directory.mkdir()
    fifth = previous["history"][-1]["preflight"]
    old_manifest = read(root / "equivalence.json")["corrected_components"]
    corrected = copy.deepcopy(old_manifest)
    corrected_verifier = verifier.digest(Path(verifier.__file__).read_bytes())
    for item in corrected["files"]:
        if item["path"] == "scripts/verify_family_generation.py":
            item["sha256"] = corrected_verifier
    implementation = verifier.digest(verifier.canonical_json(corrected))
    candidate = verifier.candidate_digest({**fifth, "implementation_source_sha256": implementation})
    monkeypatch.setattr(verifier, "implementation_digest", lambda: implementation)
    monkeypatch.setattr(verifier, "DIAGNOSTIC_PRIOR_RECEIPT_SHA256", previous["prior_sha256"])
    monkeypatch.setattr(verifier, "DIAGNOSTIC_PRIOR_IMPLEMENTATION_SHA256", fifth["implementation_source_sha256"])
    packet, _, review_repo = v1.replacement_packet(
        directory, family="agy", prior=previous["prior_sha256"], candidate=candidate
    )
    sidecar = root / "diagnostics/sixth-cleanup.json"
    fixed = {
        "schema_version": 3,
        "profile": verifier.PROFILE,
        "family": "agy",
        "prior_receipt_sha256": previous["prior_sha256"],
        "prior_ordinal": 5,
        "check": "cancel",
        "replacement_basis": "diagnostic_budget_extension",
        "diagnostic_purpose": "cleanup_diagnostic_only",
        "diagnostic_limits": {"total_calls": 6, "cancel_attempts": 3, "exceptional_replacements": 2},
        "prior_ledger_sha256": verifier.digest(verifier.canonical_json(previous["ledger"])),
        "diagnostic_sidecar": str(sidecar),
    }
    user_authorization = {
        "schema_version": 1,
        "synthetic": True,
        "answer": "Approve the one additional diagnostic",
        "family": "agy",
        "check": "cancel",
        "authorized_next_ordinal": 6,
        "cumulative_limits": {"total_calls": 6, "cancel_attempts": 3, "exceptional_replacements": 2},
        "bounded_accounting_change_authorized": True,
        "observer_baseline_commit": "d193759fd2a1c75c144e53e0f990a62415d794df",
    }
    write(directory / "user-authorization.json", user_authorization)
    monkeypatch.setattr(
        verifier, "DIAGNOSTIC_USER_AUTHORIZATION_SHA256", reference(directory / "user-authorization.json")["sha256"]
    )
    test_evidence = directory / "synthetic-tests.json"
    write(test_evidence, {"synthetic": True, "passed": True})
    components = {item["path"]: item["sha256"] for item in old_manifest["files"]}
    write(
        directory / "equivalence.json",
        {
            "schema_version": 1,
            "scope": "family_generation_production_equivalence",
            "original_source_commit": "ab01466c84ba3661e8807b54d26d53b78c94e3bf",
            "original_implementation_source_sha256": fifth["implementation_source_sha256"],
            "corrected_implementation_source_sha256": implementation,
            "original_components": old_manifest,
            "corrected_components": corrected,
            "delta": [
                {
                    "path": "scripts/verify_family_generation.py",
                    "original_sha256": components["scripts/verify_family_generation.py"],
                    "corrected_sha256": corrected_verifier,
                }
            ],
            "tests": [reference(test_evidence)],
        },
    )
    compatibility = {
        "schema_version": 1,
        "scope": "family_generation_diagnostic_compatibility",
        "profile": verifier.PROFILE,
        "family": "agy",
        "replacement_basis": "diagnostic_budget_extension",
        "prior_ordinal": 5,
        "prior_receipt_sha256": previous["prior_sha256"],
        "prior_ledger_sha256": fixed["prior_ledger_sha256"],
        "original_source_commit": "ab01466c84ba3661e8807b54d26d53b78c94e3bf",
        "original_candidate_sha256": fifth["candidate_sha256"],
        **{
            key: fifth[key]
            for key in ("executable_sha256", "recipe_sha256", "parser_sha256", "implementation_source_sha256")
        },
        "original_records": [
            {
                "ordinal": item["entry"]["ordinal"],
                "check": item["entry"]["check"],
                "reported_passed": item["entry"]["passed"],
                "candidate_sha256": item["preflight"]["candidate_sha256"],
                "preflight": reference(item["directory"] / "preflight.json"),
                "reservation": reference(item["directory"] / "submitted.json"),
                "terminal": reference(item["directory"] / "receipt.json"),
            }
            for item in previous["history"]
        ],
        "production_equivalence": reference(directory / "equivalence.json"),
        "fixture_authority": {
            **{key: fifth[key] for key in ("executable_sha256", "recipe_sha256", "parser_sha256")},
            "native_permissions_changed": False,
            "timeout_seconds": 120,
            "fixture_components": [
                {"path": name, "sha256": components[name]}
                for name in (
                    "scripts/verify_native_adapters.py",
                    "scripts/native_agy_fixture.py",
                    "scripts/native_mcp_fixture.py",
                    "scripts/native_fixture_trust.py",
                )
            ],
        },
    }
    write(directory / "compatibility.json", compatibility)
    reviewed = read(directory / "input.json")
    write(
        directory / "input.json",
        {
            **fixed,
            "scope": "family_generation_replacement_input",
            "diagnosis_sha256": reviewed["diagnosis_sha256"],
            "correction_sha256": reviewed["correction_sha256"],
            "candidate_sha256": candidate,
            "source_revision": reviewed["source_revision"],
            "compatibility_binding_sha256": reference(directory / "compatibility.json")["sha256"],
            "user_authorization_sha256": reference(directory / "user-authorization.json")["sha256"],
        },
    )
    binding = read(directory / "source-binding.json")
    binding.update({key: fifth[key] for key in ("executable_sha256", "recipe_sha256", "parser_sha256")})
    binding["implementation_source_sha256"] = implementation
    write(directory / "source-binding.json", binding)
    write(packet, {**fixed, "scope": "family_generation_replacement_review"})
    context = {**previous, "root": directory, "packet": packet, "review_repo": review_repo, "sidecar": sidecar}
    reseal(context)
    context["retained_bytes"] = {
        path: path.read_bytes() for item in previous["history"] for path in item["directory"].glob("*.json")
    }
    context["legacy_bytes"] = previous["legacy_packet"].read_bytes()
    return context


def authorize(context, **changes):
    values = {
        "ledger": context["ledger"],
        "check": "cancel",
        "prior": context["prior_sha256"],
        "review": context["packet"],
        "review_sha256": context["checksum"],
        "family": "agy",
        "review_repo": context["review_repo"],
        "diagnostic_sidecar": context["sidecar"],
    }
    values.update(changes)
    return verifier.submission_authorization(**values)


def reserve_sixth(context, monkeypatch, *, passed=False, failure=None, diagnostic=None, publication=None, **changes):
    def capture(*_args, **kwargs):
        actual_publication = {
            "published": False,
            "run_id": SYNTHETIC_RUN_ID,
            "result_sha256": SYNTHETIC_RESULT_SHA256,
            "sidecar_sha256": None,
        }
        if diagnostic is not None:
            kwargs["diagnostic_sidecar"].parent.mkdir(parents=True, exist_ok=True)
            sidecar_sha256 = verifier.persist(kwargs["diagnostic_sidecar"], diagnostic)
            actual_publication.update(published=True, sidecar_sha256=sidecar_sha256)
        return {
            "check": "cancel",
            "passed": passed,
            "diagnostic_publication": actual_publication if publication is None else copy.deepcopy(publication),
        }

    dispatch = AsyncMock(side_effect=capture if failure is None else failure)
    monkeypatch.setattr(verifier, "run_check", dispatch)
    values = {
        "label": "sixth",
        "check": "cancel",
        "live": True,
        "timeout": 120,
        "prior_evidence": context["prior_path"],
        "replacement_review": context["packet"],
        "replacement_review_sha256": context["checksum"],
        "review_repo": context["review_repo"],
        "diagnostic_sidecar": context["sidecar"],
    }
    values.update(changes)
    result = asyncio.run(verifier.verify(v1.arguments("agy", **values)))
    return result, dispatch


def synthetic_sidecar(context, *, capture_failed=False):
    source_root = Path(verifier.__file__).resolve().parents[1]
    return {
        "schema_version": 1,
        "kind": "family_cleanup_diagnostics",
        "run_id": SYNTHETIC_RUN_ID,
        "source": {
            "baseline_commit": verifier.DIAGNOSTIC_BASELINE_COMMIT,
            **{
                key: verifier.digest((source_root / name).read_bytes())
                for key, name in (
                    ("runner_sha256", "clink/runner.py"),
                    ("verifier_sha256", "scripts/verify_family_generation.py"),
                    ("native_diagnostics_sha256", "scripts/native_diagnostics.py"),
                )
            },
        },
        "executable_sha256": context["history"][-1]["preflight"]["executable_sha256"],
        "result_sha256": SYNTHETIC_RESULT_SHA256,
        "capture_failed": capture_failed,
        "exception": None,
        "processes": {"leader": [], "fixture_child": []},
    }


def test_synthetic_prefix_retains_mixed_candidates_and_failed_fifth(isolated, monkeypatch, revision_check):
    context = prepare_five(isolated, monkeypatch)
    assert [item["passed"] for item in context["ledger"]] == [True, True, True, False, False]
    assert sum(item["replacement"] for item in context["ledger"]) == 1
    assert context["result"]["all_family_generation_gates_passed"] is False
    assert context["result"]["replacement_review"]["schema_version"] == 2
    revision_check.reset_mock()
    assert verifier.load_history(context["prior_path"], "agy")[0] == context["ledger"]
    revision_check.assert_not_called()


def test_schema3_proof_binds_mixed_five_record_prefix_and_one_fixed_diagnostic(isolated, monkeypatch, revision_check):
    context = prepare_diagnostic(isolated, monkeypatch)
    proof = authorize(context)
    assert proof["schema_version"] == 3
    assert proof["prior_ordinal"] == 5
    assert proof["diagnostic_purpose"] == "cleanup_diagnostic_only"
    assert proof["diagnostic_limits"] == {"total_calls": 6, "cancel_attempts": 3, "exceptional_replacements": 2}
    assert proof["prior_ledger_sha256"] == verifier.digest(verifier.canonical_json(context["ledger"]))
    assert proof["diagnostic_sidecar"] == str(context["sidecar"])
    assert proof["user_authorization_sha256"] == verifier.DIAGNOSTIC_USER_AUTHORIZATION_SHA256
    assert proof["review_runtime_model_verified"] is False
    assert all(path.read_bytes() == raw for path, raw in context["retained_bytes"].items())
    assert context["legacy_packet"].read_bytes() == context["legacy_bytes"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("family", "grok"),
        ("check", "text"),
        ("prior_receipt_sha256", "f" * 64),
        ("prior_ordinal", 4),
        ("prior_ordinal", True),
        ("replacement_basis", "failed_gate"),
        ("diagnostic_purpose", "production_admission"),
        ("diagnostic_limits", {"total_calls": 7, "cancel_attempts": 3, "exceptional_replacements": 2}),
        ("diagnostic_limits", {"total_calls": 6.0, "cancel_attempts": 3, "exceptional_replacements": 2}),
        ("prior_ledger_sha256", "f" * 64),
        ("diagnostic_sidecar", "relative-sidecar.json"),
        ("unexpected", True),
    ],
)
def test_schema3_review_cannot_change_fixed_diagnostic_authority(isolated, monkeypatch, revision_check, key, value):
    context = prepare_diagnostic(isolated, monkeypatch)
    for filename in ("packet.json", "input.json"):
        changed = read(context["root"] / filename)
        changed[key] = value
        write(context["root"] / filename, changed)
    reseal(context)
    with pytest.raises(ValueError):
        authorize(context)
    assert len(verifier.authoritative_history("agy")) == 5


@pytest.mark.parametrize("change", ["absent", "stale", "repinned", "wrong_answer", "wrong_ordinal", "wrong_limits"])
def test_schema3_needs_the_exact_explicit_user_authorization(isolated, monkeypatch, revision_check, change):
    context = prepare_diagnostic(isolated, monkeypatch)
    authorization = context["root"] / "user-authorization.json"
    if change == "absent":
        packet = read(context["packet"])
        packet.pop("user_authorization")
        write(context["packet"], packet)
        context["checksum"] = verifier.digest(context["packet"].read_bytes())
    else:
        value = read(authorization)
        if change == "wrong_answer":
            value["answer"] = "Maybe"
        elif change == "wrong_ordinal":
            value["authorized_next_ordinal"] = True
        elif change == "wrong_limits":
            value["cumulative_limits"]["total_calls"] = 7
        else:
            value["synthetic"] = "changed after pin"
        write(authorization, value)
        if change != "stale":
            reseal(context)
        if change.startswith("wrong_"):
            monkeypatch.setattr(verifier, "DIAGNOSTIC_USER_AUTHORIZATION_SHA256", reference(authorization)["sha256"])
    with pytest.raises(ValueError):
        authorize(context)


@pytest.mark.parametrize(
    "change",
    [
        "missing_sidecar",
        "different_sidecar",
        "existing_sidecar",
        "wrong_gate",
        "wrong_family",
        "wrong_prior",
        "legacy_proof",
        "modified_ledger",
    ],
)
def test_schema3_submission_rejects_mismatched_selection_or_legacy_authority(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    changes = {}
    if change == "missing_sidecar":
        changes["diagnostic_sidecar"] = None
    elif change == "different_sidecar":
        changes["diagnostic_sidecar"] = isolated / "different-cleanup.json"
    elif change == "existing_sidecar":
        context["sidecar"].parent.mkdir()
        context["sidecar"].write_text("Synthetic existing sidecar.\n")
    elif change == "wrong_gate":
        changes["check"] = "text"
    elif change == "wrong_family":
        changes["family"] = "grok"
    elif change == "wrong_prior":
        changes["prior"] = "f" * 64
    elif change == "legacy_proof":
        changes.update(review=context["legacy_packet"], review_sha256=context["legacy_checksum"])
    elif change == "modified_ledger":
        changes["ledger"] = copy.deepcopy(context["ledger"])
        changes["ledger"][0]["reservation_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        authorize(context, **changes)


@pytest.mark.parametrize(
    "change",
    [
        "source_commit",
        "fifth_candidate",
        "fifth_implementation",
        "history_candidate",
        "history_outcome",
        "missing_history",
        "relocated_history",
        "permissions",
        "timeout",
        "fixture",
        "equivalence",
        "missing_tests",
    ],
)
def test_schema3_compatibility_cannot_rewrite_history_or_execution_authority(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    path = context["root"] / "compatibility.json"
    value = read(path)
    if change == "source_commit":
        value["original_source_commit"] = verifier.ORIGINAL_SOURCE_COMMIT
    elif change == "fifth_candidate":
        value["original_candidate_sha256"] = value["original_records"][0]["candidate_sha256"]
    elif change == "fifth_implementation":
        value["implementation_source_sha256"] = verifier.ORIGINAL_IMPLEMENTATION_SHA256
    elif change == "history_candidate":
        value["original_records"][0]["candidate_sha256"] = value["original_candidate_sha256"]
    elif change == "history_outcome":
        value["original_records"][4]["reported_passed"] = True
    elif change == "missing_history":
        value["original_records"].pop()
    elif change == "relocated_history":
        alias = context["root"] / "copied-preflight.json"
        alias.write_bytes(Path(value["original_records"][0]["preflight"]["path"]).read_bytes())
        value["original_records"][0]["preflight"] = reference(alias)
    elif change == "permissions":
        value["fixture_authority"]["native_permissions_changed"] = True
    elif change == "timeout":
        value["fixture_authority"]["timeout_seconds"] = 121
    elif change == "fixture":
        value["fixture_authority"]["fixture_components"][0]["sha256"] = "f" * 64
    else:
        equivalence = context["root"] / "equivalence.json"
        proof = read(equivalence)
        if change == "equivalence":
            proof["original_source_commit"] = verifier.ORIGINAL_SOURCE_COMMIT
        else:
            proof["tests"] = []
        write(equivalence, proof)
        value["production_equivalence"] = reference(equivalence)
    write(path, value)
    reseal(context)
    with pytest.raises(ValueError):
        authorize(context)


@pytest.mark.parametrize(
    "change", ["model", "effort", "provider", "sandbox", "approval", "source", "marker", "session"]
)
def test_schema3_preserves_the_existing_review_runtime_source_and_marker_contract(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    root = context["root"]
    if change in {"model", "effort", "provider", "sandbox", "approval"}:
        value = read(root / "runtime.json")
        field, replacement = {
            "model": ("model", "gpt-6-astra"),
            "effort": ("effort", "ultra"),
            "provider": ("model_provider", "another-provider"),
            "sandbox": ("sandbox_type", "workspace-write"),
            "approval": ("approval_policy", "on-request"),
        }[change]
        value["effective_runtime"][field] = replacement
        write(root / "runtime.json", value)
    elif change == "source":
        value = read(root / "source-binding.json")
        value["candidate_sha256"] = "f" * 64
        write(root / "source-binding.json", value)
    elif change == "session":
        value = read(root / "runtime.json")
        value["session_id"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        write(root / "runtime.json", value)
    reseal(context)
    if change == "marker":
        value = read(root / "review.json")
        value["review"] = "Synthetic SHIP claim without exact canonical input approval."
        write(root / "review.json", value)
        packet = read(context["packet"])
        packet["independent_review"] = reference(root / "review.json")
        write(context["packet"], packet)
        context["checksum"] = verifier.digest(context["packet"].read_bytes())
    with pytest.raises(ValueError):
        authorize(context)


@pytest.mark.parametrize("passed", [False, True])
def test_schema3_retains_actual_observed_result_without_borrowing_old_candidate_passes(
    isolated, monkeypatch, revision_check, passed
):
    context = prepare_diagnostic(isolated, monkeypatch)
    result, dispatch = reserve_sixth(context, monkeypatch, passed=passed)
    dispatch.assert_awaited_once()
    assert result["schema_version"] == 3
    assert result["gate_passed"] is passed
    assert result["observation"]["passed"] is passed
    assert result["submission_ledger"][-1]["passed"] is passed
    assert result["submissions_consumed_total"] == 6
    assert result["submission_allowance_total"] == 6
    assert result["maximum_cancellation_submissions"] == 3
    assert result["maximum_replacements"] == 2
    assert result["diagnostic_only"] is True
    assert result["all_family_generation_gates_passed"] is False
    assert result["diagnostic_observation_complete"] is False
    assert result["diagnostic_sidecar_evidence"] is None
    assert result["diagnostic_publication"] == result["observation"]["diagnostic_publication"]
    assert result["diagnostic_publication"]["published"] is False
    assert result["automatic_retries"] == 0
    assert result["production_admission_changed"] is False
    assert result["native_task_admission_changed"] is False
    assert result["submission_ledger"][:5] == context["ledger"]
    assert sum(item["replacement"] for item in result["submission_ledger"]) == 2
    assert sum(item["check"] == "cancel" for item in result["submission_ledger"]) == 3
    assert all(path.read_bytes() == raw for path, raw in context["retained_bytes"].items())
    assert context["legacy_packet"].read_bytes() == context["legacy_bytes"]
    revision_check.reset_mock()
    assert verifier.load_history(v1.receipt("agy", "sixth"), "agy")[0] == result["submission_ledger"]
    revision_check.assert_not_called()


@pytest.mark.parametrize("outcome", ["passed", "failed", "interrupted"])
def test_exhausted_diagnostic_history_rejects_nonlive_before_creating_evidence(
    isolated, monkeypatch, revision_check, outcome
):
    context = prepare_diagnostic(isolated, monkeypatch)
    reserve_sixth(
        context,
        monkeypatch,
        passed=outcome == "passed",
        failure=RuntimeError("Synthetic interrupted sixth") if outcome == "interrupted" else None,
    )
    prior = v1.receipt("agy", "sixth")
    if outcome == "interrupted":
        prior.unlink()
        prior = prior.with_name("preflight.json")
    ledger, prior_sha256 = verifier.load_history(prior, "agy")
    assert len(ledger) == 6 and ledger[-1]["passed"] is (outcome == "passed")
    with pytest.raises(ValueError):
        verifier.validate_ledger(ledger)
    assert prior_sha256 == verifier.digest(prior.read_bytes())
    paths = set(isolated.rglob("*"))
    original_bytes = {path: path.read_bytes() for path in paths if path.is_file()}
    original_authorization = verifier.submission_authorization
    blocked = {}
    for name in (
        "evidence_directory",
        "installed_binding",
        "persist",
        "prepare_fixture",
        "submission_authorization",
    ):
        blocked[name] = Mock(side_effect=AssertionError(f"Exhausted nonlive history reached {name}"))
        monkeypatch.setattr(verifier, name, blocked[name])
    dispatch = AsyncMock(side_effect=AssertionError("Exhausted nonlive history dispatched"))
    monkeypatch.setattr(verifier, "run_check", dispatch)
    with pytest.raises(ValueError, match="Exhausted diagnostic history does not support a non-live evidence record"):
        asyncio.run(
            verifier.verify(v1.arguments("agy", label="after-sixth-preflight", check="cancel", prior_evidence=prior))
        )
    for guard in blocked.values():
        guard.assert_not_called()
    dispatch.assert_not_called()
    assert set(isolated.rglob("*")) == paths
    assert all(path.read_bytes() == content for path, content in original_bytes.items())
    assert verifier.load_history(prior, "agy") == (ledger, prior_sha256)
    monkeypatch.setattr(verifier, "submission_authorization", original_authorization)
    with pytest.raises(ValueError):
        asyncio.run(
            verifier.verify(
                v1.arguments("agy", label="seventh-after-refusal", check="cancel", live=True, prior_evidence=prior)
            )
        )
    dispatch.assert_not_called()
    assert set(isolated.rglob("*")) == paths
    assert all(path.read_bytes() == content for path, content in original_bytes.items())


@pytest.mark.parametrize("consumed", range(6))
def test_ordinary_nonlive_history_through_five_keeps_legacy_limits_and_roundtrips(
    isolated, monkeypatch, revision_check, consumed
):
    prior = None
    if consumed == 5:
        prior = prepare_five(isolated, monkeypatch)["prior_path"]
    else:
        for check in verifier.CHECKS[:consumed]:
            monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": check, "passed": True}))
            asyncio.run(verifier.verify(v1.arguments("agy", label=check, check=check, live=True, prior_evidence=prior)))
            prior = v1.receipt("agy", check)
    ledger = verifier.load_history(prior, "agy")[0]
    reservations = set(verifier.family_root("agy").glob("*/submitted.json"))
    original_bytes = {path: path.read_bytes() for path in verifier.family_root("agy").glob("*/*.json")}
    dispatch = AsyncMock(side_effect=AssertionError("Ordinary nonlive history dispatched"))
    fixture = Mock(side_effect=AssertionError("Ordinary nonlive history prepared a fixture"))
    authorization = Mock(side_effect=AssertionError("Ordinary nonlive history requested new authorization"))
    monkeypatch.setattr(verifier, "run_check", dispatch)
    monkeypatch.setattr(verifier, "prepare_fixture", fixture)
    monkeypatch.setattr(verifier, "submission_authorization", authorization)
    result = asyncio.run(
        verifier.verify(v1.arguments("agy", label="ordinary-preflight", check="cancel", prior_evidence=prior))
    )
    assert result["schema_version"] == 1
    assert result["submission_allowance_total"] == 5
    assert result["maximum_submissions_per_gate"] == 2
    assert result["maximum_replacements"] == 1
    assert result["automatic_retries"] == 0
    assert result["submissions_consumed_total"] == consumed
    assert result["submissions_in_this_invocation"] == 0
    assert result["live_requested"] is result["live_submitted"] is result["gate_passed"] is False
    assert result["replacement_review"] is None and "observation" not in result
    assert verifier.ledger_from(result, "agy") == ledger
    terminal = v1.receipt("agy", "ordinary-preflight")
    assert verifier.ledger_from(read(terminal.with_name("preflight.json")), "agy") == ledger
    assert verifier.load_history(terminal, "agy") == (ledger, verifier.digest(terminal.read_bytes()))
    assert set(verifier.family_root("agy").glob("*/submitted.json")) == reservations
    assert all(path.read_bytes() == content for path, content in original_bytes.items())
    dispatch.assert_not_called()
    fixture.assert_not_called()
    authorization.assert_not_called()


@pytest.mark.parametrize("interrupted", [False, True])
def test_schema3_reservation_remains_consumed_without_a_terminal_and_forbids_replay_or_seventh(
    isolated, monkeypatch, revision_check, interrupted
):
    context = prepare_diagnostic(isolated, monkeypatch)
    result, dispatch = reserve_sixth(context, monkeypatch, failure=RuntimeError("Synthetic interruption"))
    dispatch.assert_awaited_once()
    assert result["error_class"] == "RuntimeError"
    if interrupted:
        v1.receipt("agy", "sixth").unlink()
    prior = verifier.family_root("agy") / "sixth" / ("preflight.json" if interrupted else "receipt.json")
    revision_check.reset_mock()
    ledger, _ = verifier.load_history(prior, "agy")
    assert len(ledger) == 6
    assert ledger[-1]["passed"] is False
    assert ledger[-1]["replacement_basis"] == "diagnostic_budget_extension"
    revision_check.assert_not_called()
    with pytest.raises(ValueError):
        authorize(context)
    with pytest.raises(ValueError):
        authorize(context, ledger=ledger)
    with pytest.raises(ValueError):
        verifier.validate_ledger(ledger + [{**ledger[-1], "ordinal": 7}], diagnostic=True)
    with pytest.raises(ValueError):
        reserve_sixth(context, monkeypatch, label="seventh", prior_evidence=prior)
    assert not (verifier.family_root("agy") / "seventh/submitted.json").exists()


@pytest.mark.parametrize("capture_failed", [False, True])
def test_schema3_reconstructs_retained_sidecar_with_honest_capture_status(
    isolated, monkeypatch, revision_check, capture_failed
):
    context = prepare_diagnostic(isolated, monkeypatch)
    diagnostic = synthetic_sidecar(context, capture_failed=capture_failed)
    assert verifier.valid_cleanup_diagnostic_sidecar(diagnostic)
    result, dispatch = reserve_sixth(context, monkeypatch, passed=True, diagnostic=diagnostic)
    dispatch.assert_awaited_once()
    assert result["gate_passed"] is True
    assert result["diagnostic_observation_complete"] is not capture_failed
    assert result["diagnostic_sidecar_evidence"] == reference(context["sidecar"])
    assert result["diagnostic_publication"] == result["observation"]["diagnostic_publication"]
    assert result["diagnostic_publication"] == {
        "published": True,
        "run_id": SYNTHETIC_RUN_ID,
        "result_sha256": SYNTHETIC_RESULT_SHA256,
        "sidecar_sha256": reference(context["sidecar"])["sha256"],
    }
    revision_check.reset_mock()
    assert verifier.load_history(v1.receipt("agy", "sixth"), "agy")[0] == result["submission_ledger"]
    revision_check.assert_not_called()
    context["sidecar"].chmod(0o600)
    context["sidecar"].write_text("Synthetic changed sidecar after reservation.\n")
    with pytest.raises(ValueError):
        verifier.authoritative_history("agy")


@pytest.mark.parametrize("change", ["malformed", "wrong_executable", "wrong_source"])
def test_schema3_bad_sidecar_never_claims_complete_observations_or_changes_actual_pass(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    diagnostic = synthetic_sidecar(context)
    if change == "malformed":
        diagnostic["unexpected"] = True
    elif change == "wrong_executable":
        diagnostic["executable_sha256"] = "f" * 64
    else:
        diagnostic["source"]["verifier_sha256"] = "f" * 64
    result, dispatch = reserve_sixth(context, monkeypatch, passed=True, diagnostic=diagnostic)
    dispatch.assert_awaited_once()
    assert result["submissions_consumed_total"] == 6
    assert result["gate_passed"] is True
    assert result["diagnostic_observation_complete"] is False
    assert result["diagnostic_sidecar_evidence"] is None
    assert verifier.load_history(v1.receipt("agy", "sixth"), "agy")[0] == result["submission_ledger"]
    with pytest.raises(ValueError):
        authorize(context, ledger=result["submission_ledger"])


@pytest.mark.parametrize("change", ["wrong_run", "wrong_result", "failed_publication", "publication_collision"])
def test_schema3_finalization_requires_actual_run_result_and_successful_publication(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    diagnostic = synthetic_sidecar(context)
    publication = None
    if change == "wrong_run":
        diagnostic["run_id"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    elif change == "wrong_result":
        diagnostic["result_sha256"] = "f" * 64
    else:
        publication = {
            "published": False,
            "run_id": SYNTHETIC_RUN_ID,
            "result_sha256": SYNTHETIC_RESULT_SHA256,
            "sidecar_sha256": None,
        }
        if change == "failed_publication":
            diagnostic = None
    result, dispatch = reserve_sixth(context, monkeypatch, passed=True, diagnostic=diagnostic, publication=publication)
    dispatch.assert_awaited_once()
    assert result["gate_passed"] is True
    assert result["observation"]["passed"] is True
    assert result["submission_ledger"][-1]["passed"] is True
    assert result["submissions_consumed_total"] == 6
    assert result["diagnostic_observation_complete"] is False
    assert result["diagnostic_sidecar_evidence"] is None
    assert result["diagnostic_publication"] == result["observation"]["diagnostic_publication"]
    assert result["diagnostic_publication"]["run_id"] == SYNTHETIC_RUN_ID
    assert result["diagnostic_publication"]["result_sha256"] == SYNTHETIC_RESULT_SHA256
    assert result["automatic_retries"] == 0
    assert verifier.load_history(v1.receipt("agy", "sixth"), "agy")[0] == result["submission_ledger"]
    with pytest.raises(ValueError):
        authorize(context, ledger=result["submission_ledger"])


@pytest.mark.parametrize(
    "change",
    [
        "summary",
        "terminal_source",
        "terminal_sidecar",
        "terminal_limits",
        "terminal_complete",
        "terminal_extra_field",
        "terminal_missing_field",
        "ordinary_gate_limit",
        "per_invocation_boolean",
        "automatic_retry",
        "production_admission",
        "native_task_admission",
        "candidate_admission_scope",
        "diagnostic_source_summary",
        "reservation",
        "user_authorization",
        "legacy_proof",
    ],
)
def test_schema3_reconstruction_revalidates_retained_proof_source_and_record_metadata(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    reserve_sixth(context, monkeypatch)
    if change == "user_authorization":
        (context["root"] / "user-authorization.json").write_text("Synthetic changed authorization.\n")
    elif change == "legacy_proof":
        context["legacy_packet"].write_text("Synthetic changed fifth proof.\n")
    else:
        path = verifier.family_root("agy") / "sixth" / ("submitted.json" if change == "reservation" else "receipt.json")
        record = read(path)
        if change == "summary":
            record["replacement_review"]["prior_ledger_sha256"] = "f" * 64
        elif change == "terminal_source":
            record["implementation_source_sha256"] = "f" * 64
        elif change == "terminal_sidecar":
            record["diagnostic_sidecar"] = str(isolated / "different-cleanup.json")
        elif change == "terminal_limits":
            record["diagnostic_limits"]["total_calls"] = 7
        elif change == "terminal_complete":
            record["diagnostic_observation_complete"] = True
        elif change == "terminal_extra_field":
            record["caller_approval"] = True
        elif change == "terminal_missing_field":
            record.pop("diagnostic_purpose")
        elif change == "ordinary_gate_limit":
            record["maximum_submissions_per_gate"] = 3
        elif change == "per_invocation_boolean":
            record["maximum_submissions_per_invocation"] = True
        elif change == "automatic_retry":
            record["automatic_retries"] = 1
        elif change == "production_admission":
            record["production_admission_changed"] = True
        elif change == "native_task_admission":
            record["native_task_admission_changed"] = True
        elif change == "candidate_admission_scope":
            record["candidate_admission_override"] = "production"
        elif change == "diagnostic_source_summary":
            record["replacement_review"]["diagnostic_source_sha256"]["verifier_sha256"] = "f" * 64
        elif change == "reservation":
            record["user_authorization_sha256"] = "f" * 64
        path.chmod(0o600)
        write(path, record)
    with pytest.raises(ValueError):
        verifier.authoritative_history("agy")


def test_schema3_changed_timeout_or_unreviewed_execution_source_stops_before_reservation(
    isolated, monkeypatch, revision_check
):
    context = prepare_diagnostic(isolated, monkeypatch)
    with pytest.raises(ValueError):
        reserve_sixth(context, monkeypatch, timeout=119)
    assert not (verifier.family_root("agy") / "sixth/submitted.json").exists()
    revision_check.side_effect = ValueError("Synthetic unreviewed execution source")
    with pytest.raises(ValueError):
        reserve_sixth(context, monkeypatch)
    assert not (verifier.family_root("agy") / "sixth/submitted.json").exists()


def test_schema3_interrupted_terminal_write_retains_a_spent_sixth_reservation(isolated, monkeypatch, revision_check):
    context = prepare_diagnostic(isolated, monkeypatch)
    original_persist = verifier.persist

    def interrupt_terminal(path, value):
        if path.parent.name == "sixth" and path.name == "receipt.json":
            raise OSError("Synthetic interruption before terminal persistence")
        return original_persist(path, value)

    monkeypatch.setattr(verifier, "persist", interrupt_terminal)
    with pytest.raises(OSError):
        reserve_sixth(context, monkeypatch)
    assert not v1.receipt("agy", "sixth").exists()
    revision_check.reset_mock()
    prior = verifier.family_root("agy") / "sixth/preflight.json"
    ledger, _ = verifier.load_history(prior, "agy")
    assert len(ledger) == 6 and ledger[-1]["passed"] is False
    assert ledger[-1]["replacement_basis"] == "diagnostic_budget_extension"
    revision_check.assert_not_called()
    with pytest.raises(ValueError):
        authorize(context, ledger=ledger)
    assert len(verifier.authoritative_history("agy")) == 6


@pytest.mark.parametrize("change", ["source", "run_id", "result_sha256"])
def test_schema3_retained_sidecar_cannot_repin_a_different_execution_binding(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    reserve_sixth(context, monkeypatch, diagnostic=synthetic_sidecar(context))
    diagnostic = read(context["sidecar"])
    if change == "source":
        diagnostic["source"]["verifier_sha256"] = "f" * 64
    elif change == "run_id":
        diagnostic["run_id"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    else:
        diagnostic["result_sha256"] = "f" * 64
    context["sidecar"].chmod(0o600)
    write(context["sidecar"], diagnostic)
    terminal = v1.receipt("agy", "sixth")
    record = read(terminal)
    record["diagnostic_sidecar_evidence"] = reference(context["sidecar"])
    for publication in (record["diagnostic_publication"], record["observation"]["diagnostic_publication"]):
        publication["sidecar_sha256"] = reference(context["sidecar"])["sha256"]
    terminal.chmod(0o600)
    write(terminal, record)
    with pytest.raises(ValueError):
        verifier.authoritative_history("agy")


@pytest.mark.parametrize("change", ["run_id", "result_sha256", "sidecar_sha256", "published"])
def test_schema3_retained_publication_must_match_the_actual_observation(isolated, monkeypatch, revision_check, change):
    context = prepare_diagnostic(isolated, monkeypatch)
    reserve_sixth(context, monkeypatch, diagnostic=synthetic_sidecar(context))
    terminal = v1.receipt("agy", "sixth")
    record = read(terminal)
    publication = record["diagnostic_publication"]
    if change == "run_id":
        publication[change] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    elif change == "published":
        publication.update(published=False, sidecar_sha256=None)
    else:
        publication[change] = "f" * 64
    terminal.chmod(0o600)
    write(terminal, record)
    with pytest.raises(ValueError):
        verifier.authoritative_history("agy")


@pytest.mark.parametrize("change", ["invalid_run", "extra_field"])
def test_schema3_invalid_publication_is_retained_without_credit_and_rejected_by_history(
    isolated, monkeypatch, revision_check, change
):
    context = prepare_diagnostic(isolated, monkeypatch)
    publication = {
        "published": True,
        "run_id": SYNTHETIC_RUN_ID,
        "result_sha256": SYNTHETIC_RESULT_SHA256,
        "sidecar_sha256": "f" * 64,
    }
    if change == "invalid_run":
        publication["run_id"] = "synthetic-invalid-uuid"
    else:
        publication["caller_approval"] = True
    result, dispatch = reserve_sixth(
        context, monkeypatch, passed=True, diagnostic=synthetic_sidecar(context), publication=publication
    )
    dispatch.assert_awaited_once()
    assert result["gate_passed"] is True
    assert result["submissions_consumed_total"] == 6
    assert result["diagnostic_publication"] == publication
    assert result["observation"]["diagnostic_publication"] == publication
    assert result["diagnostic_observation_complete"] is False
    assert result["diagnostic_sidecar_evidence"] is None
    with pytest.raises(ValueError):
        verifier.authoritative_history("agy")
