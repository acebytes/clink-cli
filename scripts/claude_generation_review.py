"""Consume conductor-owned original-receipt attestations without producing them."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from clink.generation_contracts import canonical_json, sha256
from scripts.verify_native_generation import evidence_json, regular_bytes, valid_sha256

TASK = "fn-3-bundle-claude-and-codex-clink.1"
BOUNDED_EXCEPTION_TASK = "fn-3-bundle-claude-and-codex-clink.6"
SPEC = "fn-3-bundle-claude-and-codex-clink"
AUTHORITY = "conductor_owned_original_receipt"


def _read(path: Path, *, within: Path | None = None) -> bytes:
    if (
        not path.is_absolute()
        or path.resolve(strict=True) != path
        or (within is not None and not path.is_relative_to(within))
    ):
        raise ValueError("Completion evidence must use its canonical original confined path")
    return regular_bytes(path)


def completion_proof(
    project: Path,
    review: dict[str, Any],
    original_receipt: Path,
    reviewed_input_sha256: str,
    expected_attestation_sha256: str,
    *,
    review_kind: Literal["legacy", "bounded_exception"] = "legacy",
) -> dict[str, Any]:
    """Verify local conductor provenance, without claiming raw-output linkage."""
    if review_kind not in ("legacy", "bounded_exception"):
        raise ValueError("Completion review kind must be legacy or bounded_exception")
    task = TASK if review_kind == "legacy" else BOUNDED_EXCEPTION_TASK
    reservation = review.get("review_reservation_id")
    if (
        project.resolve(strict=True) != project
        or not project.is_absolute()
        or not isinstance(reservation, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reservation) is None
        or not valid_sha256(reviewed_input_sha256)
        or not valid_sha256(expected_attestation_sha256)
    ):
        raise ValueError("Completion attestation needs an exact reservation and explicit hashes")
    attestation_path = project / ".flow/claude-generation-review-attestations" / (reservation + ".json")
    raw = _read(attestation_path)
    attestation = evidence_json(raw)
    wanted = {
        "schema_version": 1,
        "scope": (
            "claude_generation_replacement_review_completion"
            if review_kind == "legacy"
            else "claude_generation_bounded_exception_review_completion"
        ),
        "authority": AUTHORITY,
        "task_id": task,
        "reservation_id": reservation,
        "session_id": review.get("session_id"),
        "reviewed_input_sha256": reviewed_input_sha256,
        "native_output_to_review_verified": False,
    }
    transport_mode = None
    if review_kind == "bounded_exception":
        transport_mode = attestation.get("transport_mode")
        if transport_mode not in ("fresh-only", "resume-only"):
            raise ValueError("Bounded exception completion needs an explicit supported transport mode")
        wanted["transport_mode"] = transport_mode
    if (
        sha256(raw) != expected_attestation_sha256
        or set(attestation) != {*wanted, "original_receipt", "review_attempt_sha256", "review_transport_sha256"}
        or type(attestation.get("schema_version")) is not int
        or attestation.get("native_output_to_review_verified") is not False
        or any(attestation.get(key) != value for key, value in wanted.items())
        or not all(valid_sha256(attestation.get(key)) for key in ("review_attempt_sha256", "review_transport_sha256"))
    ):
        raise ValueError("Completion attestation differs from its pin or supported conductor authority")
    reference = attestation["original_receipt"]
    if (
        not isinstance(reference, dict)
        or set(reference) != {"path", "sha256"}
        or not isinstance(reference.get("path"), str)
        or not valid_sha256(reference.get("sha256"))
    ):
        raise ValueError("Completion attestation must identify one original receipt")
    original = Path(reference["path"])
    receipt_raw = _read(original, within=project / "test_output/bundled-generation")
    if (
        original_receipt != original
        or sha256(receipt_raw) != reference["sha256"]
        or evidence_json(receipt_raw) != review
    ):
        raise ValueError("Independent review must be the unchanged original conductor-attested receipt")

    spec = evidence_json(_read(project / ".flow/specs" / (SPEC + ".json")))
    attempts = spec.get("review_attempts")
    if not isinstance(attempts, list) or any(not isinstance(row, dict) for row in attempts):
        raise ValueError("Canonical review attempt inventory is malformed")
    selected = [row for row in attempts if row.get("reservation_id") == reservation]
    if len(selected) != 1:
        raise ValueError("Completion requires one unique canonical review attempt")
    attempt = selected[0]
    required = {
        "reservation_id": reservation,
        "task": task,
        "scope": "impl:" + task,
        "backend": "codex",
        "kind": "impl",
        "counter_kind": "impl",
        "outcome": "verdict",
        "verdict": "SHIP",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "round_consumed": True,
        "head_sha_observed": True,
        "forced": False,
        "failure_class": None,
        "superseded_by": None,
        "finalized": {"receipt": "complete", "digest": "complete", "status": "not_applicable"},
    }
    try:
        timestamp = datetime.fromisoformat(attempt["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError, TypeError):
        raise ValueError("Canonical review attempt timestamp is invalid") from None
    if (
        sha256(canonical_json(attempt)) != attestation["review_attempt_sha256"]
        or any(key not in attempt or attempt[key] != value for key, value in required.items())
        or any(type(attempt.get(key)) is not bool for key in ("round_consumed", "head_sha_observed", "forced"))
        or timestamp.utcoffset() is None
        or any(
            not isinstance(attempt.get(key), str) or re.fullmatch(r"[0-9a-f]{40}", attempt[key]) is None
            for key in ("base_sha", "head_sha")
        )
        or ("output_sha256" in attempt and not valid_sha256(attempt["output_sha256"]))
        or ("output_bytes" in attempt and (type(attempt["output_bytes"]) is not int or attempt["output_bytes"] < 0))
    ):
        raise ValueError("Completion does not bind a finalized configured native SHIP attempt")

    transport_raw = _read(project / ".flow/tmp/scoped-review-transports" / (reservation + ".json"))
    transport = evidence_json(transport_raw)
    expected_transport = {
        "schemaVersion": 1,
        "reservationId": reservation,
        "taskId": task,
        "baseSha": attempt["base_sha"],
        "headSha": attempt["head_sha"],
        "sessionId": review.get("session_id"),
        "requestedSpec": "codex:gpt-6.1-sol:high",
        "sandbox": "read-only",
        "resumeOnly": True,
        "runtimeVerification": "unverified",
    }
    if transport_mode == "fresh-only":
        expected_transport.update(sessionId=None, resumeOnly=False, transportMode="fresh-only")
    if (
        sha256(transport_raw) != attestation["review_transport_sha256"]
        or set(transport) != {*expected_transport, "packetSha256", "allowlistSha256"}
        or type(transport.get("schemaVersion")) is not int
        or transport.get("resumeOnly") is not expected_transport["resumeOnly"]
        or any(transport.get(key) != value for key, value in expected_transport.items())
        or not all(valid_sha256(transport.get(key)) for key in ("packetSha256", "allowlistSha256"))
    ):
        raise ValueError("Completion transport must bind the same read-only resumed native review")
    proof = {
        "completion_attestation_sha256": expected_attestation_sha256,
        "review_attempt_sha256": attestation["review_attempt_sha256"],
        "review_transport_sha256": attestation["review_transport_sha256"],
        "original_receipt_sha256": reference["sha256"],
        "reviewed_input_sha256": reviewed_input_sha256,
        "authority": AUTHORITY,
        "native_output_to_review_verified": False,
        "runtime_model_verified": False,
    }
    if review_kind == "bounded_exception":
        proof["transport_mode"] = transport_mode
    return proof
