"""Forward-only completion attestation checks use disposable conductor-owned files."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.claude_generation_review import completion_proof

TASK = "fn-3-bundle-claude-and-codex-clink.1"
SPEC = "fn-3-bundle-claude-and-codex-clink"
RESERVATION = "claude-replacement_1"
SESSION = "11111111-2222-3333-4444-555555555555"
BASE = "a" * 40
HEAD = "b" * 40


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _bind(case):
    _write(case.spec_path, {"id": SPEC, "review_attempts": case.rows})
    _write(case.transport_path, case.transport)
    case.attestation["review_attempt_sha256"] = _digest(_canonical(case.row))
    case.attestation["review_transport_sha256"] = _digest(case.transport_path.read_bytes())
    _write(case.attestation_path, case.attestation)
    case.expected_sha256 = _digest(case.attestation_path.read_bytes())


def _proof(case, **changes):
    arguments = {
        "project": case.project,
        "review": case.review,
        "original_receipt": case.receipt_path,
        "reviewed_input_sha256": case.input_sha256,
        "expected_attestation_sha256": case.expected_sha256,
    }
    arguments.update(changes)
    return completion_proof(**arguments)


@pytest.fixture
def completion_case(tmp_path):
    project = tmp_path / "project"
    review = {
        "type": "impl_review",
        "id": TASK,
        "mode": "codex",
        "verdict": "SHIP",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "spec": "codex:gpt-6.1-sol:high",
        "session_id": SESSION,
        "timestamp": "2026-11-02T00:00:00Z",
        "review_reservation_id": RESERVATION,
        "review": "Synthetic SHIP review; no native output or model call.",
    }
    receipt = project / "test_output/bundled-generation/review-replacement/receipt.json"
    _write(receipt, review)
    row = {
        "reservation_id": RESERVATION,
        "task": TASK,
        "scope": "impl:" + TASK,
        "backend": "codex",
        "kind": "impl",
        "counter_kind": "impl",
        "outcome": "verdict",
        "verdict": "SHIP",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "round_consumed": True,
        "head_sha_observed": True,
        "base_sha": BASE,
        "head_sha": HEAD,
        "timestamp": "2026-11-02T00:01:00+00:00",
        "finalized": {"receipt": "complete", "digest": "complete", "status": "not_applicable"},
        "failure_class": None,
        "superseded_by": None,
        "forced": False,
    }
    transport = {
        "schemaVersion": 1,
        "reservationId": RESERVATION,
        "taskId": TASK,
        "baseSha": BASE,
        "headSha": HEAD,
        "packetSha256": "c" * 64,
        "allowlistSha256": "d" * 64,
        "sessionId": SESSION,
        "requestedSpec": "codex:gpt-6.1-sol:high",
        "sandbox": "read-only",
        "resumeOnly": True,
        "runtimeVerification": "unverified",
    }
    input_sha256 = "e" * 64
    attestation = {
        "schema_version": 1,
        "scope": "claude_generation_replacement_review_completion",
        "authority": "conductor_owned_original_receipt",
        "task_id": TASK,
        "reservation_id": RESERVATION,
        "session_id": SESSION,
        "original_receipt": {"path": str(receipt), "sha256": _digest(receipt.read_bytes())},
        "review_attempt_sha256": "0" * 64,
        "review_transport_sha256": "0" * 64,
        "reviewed_input_sha256": input_sha256,
        "native_output_to_review_verified": False,
    }
    case = SimpleNamespace(
        project=project,
        review=review,
        receipt_path=receipt,
        row=row,
        rows=[row],
        transport=transport,
        attestation=attestation,
        input_sha256=input_sha256,
        spec_path=project / f".flow/specs/{SPEC}.json",
        transport_path=project / f".flow/tmp/scoped-review-transports/{RESERVATION}.json",
        attestation_path=project / f".flow/claude-generation-review-attestations/{RESERVATION}.json",
    )
    _bind(case)
    return case


@pytest.mark.parametrize("optional_output", [False, True])
def test_completion_binds_original_receipt_and_finalized_row_without_a_journal(completion_case, optional_output):
    case = completion_case
    if optional_output:
        case.row.update(output_sha256="f" * 64, output_bytes=321)
        _bind(case)
    assert not (case.project / ".flow/review-runs").exists()
    assert case.row["timestamp"] != case.review["timestamp"]
    assert _proof(case) == {
        "completion_attestation_sha256": case.expected_sha256,
        "review_attempt_sha256": case.attestation["review_attempt_sha256"],
        "review_transport_sha256": case.attestation["review_transport_sha256"],
        "original_receipt_sha256": _digest(case.receipt_path.read_bytes()),
        "reviewed_input_sha256": case.input_sha256,
        "authority": "conductor_owned_original_receipt",
        "native_output_to_review_verified": False,
        "runtime_model_verified": False,
    }


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("attestation", "schema_version", True),
        ("attestation", "scope", "another_completion_scope"),
        ("attestation", "authority", "caller_owned_copy"),
        ("attestation", "task_id", "another-task.1"),
        ("attestation", "reservation_id", "another-reservation"),
        ("attestation", "session_id", "22222222-2222-3333-4444-555555555555"),
        ("attestation", "reviewed_input_sha256", "0" * 64),
        ("attestation", "native_output_to_review_verified", True),
        ("attestation", "native_output_to_review_verified", 0),
        ("reference", "path", "receipt.json"),
        ("reference", "path", []),
        ("reference", "sha256", "not-a-hash"),
        ("reference", "extra", "unaccepted"),
        ("attestation", "extra", "unaccepted"),
        ("row", "reservation_id", "another-reservation"),
        ("row", "task", "another-task.1"),
        ("row", "scope", "impl:spec"),
        ("row", "backend", "cursor"),
        ("row", "kind", "plan"),
        ("row", "counter_kind", "plan"),
        ("row", "outcome", "transport_failure"),
        ("row", "verdict", "NEEDS_WORK"),
        ("row", "model", "gpt-6-astra"),
        ("row", "effort", "ultra"),
        ("row", "round_consumed", False),
        ("row", "head_sha_observed", 1),
        ("row", "base_sha", "not-a-commit"),
        ("row", "head_sha", "b" * 39),
        ("row", "timestamp", "2026-11-02T00:01:00"),
        ("row", "finalized", {"receipt": "complete", "digest": "pending", "status": "not_applicable"}),
        ("row", "failure_class", "transport_failure"),
        ("row", "superseded_by", "another-reservation"),
        ("row", "forced", True),
        ("row", "output_sha256", "not-a-hash"),
        ("row", "output_bytes", True),
        ("row", "output_bytes", -1),
        ("transport", "schemaVersion", True),
        ("transport", "reservationId", "another-reservation"),
        ("transport", "taskId", "another-task.1"),
        ("transport", "baseSha", "c" * 40),
        ("transport", "headSha", "c" * 40),
        ("transport", "packetSha256", "not-a-hash"),
        ("transport", "allowlistSha256", "not-a-hash"),
        ("transport", "sessionId", "22222222-2222-3333-4444-555555555555"),
        ("transport", "requestedSpec", "codex:gpt-6-astra:ultra"),
        ("transport", "sandbox", "workspace-write"),
        ("transport", "resumeOnly", 1),
        ("transport", "runtimeVerification", "verified"),
        ("transport", "transportMode", "fresh-only"),
    ],
    ids=lambda value: str(value),
)
def test_completion_rejects_semantic_changes_after_refreshing_their_hash_pins(completion_case, target, field, value):
    case = completion_case
    source = case.attestation["original_receipt"] if target == "reference" else getattr(case, target)
    source[field] = value
    if target in {"attestation", "reference"}:
        _write(case.attestation_path, case.attestation)
        case.expected_sha256 = _digest(case.attestation_path.read_bytes())
    else:
        _bind(case)
    with pytest.raises((ValueError, OSError)):
        _proof(case)


@pytest.mark.parametrize("case_name", ["missing_field", "duplicate_row", "row_hash", "transport_hash", "input_hash"])
def test_completion_rejects_missing_identity_or_nonunique_and_unbound_sources(completion_case, case_name):
    case = completion_case
    if case_name == "missing_field":
        del case.attestation["reviewed_input_sha256"]
    elif case_name == "duplicate_row":
        case.rows.append(dict(case.row))
        _bind(case)
    elif case_name == "row_hash":
        case.attestation["review_attempt_sha256"] = "0" * 64
    elif case_name == "transport_hash":
        case.attestation["review_transport_sha256"] = "0" * 64
    if case_name != "input_hash":
        _write(case.attestation_path, case.attestation)
        case.expected_sha256 = _digest(case.attestation_path.read_bytes())
    with pytest.raises((ValueError, OSError)):
        _proof(case, **({"reviewed_input_sha256": "not-a-hash"} if case_name == "input_hash" else {}))


@pytest.mark.parametrize(
    "case_name",
    [
        "wrong_attestation_pin",
        "wrong_fixed_path",
        "malformed_attestation",
        "duplicate_attestation_key",
        "oversized_attestation",
        "attestation_symlink",
        "attestation_parent_symlink",
        "spec_symlink",
        "transport_symlink",
        "receipt_symlink",
        "copied_identical_receipt",
        "original_overwritten",
    ],
)
def test_completion_rejects_nonoriginal_or_unsafe_files(completion_case, case_name):
    case = completion_case
    arguments = {}
    if case_name == "wrong_attestation_pin":
        case.expected_sha256 = "0" * 64
    elif case_name == "wrong_fixed_path":
        case.attestation_path.rename(case.attestation_path.with_name("caller-selected.json"))
    elif case_name in {"malformed_attestation", "duplicate_attestation_key", "oversized_attestation"}:
        content = {
            "malformed_attestation": b"{not-json}",
            "duplicate_attestation_key": _canonical(case.attestation)[:-1] + b',"schema_version":1}',
            "oversized_attestation": b" " * (1024 * 1024 + 1),
        }[case_name]
        case.attestation_path.write_bytes(content)
        case.expected_sha256 = _digest(content)
    elif case_name == "attestation_parent_symlink":
        directory = case.attestation_path.parent
        target = directory.with_name("copied-attestations")
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
    elif case_name.endswith("_symlink"):
        attribute = {
            "attestation": "attestation_path",
            "spec": "spec_path",
            "transport": "transport_path",
            "receipt": "receipt_path",
        }[case_name.removesuffix("_symlink")]
        path = getattr(case, attribute)
        target = path.with_name("copied-" + path.name)
        path.rename(target)
        path.symlink_to(target)
    elif case_name == "copied_identical_receipt":
        copied = case.receipt_path.with_name("copied-receipt.json")
        copied.write_bytes(case.receipt_path.read_bytes())
        arguments["original_receipt"] = copied
    else:
        case.receipt_path.write_bytes(case.receipt_path.read_bytes() + b"\n")
    with pytest.raises((ValueError, OSError)):
        _proof(case, **arguments)


BOUNDED_EXCEPTION_TASK = "fn-3-bundle-claude-and-codex-clink.6"


@pytest.fixture(params=["fresh-only", "resume-only"])
def bounded_completion_case(completion_case, request):
    case = completion_case
    case.transport_mode = request.param
    case.review["id"] = BOUNDED_EXCEPTION_TASK
    _write(case.receipt_path, case.review)
    case.row.update(task=BOUNDED_EXCEPTION_TASK, scope="impl:" + BOUNDED_EXCEPTION_TASK)
    case.transport["taskId"] = BOUNDED_EXCEPTION_TASK
    if case.transport_mode == "fresh-only":
        case.transport.update(sessionId=None, resumeOnly=False, transportMode="fresh-only")
    case.attestation.update(
        scope="claude_generation_bounded_exception_review_completion",
        task_id=BOUNDED_EXCEPTION_TASK,
        transport_mode=case.transport_mode,
    )
    case.attestation["original_receipt"]["sha256"] = _digest(case.receipt_path.read_bytes())
    _bind(case)
    return case


def test_explicit_legacy_completion_preserves_the_default_contract(completion_case):
    assert _proof(completion_case, review_kind="legacy") == _proof(completion_case)


@pytest.mark.parametrize("review_kind", ["unknown", "fresh-only", "", None, True, 1, [], {}])
def test_completion_rejects_unsupported_review_kinds(completion_case, review_kind):
    with pytest.raises(ValueError, match="review kind"):
        _proof(completion_case, review_kind=review_kind)


def test_completion_review_kind_is_keyword_only(completion_case):
    case = completion_case
    with pytest.raises(TypeError):
        completion_proof(
            case.project,
            case.review,
            case.receipt_path,
            case.input_sha256,
            case.expected_sha256,
            "bounded_exception",
        )


def test_bounded_completion_binds_the_fixed_task_and_transport_mode(bounded_completion_case):
    case = bounded_completion_case
    assert _proof(case, review_kind="bounded_exception") == {
        "completion_attestation_sha256": case.expected_sha256,
        "review_attempt_sha256": case.attestation["review_attempt_sha256"],
        "review_transport_sha256": case.attestation["review_transport_sha256"],
        "original_receipt_sha256": _digest(case.receipt_path.read_bytes()),
        "reviewed_input_sha256": case.input_sha256,
        "authority": "conductor_owned_original_receipt",
        "native_output_to_review_verified": False,
        "runtime_model_verified": False,
        "transport_mode": case.transport_mode,
    }
    assert case.attestation["session_id"] == case.review["session_id"] == SESSION
    assert case.transport["sessionId"] == (None if case.transport_mode == "fresh-only" else SESSION)


def test_bounded_completion_cannot_select_the_default_legacy_contract(bounded_completion_case):
    with pytest.raises(ValueError):
        _proof(bounded_completion_case)


def test_legacy_completion_cannot_select_the_bounded_contract(completion_case):
    with pytest.raises(ValueError):
        _proof(completion_case, review_kind="bounded_exception")


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("attestation", "schema_version", True),
        ("attestation", "task_id", TASK),
        ("attestation", "scope", "claude_generation_replacement_review_completion"),
        ("attestation", "authority", "caller_owned_copy"),
        ("attestation", "session_id", "22222222-2222-3333-4444-555555555555"),
        ("attestation", "transport_mode", "unknown"),
        ("attestation", "transport_mode", None),
        ("attestation", "transport_mode", False),
        ("attestation", "transport_mode", []),
        ("attestation", "transport_mode", {}),
        ("attestation", "native_output_to_review_verified", True),
        ("attestation", "extra", "unaccepted"),
        ("reference", "extra", "unaccepted"),
        ("reference", "path", "receipt.json"),
        ("row", "task", TASK),
        ("row", "scope", "impl:" + TASK),
        ("row", "model", "gpt-6-astra"),
        ("row", "effort", "ultra"),
        ("row", "round_consumed", False),
        ("row", "head_sha_observed", 1),
        ("row", "base_sha", "not-a-commit"),
        ("row", "head_sha", "b" * 39),
        ("row", "finalized", {"receipt": "complete", "digest": "pending", "status": "not_applicable"}),
        ("row", "superseded_by", "another-reservation"),
        ("row", "forced", True),
        ("transport", "schemaVersion", True),
        ("transport", "taskId", TASK),
        ("transport", "baseSha", "c" * 40),
        ("transport", "headSha", "c" * 40),
        ("transport", "sessionId", "22222222-2222-3333-4444-555555555555"),
        ("transport", "requestedSpec", "codex:gpt-6-astra:ultra"),
        ("transport", "sandbox", "workspace-write"),
        ("transport", "runtimeVerification", "verified"),
        ("transport", "packetSha256", "not-a-hash"),
        ("transport", "allowlistSha256", "not-a-hash"),
        ("transport", "extra", "unaccepted"),
        ("transport", "transportMode", "resume-only"),
        ("transport", "transportMode", None),
    ],
    ids=lambda value: str(value),
)
def test_bounded_completion_rejects_semantic_changes_with_refreshed_pins(bounded_completion_case, target, field, value):
    case = bounded_completion_case
    source = case.attestation["original_receipt"] if target == "reference" else getattr(case, target)
    source[field] = value
    _bind(case)
    with pytest.raises((ValueError, OSError)):
        _proof(case, review_kind="bounded_exception")


@pytest.mark.parametrize(
    "case_name",
    ["missing_mode", "other_mode", "wrong_resume_flag", "numeric_resume_flag", "wrong_session", "transport_mode_shape"],
)
def test_bounded_completion_rejects_mixed_transport_modes(bounded_completion_case, case_name):
    case = bounded_completion_case
    if case_name == "missing_mode":
        del case.attestation["transport_mode"]
    elif case_name == "other_mode":
        case.attestation["transport_mode"] = "resume-only" if case.transport_mode == "fresh-only" else "fresh-only"
    elif case_name == "wrong_resume_flag":
        case.transport["resumeOnly"] = not case.transport["resumeOnly"]
    elif case_name == "numeric_resume_flag":
        case.transport["resumeOnly"] = int(case.transport["resumeOnly"])
    elif case_name == "wrong_session":
        case.transport["sessionId"] = SESSION if case.transport_mode == "fresh-only" else None
    elif case.transport_mode == "fresh-only":
        del case.transport["transportMode"]
    else:
        case.transport["transportMode"] = "resume-only"
    _bind(case)
    with pytest.raises(ValueError):
        _proof(case, review_kind="bounded_exception")


@pytest.mark.parametrize(
    "case_name",
    [
        "attestation_pin",
        "attempt_pin",
        "transport_pin",
        "receipt_pin",
        "duplicate_row",
        "malformed_attestation",
        "malformed_transport",
        "receipt_overwritten",
        "receipt_replaced",
        "transport_symlink",
    ],
)
def test_bounded_completion_rejects_unbound_or_malformed_evidence(bounded_completion_case, case_name):
    case = bounded_completion_case
    arguments = {}
    if case_name == "attestation_pin":
        case.expected_sha256 = "0" * 64
    elif case_name in {"attempt_pin", "transport_pin", "receipt_pin"}:
        if case_name == "receipt_pin":
            case.attestation["original_receipt"]["sha256"] = "0" * 64
        else:
            key = "review_attempt_sha256" if case_name == "attempt_pin" else "review_transport_sha256"
            case.attestation[key] = "0" * 64
        _write(case.attestation_path, case.attestation)
        case.expected_sha256 = _digest(case.attestation_path.read_bytes())
    elif case_name == "duplicate_row":
        case.rows.append(dict(case.row))
        _bind(case)
    elif case_name in {"malformed_attestation", "malformed_transport"}:
        path = case.attestation_path if case_name == "malformed_attestation" else case.transport_path
        path.write_bytes(b"{not-json}")
        if case_name == "malformed_attestation":
            case.expected_sha256 = _digest(path.read_bytes())
        else:
            case.attestation["review_transport_sha256"] = _digest(path.read_bytes())
            _write(case.attestation_path, case.attestation)
            case.expected_sha256 = _digest(case.attestation_path.read_bytes())
    elif case_name == "receipt_overwritten":
        case.receipt_path.write_bytes(case.receipt_path.read_bytes() + b"\n")
    elif case_name == "receipt_replaced":
        copied = case.receipt_path.with_name("copied-receipt.json")
        copied.write_bytes(case.receipt_path.read_bytes())
        arguments["original_receipt"] = copied
    else:
        copied = case.transport_path.with_name("copied-transport.json")
        case.transport_path.rename(copied)
        case.transport_path.symlink_to(copied)
    with pytest.raises((ValueError, OSError)):
        _proof(case, review_kind="bounded_exception", **arguments)
