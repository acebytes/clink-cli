"""Verify candidate Grok/Agy generation with separate, bounded family lineages.

The public snapshot refuses live submissions to preserve archived call budgets.
Use the original private verifier and complete history for live continuation.

Default invocation performs only local/version preflight. Explicit --live reserves
one submission before dispatch, with at most five calls per family, two per gate
and one diagnosed, independently reviewed replacement. Explicit fixture trust is
temporary and confined to its owned synthetic project. No automatic retry,
production promotion, or counter reset is provided.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import fcntl
import json
import logging
import os
import re
import signal
import struct
import sys
import time
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from clink.generation_contracts import canonical_json  # noqa: E402
from clink.models import ResolvedCLIClient  # noqa: E402
from clink.native_generation_policy import NativeGenerationAdmission  # noqa: E402
from clink.runner import ProcessPool  # noqa: E402
from scripts import verify_native_adapters as fixtures  # noqa: E402
from scripts import verify_native_generation as native  # noqa: E402
from scripts.native_diagnostics import exception_diagnostic, valid_exception_diagnostic  # noqa: E402
from scripts.private_evidence import LIVE_HISTORY_REFUSAL, refuse_live_verification  # noqa: E402
from scripts.verify_generation_policy import digest, evidence_directory, persist, safe_identifier  # noqa: E402
from scripts.verify_native_generation import regular_bytes, regular_json, valid_sha256  # noqa: E402

PROFILE = "generation_native_v1"
SCOPE = "candidate_family_generation"
FAMILIES = ("grok", "agy")
CHECKS = ("text", "action", "permission", "cancel")
MAX_SUBMISSIONS = 5
REPLACEMENT_BASES = {"failed_gate", "invalidated_cleanup_evidence"}
DIAGNOSTIC_BASIS = "diagnostic_budget_extension"
DIAGNOSTIC_PURPOSE = "cleanup_diagnostic_only"
DIAGNOSTIC_LIMITS = {"total_calls": 6, "cancel_attempts": 3, "exceptional_replacements": 2}
DIAGNOSTIC_USER_AUTHORIZATION_SHA256 = "374d72fc82aace9f81208514e13f9c1bd21b91833314f4c609695ea4ca214630"
DIAGNOSTIC_PRIOR_RECEIPT_SHA256 = "6e6f91d54380408261214ae2ef5cda22751f98d550512299512b9f369a78fb8c"
DIAGNOSTIC_PRIOR_IMPLEMENTATION_SHA256 = "3c05c20dc7f0713c7f54f04f6bbc73780aadb5a767aabb54791a9610b98f49bf"
ORIGINAL_SOURCE_COMMIT = "e9feea12fcdaafbdef91f570a924f166563409bc"
ORIGINAL_IMPLEMENTATION_SHA256 = "e4b0d3b20a882ee6d7111aee92efe1378fb6228b5075e983ac2fa665218348cd"
ORIGINAL_GROK_CANDIDATE_SHA256 = "8b3eca4419d7068868ae7519a00e823135c4c4a509b8d440a660ba5c9aec2d60"
LINEAGE_ROOT = PROJECT / "docs/verification/family-generation"
APPROVAL_MARKER = "FAMILY_GENERATION_REPLACEMENT_APPROVED_SHA256="
REVIEW_CORE = {**native.REVIEW_CORE, "id": "branch"}
LIMITATIONS = [
    "Candidate admission is process-local; production and native-task admissions remain unchanged.",
    "Native configuration, permissions, hooks, integrations and provider-internal retries remain native-owned.",
    "Completed is execution status, not fulfillment or complete permission-observability proof.",
    "Unreported actual model remains unknown; native default selection does not identify the executed model.",
    "Cancellation does not undo effects or prove detached or remote work stopped.",
]


def generation_modules() -> tuple[Any, Any, Any]:
    from clink import native_generation, native_generation_contracts, native_generation_families

    return native_generation, native_generation_contracts, native_generation_families


def family_root(family: str) -> Path:
    if family not in FAMILIES:
        raise ValueError("Select a supported candidate family")
    return LINEAGE_ROOT / family


def lineage_path(path: Path, family: str) -> Path:
    location = path.expanduser().absolute()
    if (
        location.name not in {"preflight.json", "submitted.json", "receipt.json"}
        or location.parent.parent != family_root(family)
        or location.resolve(strict=True) != location
    ):
        raise ValueError("Family lineage evidence must retain its original canonical durable location")
    return location


def validate_ledger(ledger: Any, *, diagnostic: bool = False) -> None:
    if not isinstance(ledger, list) or len(ledger) > (6 if diagnostic else MAX_SUBMISSIONS):
        raise ValueError("The five-submission family budget is exhausted or invalid")
    counts = dict.fromkeys(CHECKS, 0)
    replacements = 0
    previous = None
    for ordinal, item in enumerate(ledger, 1):
        fields = {"ordinal", "check", "passed", "replacement", "reservation_sha256"}
        basis = item.get("replacement_basis") if isinstance(item, dict) else None
        if diagnostic and ordinal == 6:
            if (
                not diagnostic_transition(ledger[:5])
                or not isinstance(item, dict)
                or set(item) != fields | {"replacement_basis"}
                or type(item.get("ordinal")) is not int
                or item["ordinal"] != 6
                or item.get("check") != "cancel"
                or type(item.get("passed")) is not bool
                or item.get("replacement") is not True
                or basis != DIAGNOSTIC_BASIS
                or not valid_sha256(item.get("reservation_sha256"))
            ):
                raise ValueError("Only the authorized sixth cancellation can extend the ledger")
            continue
        if (
            not isinstance(item, dict)
            or set(item) not in (fields, fields | {"replacement_basis"})
            or ("replacement_basis" in item and (not isinstance(basis, str) or basis not in REPLACEMENT_BASES))
            or type(item.get("ordinal")) is not int
            or item["ordinal"] != ordinal
            or item.get("check") not in CHECKS
            or type(item.get("passed")) is not bool
            or type(item.get("replacement")) is not bool
            or not valid_sha256(item.get("reservation_sha256"))
        ):
            raise ValueError("Invalid family-generation ledger")
        check = item["check"]
        counts[check] += 1
        replacements += int(item["replacement"])
        if counts[check] > 2 or item["replacement"] != (counts[check] == 2) or replacements > 1:
            raise ValueError("At most two attempts per gate and one replacement per family are allowed")
        invalidated = basis == "invalidated_cleanup_evidence" and invalidated_cleanup_transition(
            ledger[: ordinal - 1], check
        )
        if basis is not None and not item["replacement"]:
            raise ValueError("A replacement basis cannot authorize an ordinary submission")
        if item["replacement"] and not (
            invalidated or (previous is not None and not previous["passed"] and previous["check"] == check)
        ):
            raise ValueError("Only the immediately failed gate can receive a diagnosed replacement")
        if basis == "invalidated_cleanup_evidence" and not invalidated:
            raise ValueError("Invalidated cleanup evidence permits only the sole fifth cancellation replacement")
        if previous is not None and not previous["passed"] and not item["replacement"]:
            raise ValueError("An unexplained failed or interrupted attempt stops the family lineage")
        previous = item


def diagnostic_transition(ledger: list[dict]) -> bool:
    """Recognize the spent five-call shape only; the schema-3 proof grants authority."""
    if len(ledger) != 5:
        return False
    try:
        validate_ledger(ledger)
    except ValueError:
        return False
    return (
        {item["check"] for item in ledger[:3]} == set(CHECKS) - {"cancel"}
        and all(item["passed"] and not item["replacement"] for item in ledger[:3])
        and ledger[3]["check"] == ledger[4]["check"] == "cancel"
        and not ledger[3]["passed"]
        and not ledger[3]["replacement"]
        and not ledger[4]["passed"]
        and ledger[4]["replacement"]
        and ledger[4].get("replacement_basis") == "failed_gate"
    )


def invalidated_cleanup_transition(ledger: list[dict], check: str) -> bool:
    """Recognize the accounting shape only; pinned semantic proof supplies authority."""
    return (
        check == "cancel"
        and len(ledger) == 4
        and ledger[-1]["check"] == "cancel"
        and all(item["passed"] and not item["replacement"] for item in ledger)
        and {item["check"] for item in ledger[:3]} == set(CHECKS) - {"cancel"}
        and all("replacement_basis" not in item for item in ledger)
    )


def ledger_from(record: dict[str, Any], family: str) -> list[dict[str, Any]]:
    ledger = record.get("submission_ledger")
    if (
        record.get("scope") != SCOPE
        or record.get("profile") != PROFILE
        or record.get("family") != family
        or type(record.get("submissions_consumed_total")) is not int
        or not isinstance(ledger, list)
        or record["submissions_consumed_total"] != len(ledger)
    ):
        raise ValueError("Evidence is not this candidate family-generation lineage")
    diagnostic = type(record.get("schema_version")) is int and record["schema_version"] == 3
    if diagnostic:
        validate_diagnostic_record(record, family)
    validate_ledger(ledger, diagnostic=diagnostic)
    return json.loads(json.dumps(ledger))


def authoritative_history(family: str) -> list[dict[str, Any]]:
    """Reconcile original immutable reservations, including missing terminal outcomes."""
    paths = sorted(family_root(family).glob("*/submitted.json"))
    if len(paths) > (6 if family == "agy" else MAX_SUBMISSIONS):
        raise ValueError("The family submission budget is exhausted")
    history = []
    for path in paths:
        reservation, content = regular_json(lineage_path(path, family))
        ordinal = reservation.get("ordinal")
        if (
            type(reservation.get("schema_version")) is not int
            or reservation["schema_version"] not in {1, 2, 3}
            or reservation.get("scope") != SCOPE
            or reservation.get("profile") != PROFILE
            or reservation.get("family") != family
            or reservation.get("check") not in CHECKS
            or reservation.get("state") != "consumed_before_dispatch"
            or type(reservation.get("replacement")) is not bool
            or type(ordinal) is not int
            or not 1 <= ordinal <= (6 if reservation["schema_version"] == 3 else MAX_SUBMISSIONS)
            or not valid_sha256(reservation.get("preflight_sha256"))
        ):
            raise ValueError("Invalid durable family reservation")
        version = reservation["schema_version"]
        if version == 3 and (
            family != "agy"
            or ordinal != 6
            or reservation.get("check") != "cancel"
            or reservation.get("replacement") is not True
            or reservation.get("replacement_basis") != DIAGNOSTIC_BASIS
            or reservation.get("diagnostic_only") is not True
            or set(reservation)
            != {
                "schema_version",
                "scope",
                "profile",
                "family",
                "check",
                "ordinal",
                "replacement",
                "state",
                "preflight_sha256",
                "replacement_basis",
                "diagnostic_only",
                "prior_ledger_sha256",
                "user_authorization_sha256",
            }
            or not valid_sha256(reservation.get("prior_ledger_sha256"))
            or reservation.get("user_authorization_sha256") != DIAGNOSTIC_USER_AUTHORIZATION_SHA256
        ):
            raise ValueError("Invalid one-shot diagnostic reservation")
        if version == 2 and (
            set(reservation)
            != {
                "schema_version",
                "scope",
                "profile",
                "family",
                "check",
                "ordinal",
                "replacement",
                "state",
                "preflight_sha256",
                "replacement_basis",
            }
            or reservation["replacement"] is not True
            or not isinstance(reservation.get("replacement_basis"), str)
            or reservation["replacement_basis"] not in REPLACEMENT_BASES
        ):
            raise ValueError("Invalid schema-2 replacement reservation")
        preflight, preflight_bytes = regular_json(lineage_path(path.parent / "preflight.json", family))
        before = ledger_from(preflight, family)
        if (
            digest(preflight_bytes) != reservation["preflight_sha256"]
            or len(before) != ordinal - 1
            or preflight.get("check") != reservation["check"]
            or preflight.get("live_submitted") is not False
            or type(preflight.get("submissions_in_this_invocation")) is not int
            or preflight["submissions_in_this_invocation"] != 0
            or (
                version in {2, 3}
                and (type(preflight.get("schema_version")) is not int or preflight["schema_version"] != version)
            )
            or (
                version == 3
                and any(
                    preflight.get(key) != reservation[key]
                    for key in ("prior_ledger_sha256", "user_authorization_sha256", "diagnostic_only")
                )
            )
        ):
            raise ValueError("Durable reservation changed its preflight binding")
        terminal_path = path.parent / "receipt.json"
        terminal = None
        passed = False
        if terminal_path.exists() or terminal_path.is_symlink():
            terminal, _ = regular_json(lineage_path(terminal_path, family))
            after = ledger_from(terminal, family)
            observation = terminal.get("observation")
            passed = terminal.get("gate_passed")
            if (
                terminal.get("check") != reservation["check"]
                or terminal.get("live_submitted") is not True
                or type(terminal.get("submissions_in_this_invocation")) is not int
                or terminal["submissions_in_this_invocation"] != 1
                or len(after) != ordinal
                or type(passed) is not bool
                or (
                    version in {2, 3}
                    and (type(terminal.get("schema_version")) is not int or terminal["schema_version"] != version)
                )
                or (observation is None and passed)
                or (
                    observation is not None
                    and (
                        not isinstance(observation, dict)
                        or observation.get("check") != reservation["check"]
                        or type(observation.get("passed")) is not bool
                        or observation["passed"] != passed
                    )
                )
            ):
                raise ValueError("Terminal outcome does not bind its family reservation")
        history.append(
            {
                "entry": {
                    "ordinal": ordinal,
                    "check": reservation["check"],
                    "passed": passed,
                    "replacement": reservation["replacement"],
                    "reservation_sha256": digest(content),
                    **({"replacement_basis": reservation["replacement_basis"]} if version in {2, 3} else {}),
                },
                "directory": path.parent,
                "preflight": preflight,
                "terminal": terminal,
                "reservation": reservation,
            }
        )
    history.sort(key=lambda item: item["entry"]["ordinal"])
    ledger = [item["entry"] for item in history]
    validate_ledger(ledger, diagnostic=family == "agy" and len(ledger) == 6)
    for item in history:
        for record in (item["preflight"], item["terminal"]):
            if record is not None:
                retained = ledger_from(record, family)
                if retained != ledger[: len(retained)]:
                    raise ValueError("Retained ledger changed authoritative reservations or outcomes")
        if item["reservation"]["schema_version"] in {2, 3}:
            version = item["reservation"]["schema_version"]
            ordinal = item["entry"]["ordinal"]
            summary = item["preflight"].get("replacement_review")
            if (
                not isinstance(summary, dict)
                or type(summary.get("schema_version")) is not int
                or summary["schema_version"] != version
            ):
                raise ValueError("Replacement history needs retained semantic authorization")
            reference = summary.get("review_packet")
            packet_path, _ = pinned_reference(reference, item["directory"])
            proof = replacement_proof(
                packet_path,
                reference["sha256"],
                item["preflight"].get("prior_evidence_sha256"),
                history[ordinal - 2]["entry"],
                family,
                None,
                history=history[: ordinal - 1],
                verify_current_revision=False,
                diagnostic_sidecar=Path(item["preflight"]["diagnostic_sidecar"]) if version == 3 else None,
            )
            if (
                summary != proof
                or proof["replacement_basis"] != item["entry"]["replacement_basis"]
                or any(
                    item["preflight"].get(key) != proof[key]
                    for key in (
                        "candidate_sha256",
                        "executable_sha256",
                        "recipe_sha256",
                        "parser_sha256",
                        "implementation_source_sha256",
                    )
                )
                or (item["terminal"] is not None and item["terminal"].get("replacement_review") != proof)
                or (
                    version == 3
                    and item["terminal"] is not None
                    and any(
                        item["terminal"].get(key) != proof[key]
                        for key in (
                            "candidate_sha256",
                            "executable_sha256",
                            "recipe_sha256",
                            "parser_sha256",
                            "implementation_source_sha256",
                        )
                    )
                )
            ):
                raise ValueError("Retained replacement authorization or candidate binding changed")
    return history


def load_history(path: Path | None, family: str) -> tuple[list[dict[str, Any]], str | None]:
    if path is None:
        return [], None
    location = lineage_path(path, family)
    if location.name not in {"preflight.json", "receipt.json"}:
        raise ValueError("Use original preflight or terminal family evidence")
    if location.name == "preflight.json" and (location.parent / "receipt.json").exists():
        raise ValueError("Use terminal evidence when it exists")
    record, content = regular_json(location)
    ledger = ledger_from(record, family)
    history = authoritative_history(family)
    own = next((item for item in history if item["directory"] == location.parent), None)
    if own is not None:
        ordinal = own["entry"]["ordinal"]
        if location.name == "preflight.json" and ordinal == len(ledger) + 1:
            ledger.append(dict(own["entry"]))
        elif ordinal != len(ledger):
            raise ValueError("Prior evidence omits a durable family reservation")
    elif record.get("live_submitted") is not False or record.get("submissions_in_this_invocation") != 0:
        raise ValueError("Prior terminal evidence has no durable family reservation")
    if ledger != [item["entry"] for item in history][: len(ledger)]:
        raise ValueError("Prior evidence changed authoritative family history")
    return ledger, digest(content)


def pinned_reference(reference: Any, directory: Path) -> tuple[Path, bytes]:
    """Read one existing bounded FileRef without retaining its content in summaries."""
    if (
        not isinstance(reference, dict)
        or set(reference) != {"path", "sha256"}
        or not isinstance(reference["path"], str)
        or not reference["path"]
        or not valid_sha256(reference["sha256"])
    ):
        raise ValueError("Replacement evidence references must contain only path and SHA-256")
    location = Path(reference["path"])
    location = location if location.is_absolute() else directory / location
    content = regular_bytes(location)
    if not content.strip() or digest(content) != reference["sha256"]:
        raise ValueError("Pinned replacement evidence changed or is empty")
    return location, content


def validate_production_equivalence(content: bytes, directory: Path, compatibility: dict, corrected: dict) -> None:
    proof = native.evidence_json(content)
    fixed = {
        "schema_version": 1,
        "scope": "family_generation_production_equivalence",
        "original_source_commit": compatibility["original_source_commit"],
        "original_implementation_source_sha256": compatibility["implementation_source_sha256"],
        "corrected_implementation_source_sha256": corrected["implementation_source_sha256"],
    }
    if (
        set(proof) != {*fixed, "original_components", "corrected_components", "delta", "tests"}
        or type(proof.get("schema_version")) is not int
        or any(proof.get(key) != value for key, value in fixed.items())
    ):
        raise ValueError("Production equivalence must bind the original and corrected implementation")
    manifests = []
    for key, expected in (
        ("original_components", fixed["original_implementation_source_sha256"]),
        ("corrected_components", fixed["corrected_implementation_source_sha256"]),
    ):
        manifest = proof[key]
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"schema_version", "files"}
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or not isinstance(manifest.get("files"), list)
            or not 1 <= len(manifest["files"]) <= 300
            or digest(canonical_json(manifest)) != expected
        ):
            raise ValueError("Production equivalence needs complete exact implementation manifests")
        files = {}
        for item in manifest["files"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "sha256"}
                or not isinstance(item["path"], str)
                or not re.fullmatch(r"(?:clink|scripts)/[A-Za-z0-9_./-]+\.py", item["path"])
                or ".." in Path(item["path"]).parts
                or item["path"] in files
                or not valid_sha256(item["sha256"])
            ):
                raise ValueError("Invalid implementation component manifest")
            files[item["path"]] = item["sha256"]
        manifests.append(files)
    original, current = manifests
    if set(original) != set(current):
        raise ValueError("Production equivalence cannot add or remove implementation components")
    delta = [
        {"path": path, "original_sha256": original[path], "corrected_sha256": current[path]}
        for path in sorted(original)
        if original[path] != current[path]
    ]
    if proof["delta"] != delta or [item["path"] for item in delta] != ["scripts/verify_family_generation.py"]:
        raise ValueError("Only the family verifier implementation component may change")
    if not isinstance(proof["tests"], list) or not 1 <= len(proof["tests"]) <= 16:
        raise ValueError("Production equivalence needs bounded pinned test evidence")
    for reference in proof["tests"]:
        pinned_reference(reference, directory)


def validate_compatibility(
    content: bytes, directory: Path, fixed: dict, history: list[dict], prior: str, corrected: dict
) -> None:
    """Bind reviewed eligibility to original records; booleans alone never grant a retry."""
    compatibility = native.evidence_json(content)
    identity = ("executable_sha256", "recipe_sha256", "parser_sha256", "implementation_source_sha256")
    expected = {
        "schema_version": 1,
        "scope": "family_generation_observer_compatibility",
        "profile": PROFILE,
        "family": fixed["family"],
        "replacement_basis": fixed["replacement_basis"],
        "prior_ordinal": fixed["prior_ordinal"],
    }
    if (
        set(compatibility)
        != {
            *expected,
            *identity,
            "original_source_commit",
            "original_candidate_sha256",
            "original_records",
            "production_equivalence",
            "observer_counterexample",
            "non_cancellation_proof",
        }
        or type(compatibility.get("schema_version")) is not int
        or type(compatibility.get("prior_ordinal")) is not int
        or any(compatibility.get(key) != value for key, value in expected.items())
        or not isinstance(compatibility.get("original_source_commit"), str)
        or re.fullmatch(r"[0-9a-f]{40}", compatibility["original_source_commit"]) is None
        or any(not valid_sha256(compatibility.get(key)) for key in (*identity, "original_candidate_sha256"))
        or not isinstance(compatibility.get("original_records"), list)
        or len(compatibility["original_records"]) != len(history)
        or not history
        or len(history) != fixed["prior_ordinal"]
        or any(corrected[key] != compatibility.get(key) for key in identity if key != "implementation_source_sha256")
    ):
        raise ValueError("Invalid closed original-record compatibility binding")
    if fixed["replacement_basis"] == "invalidated_cleanup_evidence" and (
        fixed["family"] != "grok"
        or not invalidated_cleanup_transition([item["entry"] for item in history], fixed["check"])
        or compatibility["original_source_commit"] != ORIGINAL_SOURCE_COMMIT
        or compatibility["implementation_source_sha256"] != ORIGINAL_IMPLEMENTATION_SHA256
        or compatibility["original_candidate_sha256"] != ORIGINAL_GROK_CANDIDATE_SHA256
    ):
        raise ValueError("Invalidated cleanup evidence must bind the exact original Grok fourth cancellation")
    last_evidence = None
    for index, (item, record) in enumerate(zip(history, compatibility["original_records"], strict=True)):
        entry = item["entry"]
        eligible = entry["passed"] and entry["check"] != "cancel"
        if (
            not isinstance(record, dict)
            or set(record)
            != {"ordinal", "check", "reported_passed", "retained_eligible", "preflight", "reservation", "terminal"}
            or type(record.get("ordinal")) is not int
            or record["ordinal"] != index + 1
            or record.get("check") != entry["check"]
            or type(record.get("reported_passed")) is not bool
            or record["reported_passed"] != entry["passed"]
            or type(record.get("retained_eligible")) is not bool
            or record["retained_eligible"] != eligible
            or (index < len(history) - 1 and entry["check"] != "cancel" and not eligible)
            or any(item["preflight"].get(key) != compatibility[key] for key in identity)
            or item["preflight"].get("candidate_sha256") != compatibility["original_candidate_sha256"]
            or candidate_digest(item["preflight"]) != compatibility["original_candidate_sha256"]
        ):
            raise ValueError("Compatibility eligibility or original candidate differs from authoritative history")
        for key, filename in (
            ("preflight", "preflight.json"),
            ("reservation", "submitted.json"),
            ("terminal", "receipt.json"),
        ):
            if key == "terminal" and item["terminal"] is None:
                if record[key] is not None or entry["passed"]:
                    raise ValueError("Missing terminal outcome cannot claim passed evidence")
                continue
            location, raw = pinned_reference(record[key], directory)
            if location.absolute() != lineage_path(item["directory"] / filename, fixed["family"]):
                raise ValueError("Compatibility must retain the actual original record locations")
            if key == "reservation" and digest(raw) != entry["reservation_sha256"]:
                raise ValueError("Compatibility reservation differs from authoritative history")
            if index == len(history) - 1 and key == ("terminal" if item["terminal"] is not None else "preflight"):
                last_evidence = digest(raw)
    if last_evidence != prior:
        raise ValueError("Compatibility must bind the exact prior evidence")
    linked = {}
    for key in ("production_equivalence", "observer_counterexample", "non_cancellation_proof"):
        location, linked[key] = pinned_reference(compatibility[key], directory)
        if key == "production_equivalence":
            validate_production_equivalence(linked[key], location.parent, compatibility, corrected)


def valid_diagnostic_limits(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value == DIAGNOSTIC_LIMITS
        and all(type(value[key]) is int for key in DIAGNOSTIC_LIMITS)
    )


def valid_diagnostic_publication(value: Any) -> bool:
    """Require actual invocation bindings and the exclusive writer's returned hash."""
    if not isinstance(value, dict) or set(value) != {"published", "run_id", "result_sha256", "sidecar_sha256"}:
        return False
    if type(value["published"]) is not bool or not isinstance(value["run_id"], str):
        return False
    try:
        if str(uuid.UUID(value["run_id"])) != value["run_id"]:
            return False
    except ValueError:
        return False
    return valid_sha256(value["result_sha256"]) and (
        valid_sha256(value["sidecar_sha256"]) if value["published"] else value["sidecar_sha256"] is None
    )


