"""The one fixed Claude budget exception requires immutable independent authority."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from clink.generation_contracts import canonical_json, sha256
from scripts import verify_claude_generation as verifier
from tests import test_claude_generation_verifier as legacy_tests
from tests.test_claude_generation_verifier import arguments, binding, replacement_packet

isolated = legacy_tests.isolated
static = legacy_tests.static


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _reference(path):
    return {"path": str(path), "sha256": sha256(path.read_bytes())}


def _seal(case):
    _write(case.input_path, case.reviewed)
    _write(case.review_path, case.review)
    _write(case.transport_path, case.transport)
    rows = json.loads(case.spec_path.read_bytes())["review_attempts"]
    rows = [row for row in rows if row["reservation_id"] != case.row["reservation_id"]] + [case.row]
    _write(case.spec_path, {"review_attempts": rows})
    case.attestation.update(
        original_receipt=_reference(case.review_path),
        review_attempt_sha256=sha256(canonical_json(case.row)),
        review_transport_sha256=sha256(case.transport_path.read_bytes()),
        reviewed_input_sha256=sha256(case.input_path.read_bytes()),
    )
    _write(case.attestation_path, case.attestation)
    for key, path in (
        ("diagnosis", case.diagnosis_path),
        ("reviewed_input", case.input_path),
        ("legacy_replacement_review", case.legacy_path),
        ("independent_review", case.review_path),
    ):
        case.packet[key] = _reference(path)
    case.packet["completion_attestation_sha256"] = sha256(case.attestation_path.read_bytes())
    _write(case.packet_path, case.packet)


def _exception_packet(root, history, legacy_path):
    diagnosis = root / "exception-diagnosis.json"
    _write(
        diagnosis,
        {
            "independent_compatibility_gap": "thinking_tokens",
            "historical_failure1_cause": "unproven",
            "historical_failure2_actual_subtype": "unproven",
        },
    )
    prior = []
    for item in history:
        directory = item["directory"]
        prior.append(
            {
                **{key: item["entry"][key] for key in ("ordinal", "check", "passed", "replacement")},
                "candidate_sha256": item["preflight"]["candidate_sha256"],
                "preflight": _reference(directory / "preflight.json"),
                "reservation": _reference(directory / "submitted.json"),
                "receipt": _reference(directory / "receipt.json"),
            }
        )
    reviewed = {
        "schema_version": 1,
        "scope": "claude_generation_bounded_exception_input",
        "profile": verifier.PROFILE,
        "policy": "claude_text_third_attempt_v1",
        "diagnosed_failure_ordinal": 2,
        "prior_submissions": prior,
        "legacy_replacement_review_sha256": sha256(legacy_path.read_bytes()),
        "diagnosis_sha256": sha256(diagnosis.read_bytes()),
        "candidate_sha256": binding()["candidate_sha256"],
        "budget": {
            "max_total": 5,
            "max_per_gate": {"text": 3, "read": 1, "cancel": 1},
            "max_replacements": 2,
            "max_per_invocation": 1,
            "automatic_retries": 0,
        },
    }
    review = {
        **verifier.REVIEW_CORE,
        "id": "fn-3-bundle-claude-and-codex-clink.6",
        "session_id": "11111111-2222-3333-4444-666666666666",
        "timestamp": "2026-11-02T01:00:00Z",
        "review_reservation_id": "claude-exception_1",
        "review": (
            "Synthetic independent exception approval; no native model call.\n\n"
            + verifier.EXCEPTION_MARKER
            + sha256(canonical_json(reviewed))
            + "\n\n<verdict>SHIP</verdict>"
        ),
    }
    spec_path = root / ".flow/specs/fn-3-bundle-claude-and-codex-clink.json"
    row = {
        **json.loads(spec_path.read_bytes())["review_attempts"][0],
        "reservation_id": review["review_reservation_id"],
        "task": review["id"],
        "scope": "impl:" + review["id"],
    }
    transport = {
        "schemaVersion": 1,
        "reservationId": review["review_reservation_id"],
        "taskId": review["id"],
        "baseSha": row["base_sha"],
        "headSha": row["head_sha"],
        "packetSha256": "c" * 64,
        "allowlistSha256": "d" * 64,
        "sessionId": None,
        "requestedSpec": "codex:gpt-6.1-sol:high",
        "sandbox": "read-only",
        "resumeOnly": False,
        "runtimeVerification": "unverified",
        "transportMode": "fresh-only",
    }
    attestation = {
        "schema_version": 1,
        "scope": "claude_generation_bounded_exception_review_completion",
        "authority": "conductor_owned_original_receipt",
        "task_id": review["id"],
        "reservation_id": review["review_reservation_id"],
        "session_id": review["session_id"],
        "native_output_to_review_verified": False,
        "transport_mode": "fresh-only",
    }
    case = SimpleNamespace(
        root=root,
        reviewed=reviewed,
        review=review,
        row=row,
        transport=transport,
        attestation=attestation,
        packet={
            "schema_version": 1,
            "scope": "claude_generation_bounded_exception_review",
            "profile": verifier.PROFILE,
        },
        diagnosis_path=diagnosis,
        legacy_path=legacy_path,
        input_path=root / "exception-input.json",
        review_path=root / "test_output/bundled-generation/exception-review/receipt.json",
        spec_path=spec_path,
        transport_path=root / ".flow/tmp/scoped-review-transports/claude-exception_1.json",
        attestation_path=root / ".flow/claude-generation-review-attestations/claude-exception_1.json",
        packet_path=root / "exception-packet.json",
    )
    _seal(case)
    return case


@pytest.fixture
async def exception_case(static, monkeypatch):
    run = AsyncMock(return_value={"check": "text", "passed": False})
    monkeypatch.setattr(verifier, "run_check", run)
    await verifier.verify(arguments())
    legacy_path, legacy_sha = replacement_packet(static, verifier.authoritative_history())
    await verifier.verify(
        arguments(label="second", replacement_review=legacy_path, replacement_review_sha256=legacy_sha)
    )
    legacy = json.loads(legacy_path.read_bytes())
    monkeypatch.setattr(verifier, "EXCEPTION_LEGACY_PACKET", str(legacy_path.relative_to(static)))
    monkeypatch.setattr(verifier, "EXCEPTION_LEGACY_PACKET_SHA256", legacy_sha)
    monkeypatch.setattr(
        verifier, "EXCEPTION_LEGACY_REVIEW", str(Path(legacy["independent_review"]["path"]).relative_to(static))
    )
    monkeypatch.setattr(verifier, "EXCEPTION_LEGACY_REVIEW_SHA256", legacy["independent_review"]["sha256"])
    history = verifier.authoritative_history()
    case = _exception_packet(static, history, legacy_path)
    case.run = run
    case.originals = {
        path: path.read_bytes()
        for directory in (history[0]["directory"], history[1]["directory"])
        for path in directory.iterdir()
        if path.name in {"preflight.json", "submitted.json", "receipt.json"}
    }
    case.originals.update({legacy_path: legacy_path.read_bytes()})

    async def passed(check, *_args):
        return {"check": check, "passed": True}

    run.side_effect = passed
    return case


def _exception_arguments(case, **changes):
    return arguments(
        **{
            "label": "third",
            "exception_review": case.packet_path,
            "exception_review_sha256": sha256(case.packet_path.read_bytes()),
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_fixed_exception_completes_only_the_original_five_entry_lineage(exception_case):
    case = exception_case
    third = await verifier.verify(_exception_arguments(case))
    assert third["live_submitted"] is True and third["gate_passed"] is True
    assert third["schema_version"] == 2
    assert third["submission_allowance_total"] == 5
    assert third["maximum_submissions_per_gate"] == {"text": 3, "read": 1, "cancel": 1}
    assert third["maximum_replacements"] == 2
    await verifier.verify(arguments(label="fourth", check="read"))
    fifth = await verifier.verify(arguments(label="fifth", check="cancel"))
    assert fifth["all_claude_generation_gates_passed"] is True
    assert fifth["submissions_consumed_total"] == case.run.await_count == 5
    history = verifier.authoritative_history()
    assert [item["entry"]["check"] for item in history] == ["text", "text", "text", "read", "cancel"]
    assert [item["entry"]["replacement"] for item in history] == [False, True, True, False, False]
    assert [item["entry"]["passed"] for item in history] == [False, False, True, True, True]
    for item in history[2:]:
        for record in (item["reservation"], item["preflight"], item["terminal"]):
            assert record["schema_version"] == 2
            assert record["budget_exception"] == third["budget_exception"]
    refused = await verifier.verify(arguments(label="sixth", check="cancel"))
    assert refused["live_submitted"] is False and refused["submissions_consumed_total"] == 5
    assert case.run.await_count == 5
    assert all(path.read_bytes() == content for path, content in case.originals.items())
    with pytest.raises(ValueError):
        verifier.validate_ledger(fifth["submission_ledger"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["no_proof", "legacy_proof", "missing_pin", "wrong_pin", "missing_file"])
async def test_no_exception_authority_is_inferred_from_spent_legacy_review(exception_case, mode):
    case = exception_case
    changes = {}
    if mode == "no_proof":
        changes.update(exception_review=None, exception_review_sha256=None)
    elif mode == "legacy_proof":
        changes.update(exception_review=case.legacy_path, exception_review_sha256=sha256(case.legacy_path.read_bytes()))
    elif mode == "missing_pin":
        changes["exception_review_sha256"] = None
    elif mode == "wrong_pin":
        changes["exception_review_sha256"] = "0" * 64
    else:
        changes["exception_review"] = case.root / "missing.json"
    result = await verifier.verify(_exception_arguments(case, **changes))
    assert result["live_submitted"] is False and result["submissions_consumed_total"] == 2
    assert case.run.await_count == 2
    assert not (verifier.LINEAGE_ROOT / "third/submitted.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name",
    ["extra", "version", "profile", "missing_field", "duplicate_key", "malformed", "oversized", "symlink"],
)
async def test_packet_shape_or_unsafe_original_cannot_reserve(exception_case, case_name):
    case = exception_case
    if case_name == "extra":
        case.packet["approved"] = True
    elif case_name == "version":
        case.packet["schema_version"] = True
    elif case_name == "profile":
        case.packet["profile"] = "native_task"
    elif case_name == "missing_field":
        del case.packet["legacy_replacement_review"]
    _write(case.packet_path, case.packet)
    if case_name == "duplicate_key":
        case.packet_path.write_bytes(case.packet_path.read_bytes()[:-1] + b',"schema_version":1}')
    elif case_name == "malformed":
        case.packet_path.write_bytes(b"{malformed}")
    elif case_name == "oversized":
        case.packet_path.write_bytes(b" " * (1024 * 1024 + 1))
    elif case_name == "symlink":
        original = case.packet_path.with_name("original-exception.json")
        case.packet_path.rename(original)
        case.packet_path.symlink_to(original)
    result = await verifier.verify(_exception_arguments(case))
    assert result["live_submitted"] is False and case.run.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name",
    ["candidate", "first_failure", "second_failure", "budget", "budget_bool", "policy", "ordinal", "prior_extra"],
)
async def test_refreshed_input_pin_cannot_rewrite_exact_reviewed_contract(exception_case, case_name):
    case = exception_case
    if case_name == "candidate":
        case.reviewed["candidate_sha256"] = "0" * 64
    elif case_name in {"first_failure", "second_failure"}:
        index = 0 if case_name == "first_failure" else 1
        case.reviewed["prior_submissions"][index]["receipt"]["sha256"] = "0" * 64
    elif case_name == "budget":
        case.reviewed["budget"]["max_total"] = 6
    elif case_name == "budget_bool":
        case.reviewed["budget"]["max_per_invocation"] = True
    elif case_name == "policy":
        case.reviewed["policy"] = "caller_selected_budget"
    elif case_name == "ordinal":
        case.reviewed["diagnosed_failure_ordinal"] = 1
    else:
        case.reviewed["prior_submissions"][0]["extra"] = "not permitted"
    _seal(case)
    result = await verifier.verify(_exception_arguments(case))
    assert result["live_submitted"] is False and result["submissions_consumed_total"] == 2
    assert case.run.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "which", ["legacy_packet", "legacy_receipt", "exception_receipt", "old_failure", "legacy_authority"]
)
async def test_copied_or_changed_original_authority_is_never_reusable(exception_case, which):
    case = exception_case
    if which == "old_failure":
        original = verifier.LINEAGE_ROOT / "first/receipt.json"
        original.chmod(0o600)
        original.write_bytes(original.read_bytes() + b"\n")
    elif which == "legacy_authority":
        attestation = case.root / ".flow/claude-generation-review-attestations/claude-replacement_1.json"
        attestation.write_bytes(attestation.read_bytes() + b"\n")
    else:
        key = {
            "legacy_packet": "legacy_replacement_review",
            "legacy_receipt": None,
            "exception_receipt": "independent_review",
        }[which]
        if key is not None:
            original = Path(case.packet[key]["path"])
            copied = original.with_name("copied-original.json")
            copied.write_bytes(original.read_bytes())
            case.packet[key]["path"] = str(copied)
        else:
            legacy = json.loads(case.legacy_path.read_bytes())
            original = Path(legacy["independent_review"]["path"])
            original.chmod(0o600)
        original.write_bytes(original.read_bytes() + b"\n")
        _write(case.packet_path, case.packet)
    result = await verifier.verify(_exception_arguments(case))
    assert result["live_submitted"] is False
    assert case.run.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("id", verifier.REVIEW_TASK),
        ("model", "gpt-6-astra"),
        ("effort", "ultra"),
        ("verdict", "NEEDS_WORK"),
        ("session_id", "not-a-session"),
        ("timestamp", "2026-11-02T01:00:00"),
    ],
)
async def test_task6_independent_review_identity_is_fixed(exception_case, field, value):
    case = exception_case
    case.review[field] = value
    _seal(case)
    result = await verifier.verify(_exception_arguments(case))
    assert result["live_submitted"] is False and case.run.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "wrong",
        "duplicate",
        "quote",
        "indent",
        "fenced",
        "nonterminal",
        "legacy",
        "native",
        "two_verdicts",
        "no_blank",
        "extra_blank",
        "lazy_quote",
        "lazy_list",
    ],
)
async def test_marker_requires_exact_unquoted_approval_before_the_sole_terminal_verdict(exception_case, mode):
    case = exception_case
    marker = verifier.EXCEPTION_MARKER + sha256(case.input_path.read_bytes())
    text = marker + "\n\n<verdict>SHIP</verdict>"
    if mode == "missing":
        text = "Source is correct.\n\n<verdict>SHIP</verdict>"
    elif mode == "wrong":
        text = text.replace(marker, verifier.EXCEPTION_MARKER + "0" * 64)
    elif mode == "duplicate":
        text = marker + "\n\n" + text
    elif mode == "quote":
        text = "> " + text
    elif mode == "indent":
        text = " " + text
    elif mode == "fenced":
        text = "```text\n" + text
    elif mode == "nonterminal":
        text += "\nExtra output"
    elif mode == "legacy":
        text = verifier.APPROVAL_MARKER + "0" * 64 + "\n\n" + text
    elif mode == "native":
        text = "NATIVE_GENERATION_REPLACEMENT_APPROVED_SHA256=" + "0" * 64 + "\n\n" + text
    elif mode == "two_verdicts":
        text = "<verdict>SHIP</verdict>\n\n" + text
    elif mode == "no_blank":
        text = text.replace("\n\n", "\n")
    elif mode == "lazy_quote":
        text = "> quoted text\n" + text
    elif mode == "lazy_list":
        text = "- list item\n" + text
    else:
        text = text.replace("\n\n", "\n\n\n")
    case.review["review"] = text
    _seal(case)
    result = await verifier.verify(_exception_arguments(case))
    assert result["live_submitted"] is False and case.run.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinal", [3, 4, 5])
@pytest.mark.parametrize("failure", ["failed", "interrupted", "missing_terminal", "before_dispatch"])
async def test_every_reserved_exception_failure_stops_the_remaining_schedule(
    exception_case, monkeypatch, ordinal, failure
):
    case = exception_case
    for current in range(3, ordinal):
        await verifier.verify(_exception_arguments(case) if current == 3 else arguments(label="fourth", check="read"))
    before_calls = case.run.await_count
    if failure == "interrupted":
        case.run.side_effect = asyncio.CancelledError
    elif failure != "before_dispatch":
        case.run.side_effect = lambda check, *_args: {"check": check, "passed": False}
    else:

        def fail_identity(_binding):
            raise ValueError("Synthetic runtime identity changed after reservation")

        monkeypatch.setattr(verifier, "_assert_runtime_identity", fail_identity)
    check = {3: "text", 4: "read", 5: "cancel"}[ordinal]
    label = "failed-final"
    args = _exception_arguments(case, label=label) if ordinal == 3 else arguments(label=label, check=check)
    if failure == "interrupted":
        with pytest.raises(asyncio.CancelledError):
            await verifier.verify(args)
    else:
        result = await verifier.verify(args)
        assert result["live_submitted"] is True and result["gate_passed"] is False
    if failure == "missing_terminal":
        (verifier.LINEAGE_ROOT / label / "receipt.json").unlink()
    history = verifier.authoritative_history()
    assert len(history) == ordinal and history[-1]["entry"]["passed"] is False
    refused = await verifier.verify(arguments(label="after-failure", check="read" if ordinal == 3 else "cancel"))
    assert refused["live_submitted"] is False and refused["submissions_consumed_total"] == ordinal
    assert case.run.await_count == before_calls + (failure != "before_dispatch")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["new_proof", "legacy_proof", "wrong_order", "new_candidate", "proof_changed", "old_authority_changed"]
)
async def test_continuation_reopens_original_authority_and_binds_exact_candidate(exception_case, monkeypatch, mode):
    case = exception_case
    await verifier.verify(_exception_arguments(case))
    changes = {}
    if mode == "new_proof":
        changes.update(exception_review=case.packet_path, exception_review_sha256=sha256(case.packet_path.read_bytes()))
    elif mode == "legacy_proof":
        changes.update(
            replacement_review=case.legacy_path, replacement_review_sha256=sha256(case.legacy_path.read_bytes())
        )
    elif mode == "wrong_order":
        changes["check"] = "cancel"
    elif mode == "new_candidate":
        changed = binding()
        changed["implementation_source_sha256"] = "0" * 64
        changed["candidate_sha256"] = verifier._candidate_digest(changed)
        monkeypatch.setattr(verifier, "preflight", lambda _: ("synthetic-claude", changed))
    elif mode == "proof_changed":
        case.packet_path.write_bytes(case.packet_path.read_bytes() + b"\n")
    else:
        case.legacy_path.write_bytes(case.legacy_path.read_bytes() + b"\n")
    if mode in {"proof_changed", "old_authority_changed"}:
        with pytest.raises(ValueError):
            await verifier.verify(arguments(label="fourth", check="read", **changes))
    else:
        args = {"label": "fourth", "check": "read", **changes}
        result = await verifier.verify(arguments(**args))
        assert result["live_submitted"] is False and result["submissions_consumed_total"] == 3
    assert case.run.await_count == 3


@pytest.mark.asyncio
async def test_exception_preparation_is_nonlive_and_lock_covers_the_final_reservation(exception_case):
    case = exception_case
    dry = await verifier.verify(_exception_arguments(case, label="dry-exception", live=False))
    assert dry["live_submitted"] is False and dry["schema_version"] == 1
    assert "budget_exception" not in dry
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(check, *_args):
        entered.set()
        await release.wait()
        return {"check": check, "passed": True}

    case.run.side_effect = blocked
    task = asyncio.create_task(verifier.verify(_exception_arguments(case)))
    await entered.wait()
    try:
        with pytest.raises(ValueError, match="Another"):
            await verifier.verify(_exception_arguments(case, label="concurrent"))
    finally:
        release.set()
        await task
    assert case.run.await_count == 3


def _rewrite_third_record(case, target, field, value):
    directory = verifier.LINEAGE_ROOT / "third"
    before_path = directory / "preflight.json"
    reservation_path = directory / "submitted.json"
    terminal_path = directory / "receipt.json"
    before = json.loads(before_path.read_bytes())
    reservation = json.loads(reservation_path.read_bytes())
    terminal = json.loads(terminal_path.read_bytes())
    record = {"preflight": before, "reservation": reservation, "terminal": terminal}[target]
    record[field] = value
    for path in (before_path, reservation_path, terminal_path):
        path.chmod(0o600)
    _write(before_path, before)
    reservation["preflight_sha256"] = sha256(before_path.read_bytes())
    _write(reservation_path, reservation)
    terminal["submission_ledger"][-1]["reservation_sha256"] = sha256(reservation_path.read_bytes())
    _write(terminal_path, terminal)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["preflight", "reservation", "terminal"])
@pytest.mark.parametrize(
    "field,value", [("extra", True), ("schema_version", 1), ("budget_exception", {"approved": True})]
)
async def test_schema2_history_rejects_repinned_unknown_fields_or_downgrade(exception_case, target, field, value):
    case = exception_case
    await verifier.verify(_exception_arguments(case))
    _rewrite_third_record(case, target, field, value)
    with pytest.raises(ValueError):
        verifier.authoritative_history()
    assert case.run.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["preflight", "terminal"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("submission_allowance_total", 6),
        ("maximum_submissions_per_gate", {"text": 3, "read": 2, "cancel": 1}),
        ("maximum_replacements", 3),
        ("maximum_submissions_per_invocation", True),
        ("automatic_retries", False),
    ],
)
async def test_schema2_limits_are_strict_fixed_metadata_not_selected_authority(exception_case, target, field, value):
    case = exception_case
    await verifier.verify(_exception_arguments(case))
    _rewrite_third_record(case, target, field, value)
    with pytest.raises(ValueError, match="limits"):
        verifier.authoritative_history()


@pytest.mark.asyncio
async def test_full_exception_candidate_is_required_for_aggregate_success_even_in_nonlive_preparation(
    exception_case, monkeypatch
):
    case = exception_case
    await verifier.verify(_exception_arguments(case))
    await verifier.verify(arguments(label="fourth", check="read"))
    await verifier.verify(arguments(label="fifth", check="cancel"))
    changed = binding()
    changed["verifier_sha256"] = "0" * 64
    changed["candidate_sha256"] = verifier._candidate_digest(changed)
    monkeypatch.setattr(verifier, "preflight", lambda _: ("synthetic-claude", changed))
    dry = await verifier.verify(arguments(label="changed-dry", check="read", live=False))
    assert dry["all_claude_generation_gates_passed"] is False
    refused = await verifier.verify(arguments(label="changed-live", check="read"))
    assert refused["live_submitted"] is False and refused["all_claude_generation_gates_passed"] is False
    assert case.run.await_count == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["text", "cancel"])
async def test_successful_exception_text_cannot_repeat_or_skip_read(exception_case, check):
    case = exception_case
    await verifier.verify(_exception_arguments(case))
    result = await verifier.verify(arguments(label="out-of-order", check=check))
    assert result["live_submitted"] is False and result["submissions_consumed_total"] == 3
    assert case.run.await_count == 3


@pytest.mark.asyncio
async def test_exception_rejects_mixed_legacy_and_new_review_arguments(exception_case):
    case = exception_case
    result = await verifier.verify(
        _exception_arguments(
            case, replacement_review=case.legacy_path, replacement_review_sha256=sha256(case.legacy_path.read_bytes())
        )
    )
    assert result["live_submitted"] is False and case.run.await_count == 2


@pytest.mark.asyncio
async def test_nonlive_continuation_rejects_new_review_arguments(exception_case):
    case = exception_case
    await verifier.verify(_exception_arguments(case))
    result = await verifier.verify(_exception_arguments(case, label="new-proof-dry", check="read", live=False))
    assert result["live_submitted"] is False and result["error_class"] == "ValueError"
    assert case.run.await_count == 3


@pytest.mark.asyncio
async def test_deeply_nested_exception_packet_fails_cleanly_without_reservation(exception_case):
    case = exception_case
    case.packet_path.write_bytes(b'{"unsupported":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}")
    result = await verifier.verify(_exception_arguments(case))
    assert result["live_submitted"] is False and result["error_class"] == "ValueError"
    assert case.run.await_count == 2


@pytest.mark.asyncio
async def test_deeply_nested_reservation_fails_cleanly_before_authority_reconstruction(exception_case):
    case = exception_case
    await verifier.verify(_exception_arguments(case))
    path = verifier.LINEAGE_ROOT / "third/submitted.json"
    path.chmod(0o600)
    path.write_bytes(b'{"budget_exception":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}")
    with pytest.raises(ValueError):
        verifier.authoritative_history()
    assert case.run.await_count == 3
