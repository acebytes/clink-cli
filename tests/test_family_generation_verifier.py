"""Exercise candidate-only family verification without invoking installed CLIs."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import verify_family_generation as verifier


def entry(ordinal=1, check="text", passed=False, replacement=False):
    return {
        "ordinal": ordinal,
        "check": check,
        "passed": passed,
        "replacement": replacement,
        "reservation_sha256": str(ordinal) * 64,
    }


@pytest.fixture
def isolated(monkeypatch):
    parent = Path(__file__).resolve().parents[1] / "test_output/family-generation-verifier-tests"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        root = Path(directory)
        monkeypatch.setattr(verifier, "PROJECT", root)
        monkeypatch.setattr(verifier, "LINEAGE_ROOT", root / "docs/verification/family-generation")
        executable = root / "synthetic-cli"
        executable.write_text("synthetic executable identity")
        monkeypatch.setattr(
            verifier,
            "installed_binding",
            lambda _: (
                str(executable),
                {
                    "observed_version": "synthetic",
                    "executable_sha256": "1" * 64,
                    "recipe_sha256": "2" * 64,
                    "parser_sha256": "3" * 64,
                },
            ),
        )
        monkeypatch.setattr(verifier, "implementation_digest", lambda: "4" * 64)

        def prepare(_family, destination):
            workspace = destination / "workspace"
            workspace.mkdir()
            return workspace, {"fixture_config_sha256": "5" * 64, "fixture_config_files": []}

        monkeypatch.setattr(verifier, "prepare_fixture", prepare, raising=False)
        yield root


def arguments(family="grok", **changes):
    values = {
        "family": family,
        "label": "first",
        "check": "text",
        "live": False,
        "timeout": 30,
        "prior_evidence": None,
        "replacement_review": None,
        "replacement_review_sha256": None,
        "review_repo": None,
        "trust_grok_fixture": False,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def receipt(family, label):
    return verifier.LINEAGE_ROOT / family / label / "receipt.json"


def test_budget_retains_five_calls_two_per_gate_one_replacement():
    ledger = [
        entry(1),
        entry(2, passed=True, replacement=True),
        entry(3, "action", True),
        entry(4, "permission", True),
        entry(5, "cancel", True),
    ]
    verifier.validate_ledger(ledger)
    with pytest.raises(ValueError, match="five"):
        verifier.submission_authorization(ledger, "text", None, None, None, "grok")
    with pytest.raises(ValueError, match="two|replacement"):
        verifier.validate_ledger([entry(1), entry(2, replacement=True), entry(3, replacement=True)])
    with pytest.raises(ValueError, match="replacement"):
        verifier.validate_ledger(
            [
                entry(1),
                entry(2, passed=True, replacement=True),
                entry(3, "action"),
                entry(4, "action", replacement=True),
            ]
        )


@pytest.mark.parametrize("check", verifier.CHECKS)
def test_unexplained_failure_stops_every_gate(check):
    with pytest.raises(ValueError, match="failed|unexplained|diagnosed"):
        verifier.submission_authorization([entry()], check, "a" * 64, None, None, "grok")


def test_default_preflight_makes_no_model_call_and_never_mutates_admission(isolated, monkeypatch):
    run = AsyncMock(side_effect=AssertionError("preflight must not submit"))
    monkeypatch.setattr(verifier, "run_check", run)
    result = asyncio.run(verifier.verify(arguments()))
    assert result["live_submitted"] is False
    assert result["submissions_consumed_total"] == 0
    assert result["production_admission_changed"] is False
    assert result["native_task_admission_changed"] is False
    run.assert_not_called()


@pytest.mark.parametrize("outcome", ["passed", "failed", "interrupted", "unknown"])
def test_reservation_precedes_dispatch_and_terminal_history_is_retained(isolated, monkeypatch, outcome):
    async def run(*_args, **_kwargs):
        submitted = verifier.LINEAGE_ROOT / "grok/first/submitted.json"
        assert submitted.is_file()
        assert verifier.regular_json(submitted)[0]["state"] == "consumed_before_dispatch"
        if outcome == "interrupted":
            raise asyncio.CancelledError
        if outcome == "unknown":
            raise RuntimeError("synthetic unknown failure")
        return {"check": "text", "passed": outcome == "passed"}

    monkeypatch.setattr(verifier, "run_check", run)
    if outcome == "interrupted":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(verifier.verify(arguments(live=True)))
    else:
        asyncio.run(verifier.verify(arguments(live=True)))
    terminal = verifier.regular_json(receipt("grok", "first"))[0]
    assert terminal["submissions_consumed_total"] == 1
    history, _ = verifier.load_history(receipt("grok", "first"), "grok")
    assert history == terminal["submission_ledger"]
    assert history[0]["passed"] is (outcome == "passed")
    if outcome != "passed":
        with pytest.raises(ValueError, match="failed|unexplained|diagnosed"):
            asyncio.run(
                verifier.verify(
                    arguments(label="second", check="action", live=True, prior_evidence=receipt("grok", "first"))
                )
            )


def test_family_lineages_cannot_reset_or_borrow_history(isolated, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": True}))
    asyncio.run(verifier.verify(arguments(live=True)))
    with pytest.raises(ValueError, match="retain|every|history"):
        asyncio.run(verifier.verify(arguments(label="reset", live=True)))
    with pytest.raises(ValueError, match="family|lineage"):
        asyncio.run(verifier.verify(arguments("agy", prior_evidence=receipt("grok", "first"))))
    agy = asyncio.run(verifier.verify(arguments("agy", live=True)))
    assert agy["submissions_consumed_total"] == 1
    assert len(verifier.authoritative_history("grok")) == len(verifier.authoritative_history("agy")) == 1


def test_interrupted_reservation_without_terminal_is_spent_and_cannot_reset(isolated, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": False}))
    asyncio.run(verifier.verify(arguments(live=True)))
    receipt("grok", "first").unlink()
    prior = verifier.LINEAGE_ROOT / "grok/first/preflight.json"
    history, _ = verifier.load_history(prior, "grok")
    assert len(history) == 1 and history[0]["passed"] is False
    with pytest.raises(ValueError, match="failed|unexplained|diagnosed"):
        asyncio.run(verifier.verify(arguments(label="second", check="action", live=True, prior_evidence=prior)))


def test_request_explicitly_uses_new_schema_native_default_and_distinct_checks(tmp_path):
    from clink.native_generation_contracts import parse_native_request

    for family in verifier.FAMILIES:
        for check in verifier.CHECKS:
            body = verifier.request_body(family, check, tmp_path)
            assert body["schema_version"] == 3
            assert body["cli"] == family
            assert body["model_selection"] == {"kind": "native_default"}
            assert "model" not in body
            request = parse_native_request(verifier.canonical_json(body))
            assert request.model is None and request.model_selection.kind == "native_default"
    assert "permission-needed.txt" in verifier.request_body("grok", "permission", tmp_path)["instructions"]


def test_wrong_output_family_and_changed_reservation_fail_closed(isolated, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": True}))
    asyncio.run(verifier.verify(arguments(live=True)))
    path = receipt("grok", "first")
    terminal = verifier.regular_json(path)[0]
    terminal["family"] = "agy"
    path.chmod(0o600)
    path.write_text(json.dumps(terminal))
    with pytest.raises(ValueError, match="family|lineage"):
        verifier.load_history(path, "grok")


def test_candidate_admission_is_process_local_and_exactly_bound(tmp_path, monkeypatch):
    captured = {}
    service = Mock()
    generation = Mock()
    generation.NativeGenerationService.side_effect = lambda **kwargs: captured.update(kwargs) or service
    monkeypatch.setattr(verifier, "generation_modules", lambda: (generation, Mock(), Mock()))
    binding = {
        "observed_version": "synthetic",
        "executable_sha256": "1" * 64,
        "recipe_sha256": "2" * 64,
        "parser_sha256": "3" * 64,
    }
    preflight = tmp_path / "preflight.json"
    preflight.write_text("synthetic candidate preflight")
    verifier.candidate_service("grok", "synthetic", tmp_path, binding, preflight, Mock())
    admission = captured["family_admissions"]["grok"]
    assert admission.verified is True
    assert admission.evidence_path == str(preflight)
    assert admission.evidence_sha256 == verifier.digest(preflight.read_bytes())
    assert "admission" not in captured and "claude_admission" not in captured


@pytest.fixture
def revision_check(monkeypatch):
    observed = Mock()
    monkeypatch.setattr(verifier, "verify_reviewed_revision", observed)
    return observed


def replacement_packet(root, *, family="grok", prior="a" * 64, candidate="c" * 64, changes=None):
    diagnosis = root / "diagnosis.md"
    diagnosis.write_text("Synthetic diagnosed failure.\n")
    correction = root / "correction.diff"
    correction.write_text("Synthetic exact corrective diff.\n")
    fixed = {
        "schema_version": 1,
        "profile": verifier.PROFILE,
        "family": family,
        "prior_receipt_sha256": prior,
        "failed_ordinal": 1,
        "check": "text",
    }
    revision = {"base_commit": "a" * 40, "head_commit": "b" * 40, "diff_sha256": "d" * 64}
    reviewed = {
        **fixed,
        "scope": "family_generation_replacement_input",
        "diagnosis_sha256": verifier.digest(diagnosis.read_bytes()),
        "correction_sha256": verifier.digest(correction.read_bytes()),
        "candidate_sha256": candidate,
        "source_revision": revision,
    }
    reviewed_path = root / "input.json"
    reviewed_path.write_bytes(verifier.canonical_json(reviewed))
    review = {
        **verifier.REVIEW_CORE,
        "base": revision["base_commit"],
        "session_id": "11111111-2222-3333-4444-555555555555",
        "timestamp": "2026-11-03T00:00:00+00:00",
        "review": "Synthetic independent replacement review.\n\n"
        + verifier.APPROVAL_MARKER
        + verifier.digest(reviewed_path.read_bytes()),
    }
    review.update(changes or {})
    review_path = root / "review.json"
    review_path.write_text(json.dumps(review))
    review_repo = root / "review-repo"
    review_repo.mkdir()
    runtime = {
        "schema_version": 1,
        "scope": "family_generation_review_runtime",
        "repository_root": str(review_repo),
        "session_id": review["session_id"],
        "receipt_sha256": verifier.digest(review_path.read_bytes()),
        "effective_runtime": {
            "timestamp": "2026-11-03T00:00:00+00:00",
            "model": "gpt-6.1-sol",
            "effort": "high",
            "model_provider": "openai",
            "approval_policy": "never",
            "sandbox_type": "read-only",
        },
        "evidence": {"source": "session_metadata"},
    }
    runtime_path = root / "runtime.json"
    runtime_path.write_text(json.dumps(runtime))
    binding = {
        "schema_version": 1,
        "scope": "family_generation_replacement_source_binding",
        **revision,
        "candidate_sha256": candidate,
        "executable_sha256": "1" * 64,
        "recipe_sha256": "2" * 64,
        "parser_sha256": "3" * 64,
        "implementation_source_sha256": "4" * 64,
        "receipt_sha256": verifier.digest(review_path.read_bytes()),
        "runtime_attestation_sha256": verifier.digest(runtime_path.read_bytes()),
    }
    binding_path = root / "source-binding.json"
    binding_path.write_text(json.dumps(binding))
    packet = {**fixed, "scope": "family_generation_replacement_review"}
    for key, path in (
        ("diagnosis", diagnosis),
        ("correction", correction),
        ("reviewed_input", reviewed_path),
        ("independent_review", review_path),
        ("runtime_attestation", runtime_path),
        ("source_binding", binding_path),
    ):
        packet[key] = {"path": path.name, "sha256": verifier.digest(path.read_bytes())}
    packet_path = root / "packet.json"
    packet_path.write_text(json.dumps(packet))
    return packet_path, verifier.digest(packet_path.read_bytes()), review_repo


def test_replacement_requires_diagnosis_correction_and_matching_independent_review(tmp_path, revision_check):
    packet, checksum, review_repo = replacement_packet(tmp_path)
    proof = verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum, "grok", review_repo)
    assert proof["candidate_sha256"] == "c" * 64
    assert proof["review_runtime_model_verified"] is False
    assert "Synthetic" not in json.dumps(proof)
    (tmp_path / "correction.diff").write_text("changed after review")
    with pytest.raises(ValueError, match="evidence changed"):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum, "grok", review_repo)


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "gpt-6-astra"},
        {"effort": "ultra"},
        {"id": "another-task"},
        {"verdict": "NEEDS_WORK"},
        {"review": "Claimed SHIP without exact input approval."},
    ],
)
def test_replacement_review_cannot_change_required_review_authority(tmp_path, changes, revision_check):
    packet, checksum, review_repo = replacement_packet(tmp_path, changes=changes)
    with pytest.raises(ValueError):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum, "grok", review_repo)


def test_replacement_does_not_borrow_another_family_or_reset_spent_allowance(tmp_path, revision_check):
    packet, checksum, review_repo = replacement_packet(tmp_path)
    with pytest.raises(ValueError, match="family"):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum, "agy", review_repo)
    spent = [entry(), entry(2, passed=False, replacement=True)]
    with pytest.raises(ValueError, match="failed|replacement"):
        verifier.submission_authorization(spent, "text", "a" * 64, packet, checksum, "grok", review_repo)
    with pytest.raises(ValueError, match="explicit authoritative"):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum, "grok")


def test_replacement_checks_current_candidate_before_reserving(isolated, monkeypatch, revision_check):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": False}))
    asyncio.run(verifier.verify(arguments(live=True)))
    prior = verifier.digest(receipt("grok", "first").read_bytes())
    packet, checksum, review_repo = replacement_packet(isolated, prior=prior)
    result = asyncio.run(
        verifier.verify(
            arguments(
                label="replacement",
                live=True,
                prior_evidence=receipt("grok", "first"),
                replacement_review=packet,
                replacement_review_sha256=checksum,
                review_repo=review_repo,
            )
        )
    )
    assert result["error_class"] == "ValueError"
    assert result["submissions_consumed_total"] == 1
    assert result["live_submitted"] is False
    assert not (verifier.LINEAGE_ROOT / "grok/replacement/submitted.json").exists()


def test_same_family_lock_prevents_concurrent_budget_reservations(isolated):
    import fcntl
    import os

    root = verifier.family_root("grok")
    root.mkdir(parents=True)
    descriptor = os.open(root / ".verification.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="owns"):
            asyncio.run(verifier.verify(arguments(live=True)))
    finally:
        os.close(descriptor)
    assert list(root.glob("*/submitted.json")) == []


def test_reviewed_replacement_completes_five_call_lineage_without_erasing_failure(
    isolated, monkeypatch, revision_check
):
    async def run(_family, check, *_args):
        return {"check": check, "passed": True}

    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": False}))
    first = asyncio.run(verifier.verify(arguments(live=True)))
    path = receipt("grok", "first")
    packet, checksum, review_repo = replacement_packet(
        isolated, prior=verifier.digest(path.read_bytes()), candidate=first["candidate_sha256"]
    )
    monkeypatch.setattr(verifier, "run_check", run)
    result = asyncio.run(
        verifier.verify(
            arguments(
                label="replacement",
                live=True,
                prior_evidence=path,
                replacement_review=packet,
                replacement_review_sha256=checksum,
                review_repo=review_repo,
            )
        )
    )
    path = receipt("grok", "replacement")
    for check in ("action", "permission", "cancel"):
        result = asyncio.run(verifier.verify(arguments(label=check, check=check, live=True, prior_evidence=path)))
        path = receipt("grok", check)
    assert result["submissions_consumed_total"] == 5
    assert result["all_family_generation_gates_passed"] is True
    assert [item["passed"] for item in result["submission_ledger"]] == [False, True, True, True, True]
    assert sum(item["replacement"] for item in result["submission_ledger"]) == 1
    with pytest.raises(ValueError, match="five"):
        asyncio.run(verifier.verify(arguments(label="reset", live=True, prior_evidence=path)))


def test_changed_candidate_does_not_reuse_another_candidate_gate_success(isolated, monkeypatch):
    async def run(_family, check, *_args):
        return {"check": check, "passed": True}

    monkeypatch.setattr(verifier, "run_check", run)
    path = None
    for check in ("text", "action", "permission"):
        asyncio.run(verifier.verify(arguments(label=check, check=check, live=True, prior_evidence=path)))
        path = receipt("grok", check)
    monkeypatch.setattr(verifier, "implementation_digest", lambda: "5" * 64)
    result = asyncio.run(verifier.verify(arguments(label="cancel", check="cancel", live=True, prior_evidence=path)))
    assert result["gate_passed"] is True
    assert result["all_family_generation_gates_passed"] is False
    assert all(item["passed"] for item in result["submission_ledger"])


@pytest.mark.parametrize("changed", [None, "HEAD", "diff", "dirty"])
def test_reviewed_revision_binds_actual_canonical_clean_clink_source(tmp_path, monkeypatch, changed):
    (tmp_path / "clink").mkdir()
    (tmp_path / "clink/native_generation.py").write_text("synthetic source")
    revision = {"base_commit": "a" * 40, "head_commit": "b" * 40, "diff_sha256": verifier.digest(b"synthetic diff")}
    monkeypatch.setenv("GIT_DIR", "must-not-be-inherited")

    def command(argv, environment, cwd, **bounds):
        assert "GIT_DIR" not in environment
        assert bounds == {"max_bytes": 16 * 1024 * 1024, "timeout_seconds": 10}
        assert cwd == tmp_path
        if argv[1:] == ["rev-parse", "--show-toplevel"]:
            return 0, str(tmp_path).encode(), b""
        if argv[1:] == ["rev-parse", "HEAD"]:
            return 0, ("c" * 40 if changed == "HEAD" else "b" * 40).encode(), b""
        if argv[1] == "diff":
            assert argv[2:] == ["--binary", "--full-index", "--no-ext-diff", "--no-textconv", "a" * 40, "b" * 40, "--"]
            return 0, b"changed" if changed == "diff" else b"synthetic diff", b""
        assert argv[1:] == ["status", "--porcelain", "--untracked-files=no"]
        return 0, b" M clink/native_generation.py" if changed == "dirty" else b"", b""

    monkeypatch.setattr(verifier.native.probes, "bounded_command", command)
    if changed is None:
        verifier.verify_reviewed_revision(tmp_path, revision)
    else:
        with pytest.raises(ValueError, match="changed"):
            verifier.verify_reviewed_revision(tmp_path, revision)


@pytest.mark.parametrize("family,live", [("grok", False), ("agy", True), ("agy", False)])
def test_fixture_trust_rejects_any_other_family_or_nonlive_invocation(isolated, monkeypatch, family, live):
    helper = Mock(side_effect=AssertionError("native trust must not be touched"))
    monkeypatch.setattr(verifier.fixtures, "grok_fixture_trust", helper)
    monkeypatch.setattr(
        verifier, "run_check", AsyncMock(side_effect=AssertionError("invalid trust option cannot submit"))
    )
    with pytest.raises(ValueError, match="explicit live Grok"):
        asyncio.run(verifier.verify(arguments(family, live=live, trust_grok_fixture=True)))
    helper.assert_not_called()


@pytest.mark.parametrize("outcome", ["passed", "prepare_failed", "cancelled", "cleanup_refused"])
def test_explicit_trust_precedes_reservation_and_outlives_service_cleanup(isolated, monkeypatch, outcome):
    events = []
    submitted = verifier.LINEAGE_ROOT / "grok/first/submitted.json"

    @contextmanager
    def trust(workspace, _native_home):
        assert workspace.is_dir() and not submitted.exists()
        events.append("prepare")
        if outcome == "prepare_failed":
            raise RuntimeError("synthetic trust preparation failed")
        state = {"fixture_trust_prepared": True, "fixture_trust_removed": False}
        try:
            yield state
        finally:
            events.append("trust_cleanup")
            if outcome == "cleanup_refused":
                raise RuntimeError("synthetic owned grant changed; cleanup refused")
            state["fixture_trust_removed"] = True

    async def run(*_args):
        assert submitted.is_file()
        events.append("dispatch")
        try:
            if outcome == "cancelled":
                raise asyncio.CancelledError
            return {"check": "text", "passed": True}
        finally:
            events.append("service_cleanup")

    monkeypatch.setattr(verifier.fixtures, "grok_fixture_trust", trust)
    monkeypatch.setattr(verifier, "run_check", run)
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(verifier.verify(arguments(live=True, trust_grok_fixture=True)))
        result = verifier.regular_json(receipt("grok", "first"))[0]
    else:
        result = asyncio.run(verifier.verify(arguments(live=True, trust_grok_fixture=True)))
    expected_calls = 0 if outcome == "prepare_failed" else 1
    assert result["submissions_consumed_total"] == expected_calls
    assert submitted.exists() is bool(expected_calls)
    if outcome == "prepare_failed":
        assert events == ["prepare"]
        assert result["host_configuration_changed"] is None
        assert result["host_configuration_restored"] is None
        assert result["fixture_trust"] == {"fixture_trust_prepared": None, "fixture_trust_removed": None}
    else:
        assert events == ["prepare", "dispatch", "service_cleanup", "trust_cleanup"]
        assert result["fixture_trust"]["fixture_trust_prepared"] is True
        assert result["host_configuration_restored"] is (outcome != "cleanup_refused")
        assert result["gate_passed"] is (outcome == "passed")
        history, _ = verifier.load_history(receipt("grok", "first"), "grok")
        assert len(history) == 1 and history[0]["passed"] is (outcome == "passed")


def test_fixture_preparation_failure_retains_zero_call_receipt(isolated, monkeypatch):
    monkeypatch.setattr(verifier, "prepare_fixture", Mock(side_effect=OSError("synthetic preparation failure")))
    trust = Mock(side_effect=AssertionError("unprepared fixture cannot be trusted"))
    monkeypatch.setattr(verifier.fixtures, "grok_fixture_trust", trust)
    result = asyncio.run(verifier.verify(arguments(live=True, trust_grok_fixture=True)))
    assert result["submissions_consumed_total"] == 0
    assert result["error_class"] == "OSError"
    assert not (verifier.LINEAGE_ROOT / "grok/first/submitted.json").exists()
    assert receipt("grok", "first").is_file()
    trust.assert_not_called()