def diagnostic_sidecar_evidence(record: dict, reference: dict) -> bool:
    """Validate the same successful source/run/result publication now and in history."""
    publication = record.get("diagnostic_publication")
    if not valid_diagnostic_publication(publication) or not publication["published"]:
        raise ValueError("Diagnostic evidence requires successful publication by this invocation")
    observation = record.get("observation")
    if not isinstance(observation, dict) or observation.get("diagnostic_publication") != publication:
        raise ValueError("Diagnostic publication differs from the actual invocation observation")
    location, content = pinned_reference(reference, Path("/"))
    value = native.evidence_json(content)
    if (
        location != Path(record["diagnostic_sidecar"])
        or digest(content) != publication["sidecar_sha256"]
        or not valid_cleanup_diagnostic_sidecar(value)
        or value["run_id"] != publication["run_id"]
        or value["result_sha256"] != publication["result_sha256"]
        or value["executable_sha256"] != record.get("executable_sha256")
        or {key: value["source"].get(key) for key in ("runner_sha256", "verifier_sha256", "native_diagnostics_sha256")}
        != record["replacement_review"].get("diagnostic_source_sha256")
    ):
        raise ValueError("Diagnostic sidecar does not bind the invocation publication and executed source")
    return not value["capture_failed"]


def validate_diagnostic_record(record: dict, family: str) -> None:
    """Validate the closed extension metadata without treating it as proof of authority."""
    ledger = record.get("submission_ledger")
    diagnostic_fields = {
        "diagnostic_only",
        "diagnostic_purpose",
        "diagnostic_limits",
        "diagnostic_sidecar",
        "prior_ledger_sha256",
        "user_authorization_sha256",
        "diagnostic_sidecar_evidence",
        "diagnostic_observation_complete",
        "diagnostic_publication",
        "maximum_cancellation_submissions",
    }
    ordinary_fields = {
        "schema_version",
        "scope",
        "profile",
        "family",
        "check",
        "live_requested",
        "live_submitted",
        "gate_passed",
        "submission_ledger",
        "submissions_consumed_total",
        "submissions_in_this_invocation",
        "submission_allowance_total",
        "maximum_submissions_per_invocation",
        "maximum_submissions_per_gate",
        "maximum_replacements",
        "prior_evidence_sha256",
        "replacement_review",
        "automatic_retries",
        "candidate_admission_override",
        "production_admission_changed",
        "native_task_admission_changed",
        "native_trust_enrolled",
        "host_configuration_changed",
        "host_configuration_restored",
        "fixture_trust_requested",
        "fixture_trust",
        "raw_output_retained",
        "prompts_recorded",
        "native_sessions_recorded",
        "limitations",
        "observed_version",
        "executable_sha256",
        "recipe_sha256",
        "parser_sha256",
        "implementation_source_sha256",
        "candidate_sha256",
        "fixture_project_sha256",
        "fixture_config_sha256",
        "observation",
        "error_class",
        "trust_cleanup_error_class",
        "all_family_generation_gates_passed",
    }
    if set(record) - diagnostic_fields - ordinary_fields or not diagnostic_fields <= set(record):
        raise ValueError("Schema-3 diagnostic record fields are closed")
    fixed_policy = {
        "maximum_submissions_per_invocation": 1,
        "automatic_retries": 0,
        "candidate_admission_override": "process_local_only",
        "production_admission_changed": False,
        "native_task_admission_changed": False,
        "fixture_trust_requested": False,
        "native_trust_enrolled": False,
        "host_configuration_changed": False,
        "host_configuration_restored": True,
        "raw_output_retained": False,
        "prompts_recorded": False,
        "native_sessions_recorded": False,
        "live_requested": True,
    }
    if any(type(record.get(key)) is not type(value) or record[key] != value for key, value in fixed_policy.items()):
        raise ValueError("Diagnostic records cannot change retry, admission or native authority")
    summary = record.get("replacement_review")
    if not isinstance(summary, dict) or any(
        record.get(key) != summary.get(key)
        for key in (
            "diagnostic_purpose",
            "diagnostic_limits",
            "diagnostic_sidecar",
            "prior_ledger_sha256",
            "user_authorization_sha256",
        )
    ):
        raise ValueError("Diagnostic record differs from its retained authorization")
    if (
        family != "agy"
        or record.get("check") != "cancel"
        or record.get("diagnostic_only") is not True
        or record.get("diagnostic_purpose") != DIAGNOSTIC_PURPOSE
        or not valid_diagnostic_limits(record.get("diagnostic_limits"))
        or record.get("user_authorization_sha256") != DIAGNOSTIC_USER_AUTHORIZATION_SHA256
        or record.get("prior_evidence_sha256") != DIAGNOSTIC_PRIOR_RECEIPT_SHA256
        or not isinstance(ledger, list)
        or len(ledger) not in {5, 6}
        or not diagnostic_transition(ledger[:5])
        or record.get("prior_ledger_sha256") != digest(canonical_json(ledger[:5]))
        or type(record.get("submission_allowance_total")) is not int
        or record["submission_allowance_total"] != 6
        or type(record.get("maximum_replacements")) is not int
        or record["maximum_replacements"] != 2
        or type(record.get("maximum_submissions_per_gate")) is not int
        or record["maximum_submissions_per_gate"] != 2
        or type(record.get("maximum_cancellation_submissions")) is not int
        or record["maximum_cancellation_submissions"] != 3
        or not isinstance(record.get("diagnostic_sidecar"), str)
        or type(record.get("diagnostic_observation_complete")) is not bool
    ):
        raise ValueError("Invalid one-shot diagnostic record")
    sidecar = Path(record["diagnostic_sidecar"])
    if not sidecar.is_absolute() or str(diagnostic_sidecar_destination(sidecar)) != str(sidecar):
        raise ValueError("Diagnostic record needs its exact separate canonical sidecar")
    publication = record["diagnostic_publication"]
    observation = record.get("observation")
    observed_publication = observation.get("diagnostic_publication") if isinstance(observation, dict) else None
    if publication != observed_publication or (
        publication is not None and not valid_diagnostic_publication(publication)
    ):
        raise ValueError("Retained diagnostic publication differs from its invocation")
    reference = record.get("diagnostic_sidecar_evidence")
    if reference is not None:
        if record["diagnostic_observation_complete"] != diagnostic_sidecar_evidence(record, reference):
            raise ValueError("Retained diagnostic sidecar is invalid")
    elif record["diagnostic_observation_complete"]:
        raise ValueError("Missing diagnostic evidence cannot claim complete observations")


