"""Verify native-generation accounting and production extraction using synthetic processes only."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import verify_native_generation as verifier


@pytest.fixture
def isolated(monkeypatch):
    parent = verifier.PROJECT / "test_output/native-generation-verifier-tests"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        root = Path(directory)
        monkeypatch.setattr(verifier, "PROJECT", root)
        monkeypatch.setattr(verifier, "LINEAGE_ROOT", root / "docs/verification/native-generation")
        yield root


def arguments(root: Path, **changes) -> argparse.Namespace:
    values = {
        "label": "first",
        "check": "text",
        "live": False,
        "timeout": 30,
        "output_dir": verifier.LINEAGE_ROOT,
        "prior_evidence": None,
        "source_manifest": root / "source.json",
        "source_manifest_sha256": "0" * 64,
        "replacement_review": None,
        "replacement_review_sha256": None,
        "login_source": verifier.LOGIN_SOURCE,
        "ownership_appendix": verifier.OWNERSHIP_APPENDIX,
        "login_appendix": verifier.LOGIN_APPENDIX,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def entry(ordinal=1, check="text", passed=False, replacement=False, checksum="0" * 64):
    return {
        "ordinal": ordinal,
        "check": check,
        "passed": passed,
        "replacement": replacement,
        "reservation_sha256": checksum,
    }


@pytest.fixture
def static_prerequisites(monkeypatch, isolated):
    binary = isolated / "codex"
    binary.write_text("synthetic executable identity")
    # Only synthetic accounting tests repin the fake executable in trusted test code.
    monkeypatch.setattr(verifier, "RESEARCHED_EXECUTABLE_SHA256", verifier.digest(binary.read_bytes()))
    monkeypatch.setattr(verifier.probes.shutil, "which", lambda _: str(binary))
    monkeypatch.setattr(verifier.probes, "bounded_version", lambda *_: (0, verifier.VERSION.encode()))
    monkeypatch.setattr(verifier, "source_proof", lambda *_: {"verified": True})
    monkeypatch.setattr(
        verifier, "historical_lineages", lambda: [{"profile": "generation_v1", "admission_reused": False}]
    )
    monkeypatch.setattr(verifier, "recipe_proof", lambda: {"verified": True})
    monkeypatch.setattr(verifier, "appendix_proof", lambda *_: {"verified": True})
    monkeypatch.setattr(
        verifier,
        "implementation_manifest",
        lambda: {"schema_version": 1, "files": [{"path": "clink/native_generation.py", "sha256": "1" * 64}]},
    )
    monkeypatch.setattr(
        verifier,
        "authentication_preflight",
        lambda *_: {
            "native_auth_configured": True,
            "chatgpt_status_reported": True,
        },
    )
    return isolated


@pytest.mark.private_evidence
def test_historical_failures_remain_pinned_and_separate(private_evidence_root, monkeypatch):
    monkeypatch.setattr(verifier, "PROJECT", private_evidence_root)
    references = verifier.historical_lineages()
    assert len(references) == 2
    assert references[0]["submissions_consumed_total"] == 0
    assert references[1]["submissions_consumed_total"] == 4
    assert references[1]["submission_allowance_total"] == 5
    assert not any(item["admission_reused"] for item in references)


def test_source_binding_requires_exact_packet_and_source_bytes(monkeypatch, tmp_path):
    source = tmp_path / "source.rs"
    source.write_text("pinned official source fixture\n")
    checksum = verifier.digest(source.read_bytes())
    monkeypatch.setattr(verifier, "SOURCE_BINDINGS", {"source.rs": checksum})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "codex_source_commit": verifier.SOURCE_COMMIT,
                "sources": [{"path": "source.rs", "sha256": checksum, "local_path": str(source)}],
            }
        )
    )
    packet = verifier.digest(manifest.read_bytes())
    assert verifier.source_proof(manifest, packet)["verified"] is True
    with pytest.raises(ValueError, match="explicit pin"):
        verifier.source_proof(manifest, "0" * 64)
    source.write_text("changed\n")
    with pytest.raises(ValueError, match="source bytes changed"):
        verifier.source_proof(manifest, packet)


def test_login_status_exposes_no_partial_key_or_raw_account_output(monkeypatch, tmp_path):
    source = tmp_path / "login.rs"
    source.write_text("synthetic pinned login source")
    monkeypatch.setattr(verifier, "LOGIN_SOURCE_SHA256", verifier.digest(source.read_bytes()))
    command = []

    def status(arguments, _environment, **_bounds):
        command.extend(arguments)
        return 0, b"", b"Logged in using an API key - PRIVATE_PARTIAL_KEY"

    monkeypatch.setattr(verifier.probes, "bounded_command", status)
    result = verifier.authentication_preflight("synthetic-codex", source)
    assert command == ["synthetic-codex", "login", "status"]
    assert result["native_auth_configured"] is True
    assert result["chatgpt_status_reported"] is False
    assert result["model_requests_submitted"] == 0
    assert "PRIVATE" not in json.dumps(result)


def make_replacement_packet(
    root,
    monkeypatch,
    *,
    prior="a" * 64,
    candidate="c" * 64,
    review_changes=None,
    journal_changes=None,
    input_changes=None,
    review_text=None,
    raw_review=None,
):
    task_id = "synthetic-native-review.1"
    monkeypatch.setattr(verifier, "REVIEW_TASK_SHA256", verifier.digest(task_id.encode()))
    root.mkdir(parents=True, exist_ok=True)
    diagnosis = root / "diagnosis.json"
    diagnosis.write_text('{"technical_diagnosis":"synthetic only"}')
    reviewed_input = {
        "schema_version": 1,
        "scope": "native_generation_replacement_input",
        "profile": verifier.PROFILE,
        "prior_receipt_sha256": prior,
        "failed_ordinal": 1,
        "check": "text",
        "diagnosis_sha256": verifier.digest(diagnosis.read_bytes()),
        "candidate_sha256": candidate,
    }
    reviewed_input.update(input_changes or {})
    input_path = root / "reviewed-input.json"
    input_path.write_bytes(verifier.canonical_json(reviewed_input))
    input_sha = verifier.digest(input_path.read_bytes())
    marker = verifier.APPROVAL_MARKER + input_sha
    text = review_text(marker) if review_text else "Synthetic independent review fixture. SHIP.\n\n" + marker
    receipt = {
        **verifier.REVIEW_CORE,
        "id": task_id,
        "session_id": "11111111-2222-3333-4444-555555555555",
        "review_reservation_id": "native-replacement_1",
        "timestamp": "2026-11-02T00:00:00+00:00",
        "review": text,
    }
    receipt.update(review_changes or {})
    review_path = root / "review.json"
    review_path.write_bytes(raw_review if raw_review is not None else json.dumps(receipt).encode())
    flow_root = root / "trusted-flow"
    monkeypatch.setattr(verifier, "TRUSTED_FLOW_ROOT", flow_root)
    journals = flow_root / ".flow/review-runs"
    journals.mkdir(parents=True)
    journal = {
        "reservation_id": receipt["review_reservation_id"],
        "task_id": task_id,
        "backend": "codex",
        "review_type": "impl",
        "outcome": "verdict",
        "verdict": "SHIP",
        "finalized": {"receipt": "complete"},
        "reviewed_model": "gpt-6.1-sol",
        "reviewed_effort": "high",
        "receipt_payload": {key: value for key, value in receipt.items() if key != "review_reservation_id"},
    }
    journal.update(journal_changes or {})
    journal_path = journals / "native-replacement_1.json"
    journal_path.write_text(json.dumps(journal))
    packet = {
        "schema_version": 1,
        "scope": "native_generation_replacement_review",
        "profile": verifier.PROFILE,
        "prior_receipt_sha256": prior,
        "failed_ordinal": 1,
        "check": "text",
        "diagnosis": {"path": diagnosis.name, "sha256": verifier.digest(diagnosis.read_bytes())},
        "reviewed_input": {"path": input_path.name, "sha256": input_sha},
        "independent_review": {"path": review_path.name, "sha256": verifier.digest(review_path.read_bytes())},
        "review_run_sha256": verifier.digest(journal_path.read_bytes()),
    }
    path = root / "packet.json"
    path.write_text(json.dumps(packet))
    return path, verifier.digest(path.read_bytes())


def test_diagnosed_replacement_requires_bound_native_receipt_and_trusted_flow_journal(tmp_path, monkeypatch):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch)
    proof = verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)
    assert proof["packet_sha256"] == checksum
    assert proof["candidate_sha256"] == "c" * 64
    assert proof["review_runtime_model_verified"] is False
    assert "Synthetic independent review" not in json.dumps(proof)
    (tmp_path / "review.json").write_text("changed review fixture")
    with pytest.raises(ValueError, match="evidence changed"):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "plan_review"},
        {"id": "another-task"},
        {"mode": "host"},
        {"verdict": "NEEDS_WORK"},
        {"model": "gpt-6-astra"},
        {"effort": "ultra"},
        {"spec": "codex:gpt-6.1-sol:ultra"},
        {"session_id": "invalid"},
        {"review_reservation_id": "../../escape"},
        {"timestamp": "2026-11-02T00:00:00"},
        {"review": ""},
    ],
)
def test_native_replacement_review_rejects_wrong_identity_or_model(tmp_path, monkeypatch, changes):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch, review_changes=changes)
    with pytest.raises(ValueError):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


@pytest.mark.parametrize(
    "text",
    [
        lambda marker: "Caller quoted approval:\n> " + marker,
        lambda marker: "> quoted approval text\n" + marker,
        lambda marker: "- list approval text\n" + marker,
        lambda marker: "```text\n\n" + marker,
        lambda marker: "~~~text\n\n" + marker,
        lambda marker: "````text\n```\n\n" + marker,
        lambda marker: "```text\n``` not a closing fence\n\n" + marker,
        lambda marker: marker + "\n" + marker,
        lambda marker: "Review mentions " + marker + " in prose.",
        lambda marker: "SHIP\n" + marker + "\nMore caller text",
    ],
)
def test_reviewer_binding_must_be_one_unquoted_terminal_line(tmp_path, monkeypatch, text):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch, review_text=text)
    with pytest.raises(ValueError):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


@pytest.mark.parametrize(
    "changes",
    [
        {"prior_receipt_sha256": "b" * 64},
        {"failed_ordinal": 2},
        {"failed_ordinal": True},
        {"check": "tool"},
        {"diagnosis_sha256": "b" * 64},
        {"profile": "generation_v1"},
        {"unbound_extra": True},
    ],
)
def test_reviewed_input_binds_exact_failure_diagnosis_and_schema(tmp_path, monkeypatch, changes):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch, input_changes=changes)
    with pytest.raises(ValueError):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": "wrong"},
        {"backend": "host"},
        {"review_type": "plan"},
        {"outcome": "transport_failure"},
        {"verdict": "NEEDS_WORK"},
        {"finalized": {"receipt": "pending"}},
        {"reviewed_model": "gpt-6-astra"},
        {"reviewed_effort": "ultra"},
        {"receipt_payload": {"model": "gpt-6.1-sol"}},
    ],
)
def test_native_review_journal_must_be_finalized_and_match_receipt(tmp_path, monkeypatch, changes):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch, journal_changes=changes)
    with pytest.raises(ValueError):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


@pytest.mark.parametrize("raw", [b"arbitrary approval bytes", b"[]", b'{"type":"impl_review","type":"impl_review"}'])
def test_arbitrary_or_malformed_review_bytes_do_not_authorize_replacement(tmp_path, monkeypatch, raw):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch, raw_review=raw)
    with pytest.raises(ValueError):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


def test_caller_approval_labels_cannot_replace_native_review_fields(tmp_path, monkeypatch):
    packet, _ = make_replacement_packet(tmp_path, monkeypatch)
    value = json.loads(packet.read_bytes())
    value["independent_review"]["verdict"] = "approved"
    packet.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="only path and SHA"):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, verifier.digest(packet.read_bytes()))


def test_review_provenance_requires_trusted_regular_journal(tmp_path, monkeypatch):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch)
    journal = verifier.TRUSTED_FLOW_ROOT / ".flow/review-runs/native-replacement_1.json"
    target = tmp_path / "caller-journal.json"
    journal.rename(target)
    journal.symlink_to(target)
    with pytest.raises(OSError):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


def test_missing_trusted_review_root_refuses_replacement(tmp_path, monkeypatch):
    packet, checksum = make_replacement_packet(tmp_path, monkeypatch)
    monkeypatch.setattr(verifier, "TRUSTED_FLOW_ROOT", None)
    with pytest.raises(ValueError, match="archived original verifier"):
        verifier.submission_authorization([entry()], "text", "a" * 64, packet, checksum)


def test_candidate_digest_binds_every_exact_current_identity_field():
    record = dict.fromkeys(verifier.CANDIDATE_FIELDS, "a" * 64)
    original = verifier.candidate_digest(record)
    for name in verifier.CANDIDATE_FIELDS:
        changed = {**record, name: "b" * 64}
        assert verifier.candidate_digest(changed) != original
    assert original == verifier.digest(verifier.canonical_json(record))


def create_implementation_fixture(root):
    (root / "clink").mkdir()
    (root / "scripts").mkdir()
    for relative in (
        "clink/native_generation.py",
        "clink/registry.py",
        "clink/runner.py",
        "clink/contracts.py",
        "scripts/verify_native_generation.py",
        "scripts/verify_cli_policy.py",
        "scripts/verify_generation_policy.py",
    ):
        (root / relative).write_text("# synthetic source fixture\n")


def test_service_only_source_change_invalidates_candidate_without_exporting_contents(isolated, monkeypatch):
    create_implementation_fixture(isolated)
    before = verifier.implementation_manifest()
    record = dict.fromkeys(verifier.CANDIDATE_FIELDS, "a" * 64)
    record["implementation_source_sha256"] = verifier.digest(verifier.canonical_json(before))
    previous_candidate = verifier.candidate_digest(record)
    packet, checksum = make_replacement_packet(isolated / "approval", monkeypatch, candidate=previous_candidate)
    approved = verifier.replacement_proof(packet, checksum, "a" * 64, entry())
    (isolated / "clink/native_generation.py").write_text("# changed synthetic service implementation\n")
    after = verifier.implementation_manifest()
    record["implementation_source_sha256"] = verifier.digest(verifier.canonical_json(after))
    assert approved["candidate_sha256"] != verifier.candidate_digest(record)
    with pytest.raises(ValueError, match="current candidate"):
        verifier.require_candidate_review(approved, record)
    assert before != after
    assert "synthetic service" not in json.dumps(after)
    assert {item["path"] for item in after["files"]} == {
        "clink/native_generation.py",
        "clink/registry.py",
        "clink/runner.py",
        "clink/contracts.py",
        "scripts/verify_native_generation.py",
        "scripts/verify_cli_policy.py",
        "scripts/verify_generation_policy.py",
    }


@pytest.mark.parametrize("unsafe", ["file_symlink", "directory_symlink", "oversized", "too_many"])
def test_implementation_manifest_rejects_unbounded_or_symlinked_source(isolated, unsafe):
    create_implementation_fixture(isolated)
    source = isolated / "clink/native_generation.py"
    if unsafe == "file_symlink":
        source.unlink()
        source.symlink_to(isolated / "clink/contracts.py")
    elif unsafe == "directory_symlink":
        (isolated / "clink/linked").symlink_to(isolated / "scripts", target_is_directory=True)
    elif unsafe == "oversized":
        source.write_bytes(b"x" * (1024 * 1024 + 1))
    else:
        for ordinal in range(257):
            (isolated / f"clink/source_{ordinal}.py").write_text("pass\n")
    with pytest.raises((OSError, ValueError)):
        verifier.implementation_manifest()


@pytest.mark.parametrize(
    "ledger",
    [
        [entry(ordinal=True)],
        [entry(passed=1)],
        [entry(replacement=1)],
        [entry(), entry(2, replacement=False)],
        [entry(), entry(2, replacement=True), entry(3, check="tool"), entry(4, check="tool", replacement=True)],
        [entry(), entry(2, replacement=True), entry(3, replacement=True)],
    ],
)
def test_invalid_or_overbudget_ledgers_are_rejected(ledger):
    with pytest.raises(ValueError):
        verifier.validate_ledger(ledger)


def test_single_invocation_and_lineage_budget_are_not_increased():
    ledger = [entry(), entry(2, check="tool"), entry(3, check="cancel"), entry(4, replacement=True)]
    verifier.validate_ledger(ledger)
    with pytest.raises(ValueError, match="exhausted"):
        verifier.submission_authorization(ledger, "cancel", "prior", None, None)
    with pytest.raises(ValueError, match="independent review"):
        verifier.submission_authorization([entry()], "text", "prior", None, None)
    with pytest.raises(ValueError, match="successful"):
        verifier.submission_authorization([entry(passed=True)], "text", "prior", None, None)


def test_interrupted_reservation_is_consumed_and_terminal_preflight_cannot_replay(isolated):
    directory = verifier.LINEAGE_ROOT / "interrupted"
    directory.mkdir(parents=True)
    preflight = directory / "preflight.json"
    verifier.persist(
        preflight,
        {
            "scope": verifier.SCOPE,
            "profile": verifier.PROFILE,
            "check": "text",
            "live_submitted": False,
            "submissions_in_this_invocation": 0,
            "submission_ledger": [],
            "submissions_consumed_total": 0,
        },
    )
    verifier.persist(
        directory / "submitted.json",
        {
            "schema_version": 1,
            "scope": verifier.SCOPE,
            "profile": verifier.PROFILE,
            "ordinal": 1,
            "check": "text",
            "replacement": False,
            "state": "consumed_before_dispatch",
            "preflight_sha256": verifier.digest(preflight.read_bytes()),
        },
    )
    ledger, _ = verifier.load_history(preflight)
    assert len(ledger) == 1 and ledger[0]["passed"] is False
    verifier.persist(
        directory / "receipt.json",
        {
            "scope": verifier.SCOPE,
            "profile": verifier.PROFILE,
            "submission_ledger": [{**ledger[0], "passed": True}],
            "submissions_consumed_total": 1,
        },
    )
    with pytest.raises(ValueError, match="terminal evidence"):
        verifier.load_history(preflight)


def test_old_profiles_cannot_supply_new_native_generation_admission(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "scope": "candidate_native_adapter_lifecycle",
                "profile": "native_task_v1",
                "submission_ledger": [],
                "submissions_consumed_total": 0,
            }
        )
    )
    with pytest.raises(ValueError, match="lineage"):
        verifier.load_history(path)


@pytest.mark.asyncio
async def test_prepare_only_never_reserves_or_dispatches(static_prerequisites, monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(verifier, "run_check", run)
    record = await verifier.verify(arguments(static_prerequisites))
    run.assert_not_awaited()
    assert record["live_submitted"] is False
    assert record["submissions_consumed_total"] == 0
    assert not (verifier.LINEAGE_ROOT / "first/submitted.json").exists()


@pytest.mark.asyncio
async def test_failure_is_reserved_before_dispatch_without_retry_and_cannot_reset(static_prerequisites, monkeypatch):
    async def failing(_check, _executable, _root, binding, preflight, _timeout):
        submitted = verifier.read_json(preflight.parent / "submitted.json")
        assert submitted["state"] == "consumed_before_dispatch"
        assert binding["submissions_consumed_total"] == submitted["ordinal"]
        raise RuntimeError("PRIVATE_BODY")

    run = AsyncMock(side_effect=failing)
    monkeypatch.setattr(verifier, "run_check", run)
    record = await verifier.verify(arguments(static_prerequisites, live=True))
    assert run.await_count == 1
    assert record["submissions_consumed_total"] == record["submissions_in_this_invocation"] == 1
    assert record["submission_ledger"][0]["passed"] is False
    assert "PRIVATE_BODY" not in json.dumps(record)
    with pytest.raises(ValueError, match="retain every"):
        await verifier.verify(arguments(static_prerequisites, label="reset", live=True, check="tool"))
    prior = verifier.LINEAGE_ROOT / "first/receipt.json"
    next_record = await verifier.verify(
        arguments(static_prerequisites, label="next", live=True, check="tool", prior_evidence=prior)
    )
    assert next_record["submissions_consumed_total"] == 2
    assert next_record["submission_ledger"][0] == record["submission_ledger"][0]


@pytest.mark.asyncio
async def test_copied_history_cannot_relabel_failed_text_as_unattempted_gate(static_prerequisites, monkeypatch):
    run = AsyncMock(side_effect=RuntimeError("synthetic failed text gate"))
    monkeypatch.setattr(verifier, "run_check", run)
    first = await verifier.verify(arguments(static_prerequisites, live=True))
    copied = static_prerequisites / "copied-history.json"
    altered = json.loads(json.dumps(first))
    altered["submission_ledger"][0]["check"] = "tool"
    copied.write_text(json.dumps(altered))
    run.reset_mock()
    with pytest.raises(ValueError):
        await verifier.verify(arguments(static_prerequisites, label="relabeled", live=True, prior_evidence=copied))
    run.assert_not_awaited()
    assert not (verifier.LINEAGE_ROOT / "relabeled/submitted.json").exists()


@pytest.mark.asyncio
async def test_copy_inside_lineage_cannot_relabel_failed_reservation(static_prerequisites, monkeypatch):
    run = AsyncMock(side_effect=RuntimeError("synthetic failed text gate"))
    monkeypatch.setattr(verifier, "run_check", run)
    original = await verifier.verify(arguments(static_prerequisites, live=True))
    copied = verifier.LINEAGE_ROOT / "copied/receipt.json"
    copied.parent.mkdir()
    original["submission_ledger"][0]["check"] = "tool"
    verifier.persist(copied, original)
    run.reset_mock()
    with pytest.raises(ValueError, match="durable reservation"):
        await verifier.verify(arguments(static_prerequisites, label="relabeled", live=True, prior_evidence=copied))
    run.assert_not_awaited()
    assert not (verifier.LINEAGE_ROOT / "relabeled").exists()


@pytest.mark.asyncio
async def test_exact_copy_cannot_claim_another_directorys_terminal_reservation(static_prerequisites, monkeypatch):
    run = AsyncMock(side_effect=RuntimeError("synthetic failed text gate"))
    monkeypatch.setattr(verifier, "run_check", run)
    original = await verifier.verify(arguments(static_prerequisites, live=True))
    copied = verifier.LINEAGE_ROOT / "copied/receipt.json"
    copied.parent.mkdir()
    verifier.persist(copied, original)
    run.reset_mock()
    with pytest.raises(ValueError, match="durable reservation"):
        await verifier.verify(
            arguments(static_prerequisites, label="copied-next", check="tool", live=True, prior_evidence=copied)
        )
    run.assert_not_awaited()
    assert not (verifier.LINEAGE_ROOT / "copied-next").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("ordinal", 2), ("check", "cancel"), ("replacement", True), ("passed", True)])
async def test_all_historical_entry_fields_bind_original_reservation_and_outcome(
    static_prerequisites, monkeypatch, field, value
):
    run = AsyncMock(side_effect=RuntimeError("synthetic failed gate"))
    monkeypatch.setattr(verifier, "run_check", run)
    await verifier.verify(arguments(static_prerequisites, live=True))
    first = verifier.LINEAGE_ROOT / "first/receipt.json"
    second = await verifier.verify(
        arguments(static_prerequisites, label="second", check="tool", live=True, prior_evidence=first)
    )
    prior = verifier.LINEAGE_ROOT / "second/receipt.json"
    second["submission_ledger"][0][field] = value
    # Only synthetic evidence is changed to exercise hostile supplied history.
    prior.chmod(0o600)
    prior.write_text(json.dumps(second))
    run.reset_mock()
    with pytest.raises(ValueError):
        await verifier.verify(
            arguments(static_prerequisites, label="tampered", check="cancel", live=True, prior_evidence=prior)
        )
    run.assert_not_awaited()
    assert not (verifier.LINEAGE_ROOT / "tampered").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["gate_passed", "observation_passed", "observation_check"])
async def test_terminal_outcome_requires_same_gate_observation(static_prerequisites, monkeypatch, field):
    run = AsyncMock(return_value={"passed": False, "check": "text"})
    monkeypatch.setattr(verifier, "run_check", run)
    record = await verifier.verify(arguments(static_prerequisites, live=True))
    prior = verifier.LINEAGE_ROOT / "first/receipt.json"
    if field == "gate_passed":
        record["gate_passed"] = True
        record["submission_ledger"][0]["passed"] = True
    elif field == "observation_passed":
        record["observation"]["passed"] = True
    else:
        record["observation"]["check"] = "tool"
    prior.chmod(0o600)
    prior.write_text(json.dumps(record))
    run.reset_mock()
    with pytest.raises(ValueError, match="terminal outcome"):
        await verifier.verify(
            arguments(static_prerequisites, label="tampered", check="tool", live=True, prior_evidence=prior)
        )
    run.assert_not_awaited()
    assert not (verifier.LINEAGE_ROOT / "tampered").exists()


@pytest.mark.asyncio
async def test_original_canonical_chain_loads_and_preserves_all_outcomes(static_prerequisites, monkeypatch):
    async def passed(check, *_args):
        return {"passed": True, "check": check}

    run = AsyncMock(side_effect=passed)
    monkeypatch.setattr(verifier, "run_check", run)
    prior = None
    receipts = []
    for check in verifier.CHECKS:
        await verifier.verify(
            arguments(static_prerequisites, label=check, check=check, live=True, prior_evidence=prior)
        )
        prior = verifier.LINEAGE_ROOT / check / "receipt.json"
        receipts.append((prior, verifier.digest(prior.read_bytes())))
    history = verifier.authoritative_history()
    for ordinal, (path, checksum) in enumerate(receipts, 1):
        ledger, retained = verifier.load_history(path)
        assert retained == checksum
        assert ledger == [item["entry"] for item in history[:ordinal]]
        assert all(item["passed"] and not item["replacement"] for item in ledger)
    assert run.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_matches", [False, True])
async def test_replacement_is_reserved_only_for_the_exact_reviewed_current_candidate(
    static_prerequisites, monkeypatch, candidate_matches
):
    run = AsyncMock(side_effect=RuntimeError("synthetic diagnosed failure"))
    monkeypatch.setattr(verifier, "run_check", run)
    first = await verifier.verify(arguments(static_prerequisites, live=True))
    prior = verifier.LINEAGE_ROOT / "first/receipt.json"
    candidate = first["candidate_sha256"] if candidate_matches else "c" * 64
    packet, checksum = make_replacement_packet(
        static_prerequisites / "approval", monkeypatch, prior=verifier.digest(prior.read_bytes()), candidate=candidate
    )
    run.reset_mock()
    run.side_effect = None
    run.return_value = {"passed": True, "check": "text"}
    result = await verifier.verify(
        arguments(
            static_prerequisites,
            label="replacement",
            live=True,
            prior_evidence=prior,
            replacement_review=packet,
            replacement_review_sha256=checksum,
        )
    )
    if candidate_matches:
        run.assert_awaited_once()
        assert result["submissions_consumed_total"] == 2
        assert result["submission_ledger"][-1]["replacement"] is True
    else:
        run.assert_not_awaited()
        assert result["error_class"] == "ValueError"
        assert result["submissions_consumed_total"] == 1
        assert result["submissions_in_this_invocation"] == 0
        assert not (verifier.LINEAGE_ROOT / "replacement/submitted.json").exists()


@pytest.mark.asyncio
async def test_concurrent_invocation_cannot_reserve_a_second_submission(static_prerequisites, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def held(*_arguments):
        started.set()
        await release.wait()
        return {"passed": False, "check": "text"}

    monkeypatch.setattr(verifier, "run_check", held)
    first = asyncio.create_task(verifier.verify(arguments(static_prerequisites, live=True)))
    await started.wait()
    try:
        with pytest.raises(ValueError, match="owns this lineage"):
            await verifier.verify(arguments(static_prerequisites, label="concurrent", live=True, check="tool"))
    finally:
        release.set()
        record = await first
    assert record["submissions_consumed_total"] == 1
    assert not (verifier.LINEAGE_ROOT / "concurrent").exists()


@pytest.mark.asyncio
async def test_failed_static_proof_never_consumes_a_submission(static_prerequisites, monkeypatch):
    monkeypatch.setattr(verifier, "source_proof", lambda *_: (_ for _ in ()).throw(ValueError("PRIVATE")))
    run = AsyncMock()
    monkeypatch.setattr(verifier, "run_check", run)
    result = await verifier.verify(arguments(static_prerequisites, live=True))
    run.assert_not_awaited()
    assert result["live_submitted"] is False and result["submissions_consumed_total"] == 0


def test_exact_researched_binary_pin_has_no_request_or_cli_override():
    assert verifier.RESEARCHED_EXECUTABLE_SHA256 == "39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1"
    assert "--executable-sha256" not in verifier.argument_parser().format_help()


@pytest.mark.asyncio
async def test_same_version_different_binary_stops_before_native_status_and_reservation(
    static_prerequisites, monkeypatch
):
    monkeypatch.setattr(
        verifier, "RESEARCHED_EXECUTABLE_SHA256", "39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1"
    )
    version = Mock(return_value=(0, verifier.VERSION.encode()))
    authentication = Mock(return_value={"native_auth_configured": True, "chatgpt_status_reported": True})
    run = AsyncMock()
    monkeypatch.setattr(verifier.probes, "bounded_version", version)
    monkeypatch.setattr(verifier, "authentication_preflight", authentication)
    monkeypatch.setattr(verifier, "run_check", run)
    result = await verifier.verify(arguments(static_prerequisites, live=True))
    version.assert_not_called()
    authentication.assert_not_called()
    run.assert_not_awaited()
    assert result["preflight_failure"] == "unresearched_binary"
    assert result["binary_matches_researched"] is False
    assert result["live_submitted"] is False and result["submissions_consumed_total"] == 0
    assert not (verifier.LINEAGE_ROOT / "first/submitted.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,chatgpt", [(True, False), (False, True), (False, False)])
async def test_subscription_lineage_requires_both_native_auth_and_chatgpt_readiness(
    static_prerequisites, monkeypatch, configured, chatgpt
):
    monkeypatch.setattr(
        verifier,
        "authentication_preflight",
        lambda *_: {
            "native_auth_configured": configured,
            "chatgpt_status_reported": chatgpt,
        },
    )
    run = AsyncMock()
    monkeypatch.setattr(verifier, "run_check", run)
    result = await verifier.verify(arguments(static_prerequisites, live=True))
    run.assert_not_awaited()
    assert result["preflight_failure"] == "chatgpt_auth_not_ready"
    assert result["live_submitted"] is False and result["submissions_consumed_total"] == 0
    assert not (verifier.LINEAGE_ROOT / "first/submitted.json").exists()


def test_recipe_disallows_native_permission_or_configuration_overrides(monkeypatch):
    _, _, policy = verifier.modules()
    assert verifier.recipe_proof()["verified"] is True
    monkeypatch.setattr(policy, "native_generation_arguments", lambda _: ("exec", "--json", "--sandbox", "read-only"))
    assert verifier.recipe_proof()["verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["text", "tool", "cancel"])
async def test_real_production_service_final_file_and_cancellation_with_synthetic_cli(tmp_path, check):
    _, _, policy = verifier.modules()
    from clink.parsers.codex_native_generation import parser_contract_digest

    binary = tmp_path / "synthetic-codex"
    binary.write_text("#!" + sys.executable + "\n" + r"""