def validate_diagnostic_user_authorization(content: bytes) -> None:
    value = native.evidence_json(content)
    if (
        digest(content) != DIAGNOSTIC_USER_AUTHORIZATION_SHA256
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("answer") != "Approve the one additional diagnostic"
        or value.get("family") != "agy"
        or value.get("check") != "cancel"
        or type(value.get("authorized_next_ordinal")) is not int
        or value["authorized_next_ordinal"] != 6
        or value.get("bounded_accounting_change_authorized") is not True
        or not valid_diagnostic_limits(value.get("cumulative_limits"))
        or value.get("observer_baseline_commit") != "d193759fd2a1c75c144e53e0f990a62415d794df"
    ):
        raise ValueError("The exact explicit one-shot user authorization is required")


def validate_diagnostic_compatibility(
    content: bytes, directory: Path, fixed: dict, history: list[dict], prior: str, corrected: dict
) -> dict[str, str]:
    """Retain each real candidate independently and compare only against the fifth source."""
    proof = native.evidence_json(content)
    fifth = history[-1]["preflight"]
    expected = {
        "schema_version": 1,
        "scope": "family_generation_diagnostic_compatibility",
        "profile": PROFILE,
        "family": "agy",
        "replacement_basis": DIAGNOSTIC_BASIS,
        "prior_ordinal": 5,
        "prior_receipt_sha256": prior,
        "prior_ledger_sha256": fixed["prior_ledger_sha256"],
        "original_source_commit": "ab01466c84ba3661e8807b54d26d53b78c94e3bf",
        "original_candidate_sha256": fifth.get("candidate_sha256"),
        **{
            key: fifth.get(key)
            for key in ("executable_sha256", "recipe_sha256", "parser_sha256", "implementation_source_sha256")
        },
    }
    if (
        set(proof) != {*expected, "original_records", "production_equivalence", "fixture_authority"}
        or type(proof.get("schema_version")) is not int
        or type(proof.get("prior_ordinal")) is not int
        or any(proof.get(key) != value for key, value in expected.items())
        or fifth.get("implementation_source_sha256") != DIAGNOSTIC_PRIOR_IMPLEMENTATION_SHA256
        or any(corrected[key] != fifth.get(key) for key in ("executable_sha256", "recipe_sha256", "parser_sha256"))
        or not isinstance(proof.get("original_records"), list)
        or len(proof["original_records"]) != 5
    ):
        raise ValueError("Invalid actual-fifth-source diagnostic compatibility")
    for item, row in zip(history, proof["original_records"], strict=True):
        entry = item["entry"]
        if (
            not isinstance(row, dict)
            or set(row)
            != {"ordinal", "check", "reported_passed", "candidate_sha256", "preflight", "reservation", "terminal"}
            or type(row.get("ordinal")) is not int
            or row["ordinal"] != entry["ordinal"]
            or row.get("check") != entry["check"]
            or type(row.get("reported_passed")) is not bool
            or row["reported_passed"] != entry["passed"]
            or row.get("candidate_sha256") != item["preflight"].get("candidate_sha256")
            or candidate_digest(item["preflight"]) != row["candidate_sha256"]
            or item["terminal"] is None
        ):
            raise ValueError("Diagnostic history must retain each actual mixed candidate and outcome")
        for key, name in (
            ("preflight", "preflight.json"),
            ("reservation", "submitted.json"),
            ("terminal", "receipt.json"),
        ):
            location, raw = pinned_reference(row[key], directory)
            if location.absolute() != lineage_path(item["directory"] / name, "agy"):
                raise ValueError("Diagnostic compatibility changed canonical historical locations")
            if key == "reservation" and digest(raw) != entry["reservation_sha256"]:
                raise ValueError("Diagnostic compatibility changed a historical reservation")
            if entry["ordinal"] == 5 and key == "terminal" and digest(raw) != prior:
                raise ValueError("Diagnostic compatibility changed the actual fifth receipt")
    location, raw = pinned_reference(proof["production_equivalence"], directory)
    validate_production_equivalence(raw, location.parent, proof, corrected)
    equivalence = native.evidence_json(raw)
    components = {row["path"]: row["sha256"] for row in equivalence["original_components"]["files"]}
    fixture_names = (
        "scripts/verify_native_adapters.py",
        "scripts/native_agy_fixture.py",
        "scripts/native_mcp_fixture.py",
        "scripts/native_fixture_trust.py",
    )
    if any(name not in components for name in fixture_names):
        raise ValueError("Diagnostic equivalence must include the complete fixture authority")
    authority = {
        "executable_sha256": fifth["executable_sha256"],
        "recipe_sha256": fifth["recipe_sha256"],
        "parser_sha256": fifth["parser_sha256"],
        "native_permissions_changed": False,
        "timeout_seconds": 120,
        "fixture_components": [{"path": name, "sha256": components[name]} for name in fixture_names],
    }
    if (
        not isinstance(proof["fixture_authority"], dict)
        or proof["fixture_authority"] != authority
        or proof["fixture_authority"].get("native_permissions_changed") is not False
        or type(proof["fixture_authority"].get("timeout_seconds")) is not int
    ):
        raise ValueError("Diagnostic fixture, permission and timeout authority must remain unchanged")
    current = {row["path"]: row["sha256"] for row in equivalence["corrected_components"]["files"]}
    required = {
        "runner_sha256": "clink/runner.py",
        "verifier_sha256": "scripts/verify_family_generation.py",
        "native_diagnostics_sha256": "scripts/native_diagnostics.py",
    }
    if any(path not in current for path in required.values()):
        raise ValueError("Diagnostic equivalence must bind all sidecar source components")
    return {key: current[path] for key, path in required.items()}