import json,sys,time
from pathlib import Path
sys.stdin.buffer.read()
output=Path(sys.argv[sys.argv.index('--output-last-message')+1])
print(json.dumps({'type':'thread.started','thread_id':'00000000-0000-0000-0000-000000000000'}),flush=True)
print(json.dumps({'type':'turn.started'}),flush=True)
check=CHECK
if check=='cancel':
 time.sleep(60)
text=(Path('tool-context.txt').read_text() if check=='tool' else 'Create a plan for Atlas cache migration on Friday, owned by Mira. Include exactly two risks, rollback steps, and acceptance checks for the engineering team.')
output.write_text(text)
print(json.dumps({'type':'item.completed','item':{'id':'i1','type':'agent_message','text':'Intermediate streamed content intentionally differs'}}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'cache_write_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0}}),flush=True)
""".replace("CHECK", repr(check)))
    binary.chmod(0o700)
    binding = {
        "executable_sha256": verifier.executable_digest(str(binary)),
        "recipe_sha256": policy.native_generation_recipe_digest(),
        "parser_sha256": parser_contract_digest(),
    }
    preflight = tmp_path / "preflight.json"
    verifier.persist(preflight, {"scope": "synthetic_no_model", **binding})
    root = tmp_path / "fixture"
    root.mkdir()
    result = await verifier.run_check(check, str(binary), root, binding, preflight, 30)
    assert result["passed"] is True, result
    assert result["production_service_used"] is True
    assert result["private_output_removed_after_cleanup"] is True
    assert result["dispatch_count"] == result["spawn_count"] == 1
    assert result["actual_model"] is None
    assert result["pool_running_after_shutdown"] == result["pool_children_after_shutdown"] == 0
    assert "Intermediate streamed content" not in json.dumps(result)


def test_help_is_explicit_and_read_only():
    parsed = verifier.argument_parser().parse_args(
        ["--label", "prepare", "--source-manifest", "proof.json", "--source-manifest-sha256", "0" * 64]
    )
    assert parsed.live is False
    assert "one bounded native-generation submission" in verifier.argument_parser().format_help()