def replacement_proof(
    path: Path | None,
    expected: str | None,
    prior: str | None,
    failed: dict,
    family: str,
    review_repo: Path | None,
    *,
    history: list[dict] | None = None,
    verify_current_revision: bool = True,
    expected_ledger: list[dict] | None = None,
    diagnostic_sidecar: Path | None = None,
) -> dict:
    if path is None or not valid_sha256(expected):
        raise ValueError("An unexplained failed attempt needs a pinned diagnosed independent replacement review")
    packet, raw = regular_json(path)
    version = packet.get("schema_version")
    if type(version) is not int or version not in {1, 2, 3}:
        raise ValueError("Replacement review must use schema 1, 2 or 3")
    ordinal_key = "failed_ordinal" if version == 1 else "prior_ordinal"
    fixed = {
        "schema_version": version,
        "scope": "family_generation_replacement_review",
        "profile": PROFILE,
        "family": family,
        "prior_receipt_sha256": prior,
        ordinal_key: failed["ordinal"],
        "check": failed["check"],
    }
    references = (
        "diagnosis",
        "correction",
        "reviewed_input",
        "independent_review",
        "source_binding",
        "runtime_attestation",
    )
    if version == 3:
        history = authoritative_history(family) if history is None else history
        ledger = [item["entry"] for item in history]
        if (
            family != "agy"
            or failed.get("ordinal") != 5
            or failed.get("check") != "cancel"
            or not diagnostic_transition(ledger)
            or history[-1]["entry"] != failed
            or prior != DIAGNOSTIC_PRIOR_RECEIPT_SHA256
            or (expected_ledger is not None and expected_ledger != ledger)
            or diagnostic_sidecar is None
            or not valid_diagnostic_limits(packet.get("diagnostic_limits"))
        ):
            raise ValueError("Only the exact failed fifth Agy cancellation can receive this diagnostic")
        sidecar = diagnostic_sidecar_destination(diagnostic_sidecar)
        if verify_current_revision and (sidecar.exists() or sidecar.is_symlink()):
            raise ValueError("The one-shot diagnostic requires a fresh sidecar destination")
        fixed.update(
            replacement_basis=DIAGNOSTIC_BASIS,
            diagnostic_purpose=DIAGNOSTIC_PURPOSE,
            diagnostic_limits=dict(DIAGNOSTIC_LIMITS),
            prior_ledger_sha256=digest(canonical_json(ledger)),
            diagnostic_sidecar=str(sidecar),
        )
        references += ("compatibility_binding", "user_authorization")
    if version == 2:
        basis = packet.get("replacement_basis")
        if not isinstance(basis, str) or basis not in REPLACEMENT_BASES:
            raise ValueError("Schema-2 replacement needs a closed replacement basis")
        fixed["replacement_basis"] = basis
        references += ("compatibility_binding",)
        history = authoritative_history(family) if history is None else history
        if (
            not history
            or history[-1]["entry"] != failed
            or (expected_ledger is not None and expected_ledger != [item["entry"] for item in history])
            or (basis == "failed_gate" and failed["passed"])
            or (
                basis == "invalidated_cleanup_evidence"
                and (
                    family != "grok"
                    or not invalidated_cleanup_transition([item["entry"] for item in history], fixed["check"])
                )
            )
        ):
            raise ValueError("Schema-2 replacement basis does not bind authoritative prior history")
    if (
        digest(raw) != expected
        or not valid_sha256(prior)
        or any(packet.get(key) != value for key, value in fixed.items())
        or type(packet.get("schema_version")) is not int
        or type(packet.get(ordinal_key)) is not int
        or set(packet) != {*fixed, *references}
    ):
        raise ValueError("Replacement review does not bind this family's failed attempt")
    linked = {}
    for key in references:
        reference = packet[key]
        if (
            not isinstance(reference, dict)
            or set(reference) != {"path", "sha256"}
            or not isinstance(reference["path"], str)
            or not reference["path"]
            or not valid_sha256(reference["sha256"])
        ):
            raise ValueError("Replacement evidence needs pinned diagnosis, correction, input and review")
        location = Path(reference["path"])
        content = regular_bytes(location if location.is_absolute() else path.parent / location)
        if not content.strip() or digest(content) != reference["sha256"]:
            raise ValueError("Pinned replacement evidence changed or is empty")
        linked[key] = content
    reviewed = native.evidence_json(linked["reviewed_input"])
    candidate = reviewed.get("candidate_sha256")
    expected_input = {
        **fixed,
        "scope": "family_generation_replacement_input",
        "diagnosis_sha256": packet["diagnosis"]["sha256"],
        "correction_sha256": packet["correction"]["sha256"],
        "candidate_sha256": candidate,
        "source_revision": reviewed.get("source_revision"),
        **({"compatibility_binding_sha256": packet["compatibility_binding"]["sha256"]} if version in {2, 3} else {}),
        **({"user_authorization_sha256": packet["user_authorization"]["sha256"]} if version == 3 else {}),
    }
    if (
        reviewed != expected_input
        or type(reviewed.get("schema_version")) is not int
        or type(reviewed.get(ordinal_key)) is not int
        or (version == 3 and not valid_diagnostic_limits(reviewed.get("diagnostic_limits")))
        or not valid_sha256(candidate)
        or linked["reviewed_input"] != canonical_json(reviewed)
    ):
        raise ValueError("Canonical reviewer input must bind the diagnosed correction and candidate")
    review = native.evidence_json(linked["independent_review"])
    if any(review.get(key) != value for key, value in REVIEW_CORE.items()):
        raise ValueError("Replacement requires the configured independent implementation SHIP review")
    try:
        session = review.get("session_id")
        valid_session = isinstance(session, str) and str(uuid.UUID(session)) == session
        timestamp = review.get("timestamp")
        valid_time = (
            isinstance(timestamp, str)
            and datetime.fromisoformat(timestamp.replace("Z", "+00:00")).utcoffset() is not None
        )
    except ValueError:
        valid_session = valid_time = False
    text = review.get("review")
    if not valid_session or not valid_time or not isinstance(text, str):
        raise ValueError("Independent review identity or timestamp is invalid")
    # Reuse the existing terminal-line/fence validation for this distinct marker.
    if native.APPROVAL_MARKER in text or text.count(APPROVAL_MARKER) != 1:
        raise ValueError("Reviewer must approve the separate family-generation replacement input")
    native.require_reviewer_marker(
        text.replace(APPROVAL_MARKER, native.APPROVAL_MARKER), packet["reviewed_input"]["sha256"]
    )
    if review_repo is None and verify_current_revision:
        raise ValueError("Replacement review needs the explicit authoritative Clink repository")
    binding = native.evidence_json(linked["source_binding"])
    runtime = native.evidence_json(linked["runtime_attestation"])
    if review_repo is None:
        retained_root = runtime.get("repository_root")
        if not isinstance(retained_root, str) or not Path(retained_root).is_absolute():
            raise ValueError("Retained review runtime needs its original authoritative repository")
        root = Path(retained_root)
    else:
        root = review_repo.expanduser().absolute()
    revision = reviewed["source_revision"]
    if (
        not isinstance(revision, dict)
        or set(revision) != {"base_commit", "head_commit", "diff_sha256"}
        or any(
            not isinstance(revision[key], str) or re.fullmatch(r"[0-9a-f]{40}", revision[key]) is None
            for key in ("base_commit", "head_commit")
        )
        or not valid_sha256(revision["diff_sha256"])
        or review.get("base") != revision["base_commit"]
    ):
        raise ValueError("Reviewer input must bind the actual reviewed Clink revision")
    hashes = {
        "candidate_sha256",
        "executable_sha256",
        "recipe_sha256",
        "parser_sha256",
        "implementation_source_sha256",
        "receipt_sha256",
        "runtime_attestation_sha256",
    }
    required_binding = {
        "schema_version": 1,
        "scope": "family_generation_replacement_source_binding",
        **revision,
        "candidate_sha256": candidate,
        "receipt_sha256": packet["independent_review"]["sha256"],
        "runtime_attestation_sha256": packet["runtime_attestation"]["sha256"],
    }
    if (
        set(binding) != {*required_binding, *hashes}
        or type(binding.get("schema_version")) is not int
        or any(binding.get(key) != value for key, value in required_binding.items())
        or any(not valid_sha256(binding.get(key)) for key in hashes)
    ):
        raise ValueError("Conductor source binding differs from the reviewed revision or candidate")
    effective = runtime.get("effective_runtime")
    expected_runtime = {
        "schema_version": 1,
        "scope": "family_generation_review_runtime",
        "repository_root": str(root),
        "session_id": session,
        "receipt_sha256": packet["independent_review"]["sha256"],
        "evidence": {"source": "session_metadata"},
    }
    if (
        set(runtime) != {*expected_runtime, "effective_runtime"}
        or type(runtime.get("schema_version")) is not int
        or any(runtime.get(key) != value for key, value in expected_runtime.items())
        or not isinstance(effective, dict)
        or set(effective) != {"timestamp", "model", "effort", "model_provider", "approval_policy", "sandbox_type"}
        or any(
            effective.get(key) != value
            for key, value in {
                "model": "gpt-6.1-sol",
                "effort": "high",
                "model_provider": "openai",
                "approval_policy": "never",
                "sandbox_type": "read-only",
            }.items()
        )
    ):
        raise ValueError("Runtime attestation must bind the same configured native review session and repository")
    try:
        if datetime.fromisoformat(effective["timestamp"].replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Native review runtime timestamp is invalid") from None
    if version == 3:
        validate_diagnostic_user_authorization(linked["user_authorization"])
        if packet["user_authorization"]["sha256"] != DIAGNOSTIC_USER_AUTHORIZATION_SHA256:
            raise ValueError("The diagnostic user authorization reference changed")
        compatibility_location = Path(packet["compatibility_binding"]["path"])
        if not compatibility_location.is_absolute():
            compatibility_location = path.parent / compatibility_location
        diagnostic_source = validate_diagnostic_compatibility(
            linked["compatibility_binding"], compatibility_location.parent, fixed, history, prior, binding
        )
    if version == 2:
        compatibility_location = Path(packet["compatibility_binding"]["path"])
        if not compatibility_location.is_absolute():
            compatibility_location = path.parent / compatibility_location
        validate_compatibility(
            linked["compatibility_binding"], compatibility_location.parent, fixed, history, prior, binding
        )
    if verify_current_revision:
        verify_reviewed_revision(root, revision)
    result = {
        "packet_sha256": expected,
        ordinal_key: failed["ordinal"],
        "candidate_sha256": candidate,
        "diagnosis_sha256": packet["diagnosis"]["sha256"],
        "correction_sha256": packet["correction"]["sha256"],
        "review_sha256": packet["independent_review"]["sha256"],
        "source_binding_sha256": packet["source_binding"]["sha256"],
        "runtime_attestation_sha256": packet["runtime_attestation"]["sha256"],
        **{
            key: binding[key]
            for key in ("executable_sha256", "recipe_sha256", "parser_sha256", "implementation_source_sha256")
        },
        "review_runtime_model_verified": False,
        "provenance": "conductor_owned_source_binding_and_session_metadata_not_remote_attestation",
    }
    if version in {2, 3}:
        result.update(
            schema_version=version,
            replacement_basis=fixed["replacement_basis"],
            compatibility_binding_sha256=packet["compatibility_binding"]["sha256"],
            review_packet={"path": str(path.expanduser().absolute()), "sha256": expected},
        )
    if version == 3:
        result.update(
            diagnostic_purpose=DIAGNOSTIC_PURPOSE,
            diagnostic_limits=dict(DIAGNOSTIC_LIMITS),
            prior_ledger_sha256=fixed["prior_ledger_sha256"],
            diagnostic_sidecar=fixed["diagnostic_sidecar"],
            user_authorization_sha256=DIAGNOSTIC_USER_AUTHORIZATION_SHA256,
            source_revision=revision,
            diagnostic_source_sha256=diagnostic_source,
        )
    return result


def verify_reviewed_revision(root: Path, revision: dict[str, str]) -> None:
    if root.resolve(strict=True) != root or not (root / "clink/native_generation.py").is_file():
        raise ValueError("Review repository must be the canonical Clink source repository")
    environment = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

    def git(*arguments: str) -> bytes:
        code, output, _ = native.probes.bounded_command(
            ["git", *arguments], environment, cwd=root, max_bytes=16 * 1024 * 1024, timeout_seconds=10
        )
        if code != 0:
            raise ValueError("Reviewed source revision could not be verified")
        return output

    if (
        git("rev-parse", "--show-toplevel").decode().strip() != str(root)
        or git("rev-parse", "HEAD").decode().strip() != revision["head_commit"]
        or digest(
            git(
                "diff",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                revision["base_commit"],
                revision["head_commit"],
                "--",
            )
        )
        != revision["diff_sha256"]
        or git("status", "--porcelain", "--untracked-files=no").strip()
    ):
        raise ValueError("Actual Clink HEAD, tracked source state or diff changed after independent review")


def submission_authorization(
    ledger: list,
    check: str,
    prior: str | None,
    review: Path | None,
    review_sha256: str | None,
    family: str,
    review_repo: Path | None = None,
    *,
    history: list[dict] | None = None,
    diagnostic_sidecar: Path | None = None,
) -> dict | None:
    validate_ledger(ledger, diagnostic=family == "agy" and len(ledger) == 6)
    if len(ledger) == 5 and review is not None:
        packet, _ = regular_json(review)
        if type(packet.get("schema_version")) is int and packet["schema_version"] == 3:
            if family != "agy" or check != "cancel":
                raise ValueError("The one-shot diagnostic authorizes only Agy cancellation")
            return replacement_proof(
                review,
                review_sha256,
                prior,
                ledger[-1],
                family,
                review_repo,
                history=history,
                expected_ledger=ledger,
                diagnostic_sidecar=diagnostic_sidecar,
            )
    if len(ledger) == 6:
        raise ValueError("The one-shot sixth diagnostic is already consumed")
    if len(ledger) >= MAX_SUBMISSIONS:
        raise ValueError("The five-submission family budget is exhausted")
    if ledger and not ledger[-1]["passed"]:
        failed = ledger[-1]
        if failed["check"] != check or any(item["replacement"] for item in ledger):
            raise ValueError("The failed or interrupted gate stops this family until its diagnosed replacement")
        return replacement_proof(
            review, review_sha256, prior, failed, family, review_repo, history=history, expected_ledger=ledger
        )
    if any(item["check"] == check for item in ledger):
        if family == "grok" and invalidated_cleanup_transition(ledger, check) and review is not None:
            packet, _ = regular_json(review)
            if (
                type(packet.get("schema_version")) is int
                and packet["schema_version"] == 2
                and packet.get("replacement_basis") == "invalidated_cleanup_evidence"
            ):
                return replacement_proof(
                    review,
                    review_sha256,
                    prior,
                    ledger[-1],
                    family,
                    review_repo,
                    history=history,
                    expected_ledger=ledger,
                )
        raise ValueError("A passed gate cannot be resubmitted")
    if review is not None or review_sha256 is not None:
        raise ValueError("Replacement review is valid only for the immediately failed gate")
    return None


def implementation_digest() -> str:
    manifest = native.implementation_manifest()
    for name in (
        "verify_family_generation",
        "verify_native_adapters",
        "verify_native_cli",
        "native_agy_fixture",
        "native_codex_fixture",
        "native_codex_trust",
        "native_denial_diagnostics",
        "native_diagnostics",
        "native_fixture_trust",
        "native_mcp_fixture",
    ):
        path = Path(__file__).parent / (name + ".py")
        manifest["files"].append({"path": "scripts/" + path.name, "sha256": digest(regular_bytes(path))})
    return digest(canonical_json(manifest))


def installed_binding(family: str) -> tuple[str, dict[str, Any]]:
    _, _, policy = generation_modules()
    executable = fixtures.shutil.which(family)
    if executable is None:
        raise ValueError("Selected native family executable is unavailable")
    executable = str(Path(executable).resolve(strict=True))
    identity = fixtures.preflight(executable, family)
    return executable, {
        "observed_version": identity["observed_version"],
        "executable_sha256": identity["executable_sha256"],
        "recipe_sha256": policy.family_generation_recipe_digest(family),
        "parser_sha256": policy.family_generation_parser_digest(family),
    }


def candidate_digest(record: dict[str, Any]) -> str:
    fields = {
        key: record[key]
        for key in (
            "family",
            "observed_version",
            "executable_sha256",
            "recipe_sha256",
            "parser_sha256",
            "implementation_source_sha256",
        )
    }
    if not all(valid_sha256(value) for key, value in fields.items() if key.endswith("sha256")):
        raise ValueError("Candidate identity must bind the binary, recipe, parser and implementation")
    return digest(canonical_json(fields))


class FixtureRegistry:
    project_dir = None

    def __init__(self, family: str, executable: str, workspace: Path):
        self.client = ResolvedCLIClient(
            name=family,
            adapter=family,
            executable=(executable,),
            working_dir=workspace,
            allowed_roots=(workspace,),
            allowed_roots_explicit=True,
            configuration_scope="explicit",
            timeout_seconds=180,
            parser=family,
            roles={},
        )

    def get_client(self, name: str) -> ResolvedCLIClient:
        if name != self.client.name:
            raise KeyError("Only this independently configured fixture family is available")
        return self.client


# These locations describe only the retained ab01466 runner. A different source is unknown.
PINNED_DIAGNOSTIC_RUNNER_SHA256 = "7fe67698f57ec1539ad3751f25dd0300166361d5d0b9159946836937f76a7632"
DIAGNOSTIC_BASELINE_COMMIT = "ab01466c84ba3661e8807b54d26d53b78c94e3bf"
_CLEANUP_OPERATIONS = {127: "term_group", 132: "probe_group", 136: "kill_group", 137: "wait_leader"}
_DIAGNOSTIC_PHASES = {"production_cleanup", "process_group_probe", "pool_execute", "unknown"}
_IDENTITY_FIELDS = {"pid", "pgid", "sid", "uid", "start_seconds", "start_microseconds"}


def diagnostic_sidecar_destination(path: Path) -> Path:
    """Keep optional diagnostic output outside every authoritative evidence tree."""
    try:
        destination = path.expanduser().resolve(strict=False)
        protected_roots = (LINEAGE_ROOT.resolve(), (PROJECT / "docs/verification").resolve())
    except (OSError, RuntimeError):
        raise ValueError("Cleanup diagnostics require a resolvable destination") from None
    if any(destination == root or root in destination.parents for root in protected_roots):
        raise ValueError("Cleanup diagnostic sidecars cannot use authoritative evidence destinations")
    return destination


def cleanup_exception_diagnostic(error: BaseException, phase: str, runner_sha256: str | None) -> dict[str, Any]:
    """Retain the existing safe diagnostic and at most one trusted cleanup caller."""
    diagnostic = exception_diagnostic(error)
    if not valid_exception_diagnostic(diagnostic):
        raise ValueError("Invalid bounded exception diagnostic")
    caller = None
    operation = "unknown"
    location = diagnostic.get("exception_location")
    if runner_sha256 == PINNED_DIAGNOSTIC_RUNNER_SHA256 and phase == "production_cleanup":
        traceback = error.__traceback__
        for _ in range(128):
            if traceback is None:
                break
            code = traceback.tb_frame.f_code
            line = traceback.tb_lineno
            if (
                code.co_filename == str(PROJECT / "clink/runner.py")
                and code.co_name == "_cleanup"
                and line in _CLEANUP_OPERATIONS
            ):
                candidate = {"file": "clink/runner.py", "function": "_cleanup", "line": line}
                direct = location == candidate and line in {132, 137}
                shared_signal = location == {"file": "clink/runner.py", "function": "_signal_group", "line": 121}
                if direct or (shared_signal and line in {127, 136}):
                    caller, operation = candidate, _CLEANUP_OPERATIONS[line]
            traceback = traceback.tb_next
    return {
        "exception_diagnostic": diagnostic,
        "execution_phase": phase if phase in _DIAGNOSTIC_PHASES else "unknown",
        "cleanup_operation": operation,
        "cleanup_caller": caller,
    }


def read_diagnostic_identity(pid: int) -> dict[str, int] | None:
    """Read only one Darwin kernel record and SID, without argv or a subprocess."""
    if sys.platform != "darwin" or type(pid) is not int or not 1 < pid < 2**31:
        return None
    # proc_bsdinfo is 136 bytes in the Darwin SDK: numeric fields surround 48 unused name bytes.
    # No name, argv, environment or process listing is exposed or retained.
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    info = library.proc_pidinfo
    info.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    info.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(136)
    if info(pid, 3, 0, buffer, len(buffer)) != len(buffer):
        return None
    data = buffer.raw
    return {
        "pid": struct.unpack_from("=I", data, 12)[0],
        "pgid": struct.unpack_from("=I", data, 100)[0],
        "sid": os.getsid(pid),
        "uid": struct.unpack_from("=I", data, 20)[0],
        "start_seconds": struct.unpack_from("=Q", data, 120)[0],
        "start_microseconds": struct.unpack_from("=Q", data, 128)[0],
    }


def valid_diagnostic_identity(value: object) -> bool:
    """Reject unknown fields and nonnumeric or out-of-bound kernel identities."""
    return (
        isinstance(value, dict)
        and set(value) == _IDENTITY_FIELDS
        and all(type(value[key]) is int for key in _IDENTITY_FIELDS)
        and all(1 < value[key] < 2**31 for key in ("pid", "pgid", "sid"))
        and 0 <= value["uid"] < 2**32
        and 0 < value["start_seconds"] < 2**63
        and 0 <= value["start_microseconds"] < 10**6
    )


class CleanupDiagnostics:
    """Bound optional observations separately from receipts and cleanup authority."""

    def __init__(self, run_id: str, executable_sha256: str, *, identity_reader=read_diagnostic_identity):
        if str(uuid.UUID(run_id)) != run_id or not valid_sha256(executable_sha256):
            raise ValueError("Diagnostics require a canonical run UUID and existing executable digest")
        self.run_id, self.executable_sha256 = run_id, executable_sha256
        self._identity_reader = identity_reader
        self.capture_failed = False
        self.exception: dict[str, Any] | None = None
        self._exception_capture_failed = False
        self.processes: dict[str, list[dict[str, Any]]] = {"leader": [], "fixture_child": []}
        self._handles: dict[str, Any] = {}
        self._pids: dict[str, int] = {}
        self.source: dict[str, str | None] = {"baseline_commit": DIAGNOSTIC_BASELINE_COMMIT}
        for key, name in (
            ("runner_sha256", "clink/runner.py"),
            ("verifier_sha256", "scripts/verify_family_generation.py"),
            ("native_diagnostics_sha256", "scripts/native_diagnostics.py"),
        ):
            try:
                self.source[key] = digest(regular_bytes(PROJECT / name))
            except (OSError, ValueError):
                self.source[key] = None
                self.capture_failed = True

    def _capture(self, role: str, moment: str) -> None:
        if any(item["moment"] == moment for item in self.processes[role]):
            return
        record: dict[str, Any] = {"moment": moment, "state": "unknown", "identity": None, "fingerprint_sha256": None}
        self.processes[role].append(record)
        pid = self._pids.get(role)
        if pid is None:
            return
        try:
            first, second = self._identity_reader(pid), self._identity_reader(pid)
            if not valid_diagnostic_identity(first) or not valid_diagnostic_identity(second):
                return
            if first != second or first["pid"] != pid:
                record["state"] = "raced"
                return
            initial = self.processes[role][0]
            if moment == "cleanup_entry" and initial["identity"] != first:
                record["state"] = "reused" if initial["identity"] is not None else "unknown"
                return
            leader = self.processes["leader"][0]["identity"] if self.processes["leader"] else None
            if role == "fixture_child" and (
                leader is None or any(first[key] != leader[key] for key in ("pgid", "sid", "uid"))
            ):
                return
            record.update(state="observed", identity=dict(first), fingerprint_sha256=digest(canonical_json(first)))
        except BaseException:
            # Capture failure is explicit metadata; it cannot mask cancellation or cleanup failure.
            self.capture_failed = True

    def observe_leader(self, process: Any, owned_children: set[Any]) -> None:
        """Accept only the retained handle already owned by this single-dispatch pool."""
        if self.processes["leader"]:
            return
        if len(owned_children) == 1 and any(child is process for child in owned_children):
            self._handles["leader"] = process
            if type(process.pid) is int and 1 < process.pid < 2**31:
                self._pids["leader"] = process.pid
        self._capture("leader", "initial")

    def observe_fixture_child(self, pid: int) -> None:
        """Observe only the designated synthetic fixture PID in the already owned session.

        The caller obtains this from its pinned fixture; this supplies no signaling authority.
        A bare PID without the observed owned leader never triggers a metadata read.
        """
        if self.processes["fixture_child"]:
            return
        leader = self.processes["leader"][0]["identity"] if self.processes["leader"] else None
        if leader is not None and type(pid) is int and 1 < pid < 2**31 and pid != leader["pid"]:
            self._pids["fixture_child"] = pid
        self._capture("fixture_child", "initial")

    def cleanup_entry(self, process: Any) -> None:
        if self._handles.get("leader") is not process:
            return
        for role in ("leader", "fixture_child"):
            if not self.processes[role]:
                self._capture(role, "initial")
            self._capture(role, "cleanup_entry")

    def observe_exception(self, error: BaseException, phase: str) -> None:
        if self.exception is not None or self._exception_capture_failed:
            return
        try:
            # Recheck after an exception, outside the production signaling path. Drift is unknown.
            try:
                current = digest(regular_bytes(PROJECT / "clink/runner.py"))
            except (OSError, ValueError):
                current = None
                self.capture_failed = True
            source = self.source["runner_sha256"] if current == self.source["runner_sha256"] else None
            if source is None:
                self.capture_failed = True
            self.exception = cleanup_exception_diagnostic(error, phase, source)
        except BaseException:
            self.capture_failed = self._exception_capture_failed = True

    def sidecar(self, result: dict[str, Any]) -> dict[str, Any]:
        """Bind metadata to exact source, run and result without retaining result contents."""
        value = {
            "schema_version": 1,
            "kind": "family_cleanup_diagnostics",
            "run_id": self.run_id,
            "source": dict(self.source),
            "executable_sha256": self.executable_sha256,
            "result_sha256": digest(canonical_json(result)),
            "capture_failed": self.capture_failed,
            "exception": self.exception,
            "processes": self.processes,
        }
        if not valid_cleanup_diagnostic_sidecar(value):
            raise ValueError("Invalid bounded cleanup diagnostic sidecar")
        return json.loads(canonical_json(value))

    def persist_sidecar(self, path: Path, result: dict[str, Any]) -> str:
        """Write a new immutable sidecar only when explicitly requested after execution."""
        return persist(diagnostic_sidecar_destination(path), self.sidecar(result))


def valid_cleanup_diagnostic_sidecar(value: object) -> bool:
    """Validate the closed version-one schema; historical receipt schemas are untouched."""
    fields = {
        "schema_version",
        "kind",
        "run_id",
        "source",
        "executable_sha256",
        "result_sha256",
        "capture_failed",
        "exception",
        "processes",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        return False
    if value["kind"] != "family_cleanup_diagnostics" or type(value["capture_failed"]) is not bool:
        return False
    try:
        if not isinstance(value["run_id"], str) or str(uuid.UUID(value["run_id"])) != value["run_id"]:
            return False
    except ValueError:
        return False
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {
        "baseline_commit",
        "runner_sha256",
        "verifier_sha256",
        "native_diagnostics_sha256",
    }:
        return False
    if source["baseline_commit"] != DIAGNOSTIC_BASELINE_COMMIT or any(
        source[key] is not None and not valid_sha256(source[key]) for key in source if key != "baseline_commit"
    ):
        return False
    if not all(valid_sha256(value[key]) for key in ("executable_sha256", "result_sha256")):
        return False
    exception = value["exception"]
    if exception is not None:
        if not isinstance(exception, dict) or set(exception) != {
            "exception_diagnostic",
            "execution_phase",
            "cleanup_operation",
            "cleanup_caller",
        }:
            return False
        if (
            not valid_exception_diagnostic(exception["exception_diagnostic"])
            or not isinstance(exception["execution_phase"], str)
            or exception["execution_phase"] not in _DIAGNOSTIC_PHASES
            or not isinstance(exception["cleanup_operation"], str)
            or exception["cleanup_operation"] not in {*_CLEANUP_OPERATIONS.values(), "unknown"}
        ):
            return False
        caller = exception["cleanup_caller"]
        if caller is not None:
            if (
                not isinstance(caller, dict)
                or set(caller) != {"file", "function", "line"}
                or caller["file"] != "clink/runner.py"
                or caller["function"] != "_cleanup"
                or type(caller["line"]) is not int
                or caller["line"] not in _CLEANUP_OPERATIONS
            ):
                return False
        if exception["cleanup_operation"] == "unknown":
            if caller is not None:
                return False
        else:
            if (
                caller is None
                or source["runner_sha256"] != PINNED_DIAGNOSTIC_RUNNER_SHA256
                or exception["execution_phase"] != "production_cleanup"
                or _CLEANUP_OPERATIONS[caller["line"]] != exception["cleanup_operation"]
            ):
                return False
            location = exception["exception_diagnostic"].get("exception_location")
            expected = (
                {"file": "clink/runner.py", "function": "_signal_group", "line": 121}
                if caller["line"] in {127, 136}
                else caller
            )
            if location != expected:
                return False
    processes = value["processes"]
    if not isinstance(processes, dict) or set(processes) != {"leader", "fixture_child"}:
        return False
    for snapshots in processes.values():
        if not isinstance(snapshots, list) or len(snapshots) > 2:
            return False
        for index, snapshot in enumerate(snapshots):
            if (
                not isinstance(snapshot, dict)
                or set(snapshot) != {"moment", "state", "identity", "fingerprint_sha256"}
                or snapshot["moment"] != ("initial", "cleanup_entry")[index]
            ):
                return False
            if not isinstance(snapshot["state"], str) or snapshot["state"] not in {
                "observed",
                "unknown",
                "raced",
                "reused",
            }:
                return False
            identity = snapshot["identity"]
            if snapshot["state"] == "observed":
                if not valid_diagnostic_identity(identity) or snapshot["fingerprint_sha256"] != digest(
                    canonical_json(identity)
                ):
                    return False
            elif identity is not None or snapshot["fingerprint_sha256"] is not None:
                return False
    for snapshots in processes.values():
        if (
            len(snapshots) == 2
            and snapshots[1]["state"] == "observed"
            and snapshots[1]["identity"] != snapshots[0]["identity"]
        ):
            return False
    leader = processes["leader"][0]["identity"] if processes["leader"] else None
    for snapshot in processes["fixture_child"]:
        if snapshot["state"] == "observed":
            child = snapshot["identity"]
            if (
                leader is None
                or child["pid"] == leader["pid"]
                or any(child[key] != leader[key] for key in ("pgid", "sid", "uid"))
            ):
                return False
    return len(canonical_json(value)) <= 8192


class ObservedPool(ProcessPool):
    """Use production supervision and observe metadata from one reserved dispatch."""

    def __init__(self):
        super().__init__(maximum=1)
        self.dispatches = self.spawns = 0
        self.cleanup_confirmed = False
        self._cleanup_uncertain = False
        self.exception_observation: dict[str, str] | None = None
        self.exception_diagnostic_capture_failed = False
        self.private_input: Path | None = None
        self.private_input_modes = False

    def _observe_exception(self, error: Exception, phase: str) -> None:
        """Keep only the first allowlisted class and fixed phase without affecting propagation."""
        try:
            if self.exception_observation is None and not self.exception_diagnostic_capture_failed:
                self.exception_observation = {
                    "exception_class": exception_diagnostic(error)["exception_class"],
                    "execution_phase": phase,
                }
        except BaseException:
            # Diagnostics cannot replace the supervision exception already being propagated.
            self.exception_diagnostic_capture_failed = True

    async def execute(self, command: tuple[str, ...], **kwargs: Any) -> Any:
        try:
            if self.dispatches:
                raise RuntimeError("Only one top-level submission is allowed per verifier invocation")
            self.dispatches += 1
            if "--prompt-file" in command:
                self.private_input = Path(command[command.index("--prompt-file") + 1])
                self.private_input_modes = (
                    not self.private_input.is_symlink()
                    and self.private_input.is_file()
                    and self.private_input.stat().st_mode & 0o777 == 0o600
                    and self.private_input.parent.stat().st_mode & 0o777 == 0o700
                )
            original = kwargs.get("on_started")

            def started() -> None:
                self.spawns += 1
                self.cleanup_confirmed = False
                if original is not None:
                    original()

            return await super().execute(command, **dict(kwargs, on_started=started))
        except Exception as error:
            self._observe_exception(error, "pool_execute")
            raise

    async def _cleanup(self, process: asyncio.subprocess.Process) -> None:
        self.cleanup_confirmed = False
        confirmed = False
        phase = "production_cleanup"
        try:
            await super()._cleanup(process)
            phase = "process_group_probe"
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                confirmed = True
        except Exception as error:
            self._observe_exception(error, phase)
            raise
        finally:
            # A later callback cannot erase an earlier failed, interrupted, or negative observation.
            if not confirmed:
                self._cleanup_uncertain = True
            self.cleanup_confirmed = confirmed and not self._cleanup_uncertain


class DiagnosticObservedPool(ObservedPool):
    """Opt-in preparation only: preserve the existing observer and production cleanup."""

    def __init__(self, diagnostics: CleanupDiagnostics):
        super().__init__()
        self.diagnostics = diagnostics

    def _observe_exception(self, error: Exception, phase: str) -> None:
        super()._observe_exception(error, phase)
        try:
            self.diagnostics.observe_exception(error, phase)
        except BaseException:
            self.diagnostics.capture_failed = True

    async def execute(self, command: tuple[str, ...], **kwargs: Any) -> Any:
        original = kwargs.get("on_started")

        def started() -> None:
            try:
                if len(self._children) == 1:
                    self.diagnostics.observe_leader(next(iter(self._children)), self._children)
            except BaseException:
                self.diagnostics.capture_failed = True
            if original is not None:
                original()

        return await super().execute(command, **dict(kwargs, on_started=started))

    async def _cleanup(self, process: asyncio.subprocess.Process) -> None:
        try:
            self.diagnostics.cleanup_entry(process)
        except BaseException:
            self.diagnostics.capture_failed = True
        await super()._cleanup(process)


def candidate_service(
    family: str, executable: str, workspace: Path, binding: dict, preflight: Path, pool: ProcessPool
) -> Any:
    generation, _, _ = generation_modules()
    return generation.NativeGenerationService(
        registry=FixtureRegistry(family, executable, workspace),
        pool=pool,
        family_admissions={
            family: NativeGenerationAdmission(
                version=binding["observed_version"],
                verified=True,
                executable_sha256=binding["executable_sha256"],
                recipe_sha256=binding["recipe_sha256"],
                parser_sha256=binding["parser_sha256"],
                evidence_path=str(preflight),
                evidence_sha256=digest(regular_bytes(preflight)),
            )
        },
    )


def request_body(family: str, check: str, workspace: Path) -> dict[str, Any]:
    command = fixtures.agy_fixture_command(workspace) if family == "agy" else fixtures.fixture_command()
    instructions = {
        "text": "Improve this rough prompt. Preserve Atlas, Mira, Friday, exactly two risks, rollback and acceptance checks. Return only the improved prompt. Use no tools.",
        "action": f"Fix add in arithmetic.py to return a + b and run exactly `{command}`. Use only the assigned synthetic fixture operations. Do not change configuration or use integrations. Return a short result.",
        "permission": "Use the native write tool to create permission-needed.txt containing permission canary. This operation requires permission. Stop immediately on denial; do not retry or use alternatives.",
        "cancel": f"Run exactly `{command} cancel` as a foreground shell operation. Use no other tools or background mode. This is a synthetic cancellation test.",
    }[check]
    if family == "agy" and check in {"action", "cancel"}:
        instructions += " " + fixtures.agy_command_prompt(workspace)
        if check == "action":
            instructions += (
                " Use write_file with exactly "
                + json.dumps(
                    {
                        "Path": str(workspace / "arithmetic.py"),
                        "Overwrite": True,
                        "Content": "def add(a, b):\n    return a + b\n",
                    }
                )
                + "."
            )
    return {
        "schema_version": 3,
        "execution_mode": "native",
        "project_dir": str(workspace),
        "request_id": str(uuid.uuid4()),
        "cli": family,
        "model_selection": {"kind": "native_default"},
        "instructions": instructions,
        "input": (
            "Need plan to move Atlas cache Friday. Mira owns it. Need two risks, rollback, checks."
            if check == "text"
            else "Perform only the explicitly assigned disposable fixture check."
        ),
        "timeout_seconds": 120,
    }


def prepare_fixture(family: str, root: Path) -> tuple[Path, dict[str, Any]]:
    workspace = root / "workspace"
    config_files = fixtures.create_fixture(workspace, family)
    intact = {
        "fixture_config_files": config_files,
        "fixture_config_sha256": fixtures.fixture_digest(workspace, config_files),
    }
    return workspace.resolve(strict=True), intact


async def run_check(
    family: str,
    check: str,
    executable: str,
    root: Path,
    binding: dict,
    preflight: Path,
    timeout: int,
    prepared: tuple[Path, dict[str, Any]] | None = None,
    *,
    diagnostic_sidecar: Path | None = None,
) -> dict:
    if diagnostic_sidecar is not None:
        diagnostic_sidecar = diagnostic_sidecar_destination(diagnostic_sidecar)
    _, contracts, _ = generation_modules()
    workspace, intact = prepared if prepared is not None else prepare_fixture(family, root)
    body = request_body(family, check, workspace)
    body["timeout_seconds"] = timeout
    request = contracts.parse_native_request(canonical_json(body))
    diagnostics = None
    if diagnostic_sidecar is not None:
        if family != "agy" or check != "cancel":
            raise ValueError("Cleanup diagnostics are limited to the Agy cancellation verifier")
        try:
            diagnostics = CleanupDiagnostics(body["request_id"], binding["executable_sha256"])
        except BaseException:
            logging.getLogger(__name__).warning("cleanup_diagnostic_capture_unavailable")
    pool = ObservedPool() if diagnostics is None else DiagnosticObservedPool(diagnostics)
    service = candidate_service(family, executable, workspace, binding, preflight, pool)
    task = asyncio.create_task(service.run(request))
    child = None
    try:
        if check == "cancel":
            until = time.monotonic() + max(0, timeout - 15)
            while not task.done() and time.monotonic() < until:
                if (workspace / "mutation.txt").exists() and (workspace / "child-ready.txt").exists():
                    child = fixtures.fixture_child_identity(workspace)
                    if child is not None:
                        if diagnostics is not None:
                            try:
                                diagnostics.observe_fixture_child(child[0])
                            except BaseException:
                                diagnostics.capture_failed = True
                        break
                await asyncio.sleep(0.05)
            if not task.done():
                task.cancel()
        result = await asyncio.wait_for(asyncio.shield(task), timeout + 15)
    finally:
        if not task.done():
            task.cancel()
        await pool.shutdown()
        await asyncio.gather(task, return_exceptions=True)
    diagnostic_publication = None
    if diagnostic_sidecar is not None:
        try:
            diagnostic_publication = {
                "published": False,
                "run_id": body["request_id"],
                "result_sha256": digest(canonical_json(result)),
                "sidecar_sha256": None,
            }
        except BaseException:
            logging.getLogger(__name__).warning("cleanup_diagnostic_binding_unavailable")
    if diagnostics is not None and diagnostic_publication is not None:
        try:
            sidecar_sha256 = diagnostics.persist_sidecar(diagnostic_sidecar, result)
            diagnostic_publication.update(published=True, sidecar_sha256=sidecar_sha256)
        except BaseException:
            diagnostics.capture_failed = True
            logging.getLogger(__name__).warning("cleanup_diagnostic_sidecar_unavailable")
    execution = result.get("execution", {})
    output = result.get("output")
    status = result.get("status")
    code = (result.get("error") or {}).get("code")
    useful = (
        isinstance(output, str)
        and len(output.encode()) >= 100
        and all(word in output.lower() for word in ("atlas", "mira", "friday", "risk", "rollback", "check"))
        and re.search(r"\b(?:two|2)\b.{0,24}\brisks?\b", output.lower()) is not None
        and output != body["input"]
    )
    private_removed = pool.private_input is None or (
        not pool.private_input.exists() and not pool.private_input.parent.exists()
    )
    cleanup = pool.cleanup_confirmed and pool.running == 0 and not pool._children and private_removed
    identity = (
        execution.get("profile") == PROFILE
        and execution.get("execution_mode") == "native"
        and execution.get("admission") == "verified"
        and execution.get("project_dir") == str(workspace)
        and execution.get("permission_observability") == "limited"
        and execution.get("fulfillment_verified") is False
        and execution.get("side_effects_possible") is True
        and execution.get("catalog_sha256") is None
        and execution.get("requested_model") is None
        and all(execution.get(key) == binding[key] for key in ("executable_sha256", "recipe_sha256"))
    )
    observations = {"fixture_configuration_intact": fixtures.fixture_intact(workspace, intact)}
    completed = status == "completed" and code is None and result.get("truncated") is False
    if check == "text":
        observations["useful_transformation"] = bool(useful)
        gate = completed and useful
    elif check == "action":
        observations["arithmetic_fixed"] = (
            workspace / "arithmetic.py"
        ).read_text() == "def add(a, b):\n    return a + b\n"
        observations["independent_fixture_test_passed"] = fixtures.fixture_test(workspace)
        gate = completed and all(observations.values())
    elif check == "permission":
        observations["protected_file_untouched"] = (
            not (workspace / "permission-needed.txt").exists()
            and not (workspace / "permission-needed.txt").is_symlink()
        )
        if family == "agy":
            observations["permission_hook_observed"] = fixtures.agy_fixture_observations(workspace)[
                "permission_hook_observed"
            ]
        gate = status == "failed" and code == "permission_needed" and output is None and all(observations.values())
    else:
        observations.update(
            synthetic_child_identity_observed=child is not None,
            synthetic_child_ready=(workspace / "child-ready.txt").exists(),
            partial_effect_observed=(workspace / "mutation.txt").exists(),
            synthetic_child_absent=fixtures.descendant_status(child)[0],
        )
        gate = status == "cancelled" and output is None and all(observations.values())
    observation = {
        "check": check,
        "passed": bool(
            gate
            and identity
            and cleanup
            and pool.dispatches == pool.spawns == 1
            and all(observations.values())
            and (family != "grok" or pool.private_input_modes)
        ),
        "production_service_used": True,
        "terminal_status": safe_identifier(status),
        "error_code": safe_identifier(code),
        "actual_model": safe_identifier(execution.get("actual_model")),
        "actual_model_origin": "native_terminal_metadata" if execution.get("actual_model") is not None else "unknown",
        "observations": observations,
        "identity_bound": identity,
        "dispatch_count": pool.dispatches,
        "spawn_count": pool.spawns,
        "private_input_removed": private_removed,
        "process_groups_absent_after_cleanup": pool.cleanup_confirmed,
        "pool_exception_observation": pool.exception_observation,
        "pool_exception_diagnostic_capture_failed": pool.exception_diagnostic_capture_failed,
        "pool_running_after_shutdown": pool.running,
        "output_sha256": digest(output.encode()) if isinstance(output, str) else None,
        "output_bytes": len(output.encode()) if isinstance(output, str) else 0,
        "raw_output_retained": False,
    }
    if diagnostic_publication is not None:
        observation["diagnostic_publication"] = diagnostic_publication
    return observation


async def _verify(args: argparse.Namespace) -> dict[str, Any]:
    if args.check not in CHECKS or type(args.timeout) is not int or not 1 <= args.timeout <= 180:
        raise ValueError("Select one bounded candidate family-generation gate")
    if args.trust_grok_fixture and (args.family != "grok" or not args.live):
        raise ValueError("Fixture trust requires explicit live Grok verification")
    diagnostic_sidecar = vars(args).get("diagnostic_sidecar")
    if diagnostic_sidecar is not None and (args.family != "agy" or args.check != "cancel" or not args.live):
        raise ValueError("Cleanup diagnostics require explicit live Agy cancellation verification")
    if diagnostic_sidecar is not None:
        diagnostic_sidecar = diagnostic_sidecar_destination(diagnostic_sidecar)
    ledger, prior = load_history(args.prior_evidence, args.family)
    history = authoritative_history(args.family)
    if ledger != [item["entry"] for item in history]:
        raise ValueError("Prior evidence must retain every submitted family-generation attempt")
    if not args.live and len(ledger) > MAX_SUBMISSIONS:
        raise ValueError("Exhausted diagnostic history does not support a non-live evidence record")
    replacement = (
        submission_authorization(
            ledger,
            args.check,
            prior,
            args.replacement_review,
            args.replacement_review_sha256,
            args.family,
            args.review_repo,
            history=history,
            diagnostic_sidecar=diagnostic_sidecar,
        )
        if args.live
        else None
    )
    version = replacement.get("schema_version", 1) if replacement is not None else 1
    if version == 3 and args.timeout != 120:
        raise ValueError("The authorized diagnostic retains the reviewed 120-second timeout")
    if version == 3:
        verify_reviewed_revision(PROJECT, replacement["source_revision"])
    target = evidence_directory(family_root(args.family), args.label)
    record = {
        "schema_version": version,
        "scope": SCOPE,
        "profile": PROFILE,
        "family": args.family,
        "check": args.check,
        "live_requested": args.live,
        "live_submitted": False,
        "gate_passed": False,
        "submission_ledger": ledger,
        "submissions_consumed_total": len(ledger),
        "submissions_in_this_invocation": 0,
        "submission_allowance_total": MAX_SUBMISSIONS,
        "maximum_submissions_per_invocation": 1,
        "maximum_submissions_per_gate": 2,
        "maximum_replacements": 1,
        "prior_evidence_sha256": prior,
        "replacement_review": replacement,
        "automatic_retries": 0,
        "candidate_admission_override": "process_local_only",
        "production_admission_changed": False,
        "native_task_admission_changed": False,
        "native_trust_enrolled": False,
        "host_configuration_changed": False,
        "host_configuration_restored": True,
        "fixture_trust_requested": args.trust_grok_fixture,
        "fixture_trust": {"fixture_trust_prepared": False, "fixture_trust_removed": False},
        "raw_output_retained": False,
        "prompts_recorded": False,
        "native_sessions_recorded": False,
        "limitations": list(LIMITATIONS),
    }
    if version == 3:
        record.update(
            diagnostic_only=True,
            diagnostic_purpose=DIAGNOSTIC_PURPOSE,
            diagnostic_limits=dict(DIAGNOSTIC_LIMITS),
            diagnostic_sidecar=str(diagnostic_sidecar),
            prior_ledger_sha256=replacement["prior_ledger_sha256"],
            user_authorization_sha256=DIAGNOSTIC_USER_AUTHORIZATION_SHA256,
            diagnostic_sidecar_evidence=None,
            diagnostic_observation_complete=False,
            diagnostic_publication=None,
            submission_allowance_total=6,
            maximum_cancellation_submissions=3,
            maximum_replacements=2,
        )
    preflight = target / "preflight.json"
    controls = ExitStack()
    trust_receipt: dict[str, bool] | None = None
    entry = None
    try:
        executable, binding = installed_binding(args.family)
        record.update(binding)
        record["implementation_source_sha256"] = implementation_digest()
        record["candidate_sha256"] = candidate_digest(record)
        if replacement is not None and replacement["candidate_sha256"] != record["candidate_sha256"]:
            raise ValueError("Replacement review does not bind the exact current candidate")
        if replacement is not None and any(
            replacement[key] != record[key]
            for key in ("executable_sha256", "recipe_sha256", "parser_sha256", "implementation_source_sha256")
        ):
            raise ValueError("Replacement source binding does not match current native candidate pins")
        if args.live:
            fixture_root = PROJECT / "test_output/family-generation" / args.family / args.label
            fixture_root.mkdir(parents=True, mode=0o700)
            prepared = prepare_fixture(args.family, fixture_root)
            record["fixture_project_sha256"] = digest(str(prepared[0]).encode())
            record["fixture_config_sha256"] = prepared[1]["fixture_config_sha256"]
            if args.trust_grok_fixture:
                record.update(
                    native_trust_enrolled=None, host_configuration_changed=None, host_configuration_restored=None
                )
                record["fixture_trust"] = {"fixture_trust_prepared": None, "fixture_trust_removed": None}
                native_home = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok"))).expanduser().resolve()
                trust_receipt = controls.enter_context(fixtures.grok_fixture_trust(prepared[0], native_home))
                record["fixture_trust"] = trust_receipt
                record.update(
                    native_trust_enrolled=trust_receipt.get("fixture_trust_prepared") is True,
                    host_configuration_changed=trust_receipt.get("fixture_trust_prepared") is True,
                    host_configuration_restored=False,
                )
                if trust_receipt.get("fixture_trust_prepared") is not True:
                    raise ValueError("Explicit fixture trust preparation was not confirmed")
            persist(preflight, record)
            reservation = {
                "schema_version": record["schema_version"],
                "scope": SCOPE,
                "profile": PROFILE,
                "family": args.family,
                "check": args.check,
                "ordinal": len(ledger) + 1,
                "replacement": replacement is not None,
                "state": "consumed_before_dispatch",
                "preflight_sha256": digest(regular_bytes(preflight)),
                **({"replacement_basis": replacement["replacement_basis"]} if version in {2, 3} else {}),
                **(
                    {
                        "diagnostic_only": True,
                        "prior_ledger_sha256": replacement["prior_ledger_sha256"],
                        "user_authorization_sha256": DIAGNOSTIC_USER_AUTHORIZATION_SHA256,
                    }
                    if version == 3
                    else {}
                ),
            }
            reserved = persist(target / "submitted.json", reservation)
            entry = {
                "ordinal": reservation["ordinal"],
                "check": args.check,
                "passed": False,
                "replacement": replacement is not None,
                "reservation_sha256": reserved,
                **({"replacement_basis": replacement["replacement_basis"]} if version in {2, 3} else {}),
            }
            ledger.append(entry)
            record.update(live_submitted=True, submissions_consumed_total=len(ledger), submissions_in_this_invocation=1)
            observation = await run_check(
                args.family,
                args.check,
                executable,
                fixture_root,
                record,
                preflight,
                args.timeout,
                prepared,
                **({"diagnostic_sidecar": diagnostic_sidecar} if diagnostic_sidecar is not None else {}),
            )
            if observation.get("check") != args.check or type(observation.get("passed")) is not bool:
                raise ValueError("Candidate gate returned an invalid observation")
            record["observation"] = observation
            if version == 3:
                record["diagnostic_publication"] = observation.get("diagnostic_publication")
            entry["passed"] = record["gate_passed"] = observation["passed"]
    except asyncio.CancelledError:
        record["error_class"] = "CancelledError"
        raise
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        record["error_class"] = type(error).__name__
    finally:
        try:
            controls.close()
        except (OSError, RuntimeError, ValueError) as error:
            record["trust_cleanup_error_class"] = type(error).__name__
            record["gate_passed"] = False
            if entry is not None:
                entry["passed"] = False
            if "observation" in record:
                record["observation"]["passed"] = False
        if trust_receipt is not None:
            record["fixture_trust"] = dict(trust_receipt)
            record["host_configuration_restored"] = trust_receipt.get("fixture_trust_removed") is True
            if record["host_configuration_restored"] is not True:
                record["gate_passed"] = False
                if entry is not None:
                    entry["passed"] = False
                if "observation" in record:
                    record["observation"]["passed"] = False
        if not preflight.exists():
            persist(preflight, record)
        if version == 3:
            try:
                location = diagnostic_sidecar_destination(Path(record["diagnostic_sidecar"]))
                reference = {"path": str(location), "sha256": digest(regular_bytes(location))}
                complete = diagnostic_sidecar_evidence(record, reference)
                record["diagnostic_sidecar_evidence"] = reference
                record["diagnostic_observation_complete"] = complete
            except (OSError, ValueError):
                # The reservation stays consumed, with the actual gate result unchanged.
                record["diagnostic_sidecar_evidence"] = None
                record["diagnostic_observation_complete"] = False
        passed = {
            item["entry"]["check"]
            for item in history
            if item["entry"]["passed"] and item["preflight"].get("candidate_sha256") == record.get("candidate_sha256")
        }
        if record["gate_passed"]:
            passed.add(args.check)
        record["all_family_generation_gates_passed"] = set(CHECKS) <= passed
        persist(target / "receipt.json", record)
    return record


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    refuse_live_verification(args.live, PROJECT)
    directory = family_root(args.family)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.resolve(strict=True) != directory:
        raise ValueError("Candidate family evidence root must be canonical")
    descriptor = os.open(directory / ".verification.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another verifier owns this candidate family's lineage") from None
        return await _verify(args)
    finally:
        os.close(descriptor)


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=FAMILIES, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--check", choices=CHECKS, default="text")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Unavailable in public snapshot; continue only with the original private verifier and history",
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--diagnostic-sidecar",
        type=Path,
        help="Default-off bounded Agy cancellation metadata sidecar; does not alter admission or call budgets",
    )
    parser.add_argument(
        "--trust-grok-fixture",
        action="store_true",
        help="Temporarily trust only this owned disposable Grok fixture, then remove its unchanged grant",
    )
    parser.add_argument("--prior-evidence", type=Path)
    parser.add_argument("--replacement-review", type=Path)
    parser.add_argument("--replacement-review-sha256")
    parser.add_argument(
        "--review-repo",
        type=Path,
        help="Explicit canonical Clink source repository covered by the standalone native review",
    )
    return parser


async def supervised_verify(args: argparse.Namespace) -> dict[str, Any]:
    task = asyncio.create_task(verify(args))
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, task.cancel)
    try:
        return await task
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main() -> int:
    parser = argument_parser()
    args = parser.parse_args()
    if args.live:
        parser.error(LIVE_HISTORY_REFUSAL)
    try:
        record = asyncio.run(supervised_verify(args))
    except asyncio.CancelledError:
        return 130
    except (OSError, ValueError) as error:
        parser.error("Candidate family verification refused: " + type(error).__name__)
    print(
        json.dumps(
            {
                "family": args.family,
                "check": args.check,
                "live_submitted": record["live_submitted"],
                "gate_passed": record["gate_passed"],
                "submissions_consumed_total": record["submissions_consumed_total"],
                "receipt": str(family_root(args.family) / args.label / "receipt.json"),
            }
        )
    )
    return 0 if record["gate_passed"] or (not args.live and "error_class" not in record) else 2


if __name__ == "__main__":
    raise SystemExit(main())
