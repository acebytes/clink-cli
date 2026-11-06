"""Verify the separate native generation profile with one explicit live submission.

The public snapshot refuses live submissions to preserve archived call budgets.
Use the original private verifier and complete history for live continuation.

Native tools and headless permissions remain native-owned. The three gates prove
a bounded final response, a scoped permitted read, and controlled cancellation;
they do not prove fulfillment or complete permission-denial observability. No
native configuration, permission, sandbox, or trust overrides are supplied.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import signal
import stat
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from clink.generation_contracts import canonical_json  # noqa: E402
from clink.models import ResolvedCLIClient  # noqa: E402
from clink.policy import executable_digest  # noqa: E402
from clink.runner import ProcessPool  # noqa: E402
from scripts import verify_cli_policy as probes  # noqa: E402
from scripts.private_evidence import LIVE_HISTORY_REFUSAL, refuse_live_verification  # noqa: E402
from scripts.verify_generation_policy import (  # noqa: E402
    digest,
    evidence_directory,
    persist,
    read_json,
    safe_identifier,
    unique_object,
)

PROFILE = "generation_native_v1"
SCOPE = "candidate_native_generation"
VERSION = "codex-cli 0.166.0"
RESEARCHED_EXECUTABLE_SHA256 = "39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1"
SOURCE_COMMIT = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
LOGIN_SOURCE_SHA256 = "7c887f1f55ea9e9514afd85ce19307c670b575a1edb323d7e805acd96cb524be"
AUDIT_ROOT = PROJECT / "test_output/generation-native-source-audit-1"
LOGIN_SOURCE = AUDIT_ROOT / "source/codex-rs/cli/src/login.rs"
OWNERSHIP_APPENDIX = AUDIT_ROOT / "process-ownership-appendix1.json"
LOGIN_APPENDIX = AUDIT_ROOT / "login-status-appendix2.json"
OWNERSHIP_APPENDIX_SHA256 = "c4c99cd80637b1c95c693c0e5f1fad191a09836806c894c231911e1876aca33f"
LOGIN_APPENDIX_SHA256 = "454504f1b838b13f99677e2c5bf5c2a13040af0a1bc58d157b1f9871f09ab599"
CHECKS = ("text", "tool", "cancel")
MAX_SUBMISSIONS = 4
# Historical replacement authority is private and is never inferred from this checkout.
# The archived original verifier retains its reviewed local journal binding.
TRUSTED_FLOW_ROOT: Path | None = None
REVIEW_TASK_SHA256 = "40fc0bb29d7a9f6d5e48ab62cef3eec99906c6847f57d473f54796356c1cbbc7"
APPROVAL_MARKER = "NATIVE_GENERATION_REPLACEMENT_APPROVED_SHA256="
REVIEW_CORE = {
    "type": "impl_review",
    "mode": "codex",
    "verdict": "SHIP",
    "model": "gpt-6.1-sol",
    "effort": "high",
    "spec": "codex:gpt-6.1-sol:high",
}
CANDIDATE_FIELDS = (
    "executable_sha256",
    "recipe_sha256",
    "parser_sha256",
    "final_file_sha256",
    "verifier_sha256",
    "source_manifest_sha256",
    "ownership_appendix_sha256",
    "login_appendix_sha256",
    "implementation_source_sha256",
)
LINEAGE_ROOT = PROJECT / "docs/verification/native-generation"
SOURCE_BINDINGS = {
    "codex-rs/exec/src/cli.rs": "66643e8b3d5964b70cb8f231fb309312376272fb3ea87bcb6849f44cf112f8f6",
    "codex-rs/exec/src/lib.rs": "57a61d50995a365f7572d7eaa05de88054680ff339642b8a823a5c9198e4e012",
    "codex-rs/config/src/loader/mod.rs": "c3aa56395e7e5a2b75e17c670fbfda51a0040f78a95aeb288700dd555e46ed20",
    "codex-rs/core/src/config/mod.rs": "e027e9fb2e4cd101abdb3984768eef07d1d0ac7c8aa631989011cd17bda0c6df",
    "codex-rs/exec/src/event_processor.rs": "b23e5cff350f621247ca2d7e21d2458ddd515074becf8f7de599aa495459b2b4",
    "codex-rs/exec/src/event_processor_with_jsonl_output.rs": "41b9532fe9d480b3d427e487f3f3f940f96450e36e7d98ec8509615c9d563367",
    "codex-rs/exec/src/exec_events.rs": "dafa872d7e86a099e56e28a329dcb9c03db90ed768c3b88cca8c91d46dc1d0e5",
}
HISTORICAL = (
    (
        "docs/verification/generation-refusal-1/receipt.json",
        "1db740d2626127630a479f776904e981a2505d40a3b7ffcdf761a94ed0b623f1",
    ),
    (
        "docs/verification/native-codex-0.166.0-attempt5.json",
        "81b16be0a95ad0baff9eaca1d1d0e3c1160e82f2383af213ffb77bdbff3f84e8",
    ),
)
LIMITATIONS = [
    "Native configured tools, hooks, integrations, subagents and provider-internal retries can perform additional work.",
    "Completed means a validated final response, not that every tool succeeded or fulfillment was verified.",
    "Native exec omits some prelaunch refusal telemetry; permission observability remains limited.",
    "The final file selects the last agent message with native Plan fallback; streamed messages may be incomplete.",
    "Cancellation does not undo effects or prove detached or remote work stopped.",
    "Candidate admission applies only inside this verifier and does not admit production or native tasks.",
    "This lineage requires ChatGPT native login status; readiness does not guarantee provider billing or entitlement.",
]


def modules() -> tuple[Any, Any, Any]:
    from clink import native_generation, native_generation_contracts, native_generation_policy

    return native_generation, native_generation_contracts, native_generation_policy


def source_proof(path: Path, expected: str) -> dict[str, Any]:
    if digest(path.read_bytes()) != expected:
        raise ValueError("Source manifest differs from its explicit pin")
    manifest = read_json(path)
    sources = manifest.get("sources")
    if (
        manifest.get("codex_source_commit") != SOURCE_COMMIT
        or not isinstance(sources, list)
        or len(sources) != len(SOURCE_BINDINGS)
    ):
        raise ValueError("Source manifest does not bind the pinned native-generation source")
    observed = {}
    for source in sources:
        if not isinstance(source, dict) or source.get("path") not in SOURCE_BINDINGS:
            raise ValueError("Source manifest contains an unexpected source")
        name = source["path"]
        if name in observed or source.get("sha256") != SOURCE_BINDINGS[name]:
            raise ValueError("Source manifest changes or repeats a pinned source")
        local = Path(source["local_path"])
        if local.is_symlink() or not local.is_file():
            raise ValueError("Pinned source must be a regular file")
        with local.open("rb") as stream:
            content = stream.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024 or digest(content) != SOURCE_BINDINGS[name]:
            raise ValueError("Pinned native source bytes changed")
        observed[name] = SOURCE_BINDINGS[name]
    return {
        "verified": True,
        "manifest_sha256": expected,
        "source_commit": SOURCE_COMMIT,
        "source_files": observed,
        "model_requests_submitted": 0,
    }


def historical_lineages() -> list[dict[str, Any]]:
    references = []
    for name, expected in HISTORICAL:
        path = PROJECT / name
        if digest(path.read_bytes()) != expected:
            raise ValueError("Required historical evidence changed")
        value = read_json(path)
        references.append(
            {
                "path": name,
                "sha256": expected,
                "scope": value.get("scope"),
                "profile": value.get("profile", "native_task_v1"),
                "submissions_consumed_total": value["submissions_consumed_total"],
                "submission_allowance_total": value.get("submission_allowance_total"),
                "admission_reused": False,
            }
        )
    return references


def appendix_proof(path: Path, expected: str) -> dict[str, Any]:
    if digest(path.read_bytes()) != expected:
        raise ValueError("Native source appendix differs from its pin")
    packet = read_json(path)
    if packet.get("codex_source_commit") != SOURCE_COMMIT or not isinstance(packet.get("sources"), list):
        raise ValueError("Native source appendix has an unsupported shape")
    for source in packet["sources"]:
        local = Path(source["local_path"])
        if local.is_symlink() or not local.is_file():
            raise ValueError("Native source appendix must retain regular source files")
        with local.open("rb") as stream:
            content = stream.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024 or digest(content) != source["sha256"]:
            raise ValueError("Native source appendix bytes changed")
    return {"verified": True, "sha256": expected, "source_file_count": len(packet["sources"])}


def authentication_preflight(executable: str, source: Path) -> dict[str, Any]:
    """Use the pinned no-model native status interface; discard all sensitive output."""
    if source.is_symlink() or digest(source.read_bytes()) != LOGIN_SOURCE_SHA256:
        raise ValueError("Native login-status source differs from its pin")
    code, output, errors = probes.bounded_command(
        [executable, "login", "status"],
        probes.child_environment(dict(os.environ)),
        max_bytes=4096,
        timeout_seconds=15,
    )
    return {
        "source_sha256": LOGIN_SOURCE_SHA256,
        "exit_code": code,
        "native_auth_configured": code == 0,
        "chatgpt_status_reported": code == 0 and errors.strip() == b"Logged in using ChatGPT",
        "model_requests_submitted": 0,
        "raw_output_retained": False,
        "limitations": [
            "Native status does not verify model access, successful network authorization, or subscription entitlement."
        ],
    }


def lineage_path(path: Path) -> Path:
    location = path.expanduser().absolute()
    if (
        location.name not in {"preflight.json", "submitted.json", "receipt.json"}
        or location.parent.parent != LINEAGE_ROOT
        or location.resolve(strict=True) != location
    ):
        raise ValueError("Prior lineage evidence must use its original canonical durable location")
    return location


def evidence_ledger(prior: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = prior.get("submission_ledger")
    if (
        prior.get("profile") != PROFILE
        or prior.get("scope") != SCOPE
        or not isinstance(ledger, list)
        or type(prior.get("submissions_consumed_total")) is not int
        or prior["submissions_consumed_total"] != len(ledger)
    ):
        raise ValueError("Prior evidence is not this native-generation lineage")
    validate_ledger(ledger)
    return json.loads(json.dumps(ledger))


def authoritative_history() -> list[dict[str, Any]]:
    """Bind accounting to original reservations and same-directory terminal outcomes."""
    paths = sorted(LINEAGE_ROOT.glob("*/submitted.json"))
    if len(paths) > MAX_SUBMISSIONS:
        raise ValueError("Native-generation submission budget exceeded")
    history = []
    for path in paths:
        lineage_path(path)
        reservation, content = regular_json(path)
        ordinal = reservation.get("ordinal")
        if (
            type(reservation.get("schema_version")) is not int
            or reservation["schema_version"] != 1
            or reservation.get("check") not in CHECKS
            or type(reservation.get("replacement")) is not bool
            or not valid_sha256(reservation.get("preflight_sha256"))
            or type(ordinal) is not int
            or not 1 <= ordinal <= MAX_SUBMISSIONS
            or reservation.get("scope") != SCOPE
            or reservation.get("profile") != PROFILE
            or reservation.get("state") != "consumed_before_dispatch"
        ):
            raise ValueError("Prior submission reservation is invalid")
        preflight_path = lineage_path(path.parent / "preflight.json")
        preflight, preflight_bytes = regular_json(preflight_path)
        before = evidence_ledger(preflight)
        if (
            digest(preflight_bytes) != reservation["preflight_sha256"]
            or len(before) != ordinal - 1
            or preflight.get("check") != reservation["check"]
            or preflight.get("live_submitted") is not False
            or type(preflight.get("submissions_in_this_invocation")) is not int
            or preflight["submissions_in_this_invocation"] != 0
        ):
            raise ValueError("Durable reservation changed its preflight binding")
        terminal_path = path.parent / "receipt.json"
        terminal = None
        passed = False
        terminal_sha256 = None
        if terminal_path.exists() or terminal_path.is_symlink():
            lineage_path(terminal_path)
            terminal, terminal_bytes = regular_json(terminal_path)
            after = evidence_ledger(terminal)
            observation = terminal.get("observation")
            passed = terminal.get("gate_passed")
            if (
                terminal.get("check") != reservation["check"]
                or terminal.get("live_submitted") is not True
                or type(terminal.get("submissions_in_this_invocation")) is not int
                or terminal["submissions_in_this_invocation"] != 1
                or len(after) != ordinal
                or type(passed) is not bool
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
                raise ValueError("Durable terminal outcome does not bind its reservation")
            terminal_sha256 = digest(terminal_bytes)
        history.append(
            {
                "entry": {
                    "ordinal": ordinal,
                    "check": reservation["check"],
                    "replacement": reservation["replacement"],
                    "passed": passed,
                    "reservation_sha256": digest(content),
                },
                "preflight": preflight,
                "terminal": terminal,
                "preflight_sha256": digest(preflight_bytes),
                "terminal_sha256": terminal_sha256,
                "directory": path.parent,
            }
        )
    history.sort(key=lambda item: item["entry"]["ordinal"])
    entries = [item["entry"] for item in history]
    validate_ledger(entries)
    for item in history:
        bind_ledger(evidence_ledger(item["preflight"]), entries)
        if item["terminal"] is not None:
            bind_ledger(evidence_ledger(item["terminal"]), entries)
    return history


def bind_ledger(ledger: list[dict[str, Any]], authoritative: list[dict[str, Any]]) -> None:
    validate_ledger(ledger)
    if ledger != authoritative[: len(ledger)]:
        raise ValueError("Prior ledger fields or outcomes differ from authoritative durable evidence")


def load_history(path: Path | None) -> tuple[list[dict[str, Any]], str | None]:
    if path is None:
        return [], None
    path = lineage_path(path)
    if path.name not in {"preflight.json", "receipt.json"}:
        raise ValueError("Prior lineage evidence must be a preflight or terminal receipt")
    if path.name == "preflight.json" and (path.parent / "receipt.json").exists():
        raise ValueError("Use terminal evidence; preflight recovery is only for interrupted attempts")
    prior, content = regular_json(path)
    ledger = evidence_ledger(prior)
    history = authoritative_history()
    own = next((item for item in history if item["directory"] == path.parent), None)
    if own is not None:
        ordinal = own["entry"]["ordinal"]
        if ordinal == len(ledger) + 1 and path.name == "preflight.json":
            ledger.append(dict(own["entry"]))
        elif ordinal != len(ledger):
            raise ValueError("Prior evidence omits or changes a durable reservation")
    elif (
        prior.get("live_submitted") is not False
        or type(prior.get("submissions_in_this_invocation")) is not int
        or prior["submissions_in_this_invocation"] != 0
    ):
        raise ValueError("Prior terminal evidence has no corresponding durable reservation")
    bind_ledger(ledger, [item["entry"] for item in history])
    return ledger, digest(content)


def validate_ledger(ledger: list[dict[str, Any]]) -> None:
    if len(ledger) > MAX_SUBMISSIONS:
        raise ValueError("Native-generation submission budget exceeded")
    counts = dict.fromkeys(CHECKS, 0)
    replacements = 0
    latest: dict[str, dict[str, Any]] = {}
    for ordinal, item in enumerate(ledger, 1):
        if (
            not isinstance(item, dict)
            or type(item.get("ordinal")) is not int
            or item["ordinal"] != ordinal
            or item.get("check") not in CHECKS
            or type(item.get("passed")) is not bool
            or type(item.get("replacement")) is not bool
            or not isinstance(item.get("reservation_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", item["reservation_sha256"]) is None
        ):
            raise ValueError("Invalid native-generation submission ledger")
        check = item["check"]
        counts[check] += 1
        replacements += int(item["replacement"])
        if (
            counts[check] > 2
            or item["replacement"] != (counts[check] == 2)
            or (check in latest and latest[check]["passed"])
        ):
            raise ValueError("At most two independently reviewed attempts are allowed per gate")
        latest[check] = item
    if replacements > 1:
        raise ValueError("Only one replacement is allowed across the lineage")


def replacement_proof(
    path: Path | None, expected: str | None, prior: str | None, failed: dict[str, Any]
) -> dict[str, Any]:
    if path is None or not valid_sha256(expected):
        raise ValueError("A diagnosed replacement needs an explicitly pinned independent review")
    packet, packet_bytes = regular_json(path)
    if digest(packet_bytes) != expected:
        raise ValueError("A diagnosed replacement needs an explicitly pinned independent review")
    if (
        set(packet)
        != {
            "schema_version",
            "scope",
            "profile",
            "prior_receipt_sha256",
            "failed_ordinal",
            "check",
            "diagnosis",
            "reviewed_input",
            "independent_review",
            "review_run_sha256",
        }
        or not valid_sha256(prior)
        or type(packet.get("schema_version")) is not int
        or packet["schema_version"] != 1
        or packet.get("scope") != "native_generation_replacement_review"
        or packet.get("profile") != PROFILE
        or packet.get("prior_receipt_sha256") != prior
        or type(packet.get("failed_ordinal")) is not int
        or packet.get("failed_ordinal") != failed["ordinal"]
        or packet.get("check") != failed["check"]
    ):
        raise ValueError("Replacement review does not bind the failed attempt")
    linked = {}
    for key in ("diagnosis", "reviewed_input", "independent_review"):
        reference = packet[key]
        if (
            not isinstance(reference, dict)
            or set(reference) != {"path", "sha256"}
            or not isinstance(reference["path"], str)
            or not reference["path"]
            or not valid_sha256(reference["sha256"])
        ):
            raise ValueError("Replacement evidence references must contain only path and SHA-256")
        location = Path(reference["path"])
        if not location.is_absolute():
            location = path.parent / location
        content = regular_bytes(location)
        if digest(content) != reference["sha256"]:
            raise ValueError("Replacement technical evidence changed")
        linked[key] = content
    reviewed_input = evidence_json(linked["reviewed_input"])
    candidate = reviewed_input.get("candidate_sha256")
    expected_input = {
        "schema_version": 1,
        "scope": "native_generation_replacement_input",
        "profile": PROFILE,
        "prior_receipt_sha256": prior,
        "failed_ordinal": failed["ordinal"],
        "check": failed["check"],
        "diagnosis_sha256": packet["diagnosis"]["sha256"],
        "candidate_sha256": candidate,
    }
    if (
        reviewed_input != expected_input
        or type(reviewed_input.get("schema_version")) is not int
        or type(reviewed_input.get("failed_ordinal")) is not int
        or not valid_sha256(candidate)
        or linked["reviewed_input"] != canonical_json(reviewed_input)
    ):
        raise ValueError("Reviewer input does not canonically bind this failed attempt and diagnosis")
    receipt = evidence_json(linked["independent_review"])
    task_id = receipt.get("id")
    if (
        not isinstance(task_id, str)
        or digest(task_id.encode()) != REVIEW_TASK_SHA256
        or any(receipt.get(key) != value for key, value in REVIEW_CORE.items())
    ):
        raise ValueError("Replacement requires the configured genuine native implementation review")
    session = receipt.get("session_id")
    try:
        valid_session = isinstance(session, str) and str(uuid.UUID(session)) == session
    except ValueError:
        valid_session = False
    reservation = receipt.get("review_reservation_id")
    timestamp = receipt.get("timestamp")
    try:
        parsed_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if isinstance(timestamp, str) else None
        valid_time = parsed_time is not None and parsed_time.utcoffset() is not None
    except ValueError:
        valid_time = False
    text = receipt.get("review")
    if (
        not valid_session
        or not isinstance(reservation, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reservation) is None
        or not valid_time
        or not isinstance(text, str)
        or not text.strip()
    ):
        raise ValueError("Native review receipt has invalid identity, timestamp, or reviewer text")
    require_reviewer_marker(text, packet["reviewed_input"]["sha256"])
    if TRUSTED_FLOW_ROOT is None:
        raise ValueError("Historical replacement requires the archived original verifier and reviewed journal binding")
    journal_path = TRUSTED_FLOW_ROOT / ".flow/review-runs" / (reservation + ".json")
    journal, journal_bytes = regular_json(journal_path)
    if not valid_sha256(packet["review_run_sha256"]) or digest(journal_bytes) != packet["review_run_sha256"]:
        raise ValueError("Trusted native review journal differs from its pinned evidence")
    required_journal = {
        "reservation_id": reservation,
        "task_id": task_id,
        "backend": "codex",
        "review_type": "impl",
        "outcome": "verdict",
        "verdict": "SHIP",
        "reviewed_model": "gpt-6.1-sol",
        "reviewed_effort": "high",
    }
    payload = journal.get("receipt_payload")
    fields = {*REVIEW_CORE, "id", "session_id", "timestamp", "review"}
    if (
        any(journal.get(key) != value for key, value in required_journal.items())
        or not isinstance(journal.get("finalized"), dict)
        or journal["finalized"].get("receipt") != "complete"
        or not isinstance(payload, dict)
        or any(payload.get(key) != receipt[key] for key in fields)
    ):
        raise ValueError("Trusted native review publication does not match this receipt")
    return {
        "packet_sha256": expected,
        "diagnosis_sha256": packet["diagnosis"]["sha256"],
        "review_sha256": packet["independent_review"]["sha256"],
        "reviewed_input_sha256": packet["reviewed_input"]["sha256"],
        "review_run_sha256": packet["review_run_sha256"],
        "candidate_sha256": candidate,
        "provenance": "conductor_owned_flow_journal_not_remote_attestation",
        "review_runtime_model_verified": False,
        "failed_ordinal": failed["ordinal"],
    }


def valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def regular_bytes(path: Path) -> bytes:
    """Read bounded stable regular evidence without following a final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise ValueError("Replacement evidence must be a bounded regular file")
        content = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()

    def fields(value: os.stat_result) -> tuple[int, int, int, int]:
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    if (
        len(content) > 1024 * 1024
        or not stat.S_ISREG(current.st_mode)
        or fields(before) != fields(after)
        or fields(after) != fields(current)
        or len(content) != after.st_size
    ):
        raise ValueError("Replacement evidence changed while reading")
    return content


def evidence_json(content: bytes) -> dict[str, Any]:
    value = json.loads(content, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("Replacement evidence must be a JSON object")
    return value


def regular_json(path: Path) -> tuple[dict[str, Any], bytes]:
    content = regular_bytes(path)
    return evidence_json(content), content


def require_reviewer_marker(text: str, reviewed_input_sha256: str) -> None:
    lines = [line.rstrip("\r") for line in text.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    marker = APPROVAL_MARKER + reviewed_input_sha256
    if not lines or lines[-1] != marker or text.count(APPROVAL_MARKER) != 1:
        raise ValueError("Reviewer must explicitly approve this input on one terminal whole line")
    if len(lines) > 1 and lines[-2].strip():
        raise ValueError("Reviewer approval must be detached from quoted or list text by a blank line")
    fence: tuple[str, int] | None = None
    for line in lines[:-1]:
        opening = re.fullmatch(r"[ \t]{0,3}(`{3,}|~{3,})(.*)", line)
        if opening is None:
            continue
        run, rest = opening.groups()
        if fence is None:
            if run[0] != "`" or "`" not in rest:
                fence = (run[0], len(run))
        elif run[0] == fence[0] and len(run) >= fence[1] and not rest.strip():
            fence = None
    if fence is not None:
        raise ValueError("Reviewer approval cannot appear in a code fence")


def candidate_digest(record: dict[str, Any]) -> str:
    manifest = {key: record[key] for key in CANDIDATE_FIELDS}
    if not all(valid_sha256(value) for value in manifest.values()):
        raise ValueError("Candidate identity must bind every current implementation and source digest")
    return digest(canonical_json(manifest))


def require_candidate_review(proof: dict[str, Any] | None, record: dict[str, Any]) -> str:
    current = candidate_digest(record)
    if proof is not None and proof["candidate_sha256"] != current:
        raise ValueError("Replacement review does not bind the current candidate")
    return current


def implementation_manifest() -> dict[str, Any]:
    """Bind current Clink implementation and verifier helpers without exporting source."""
    package = PROJECT / "clink"
    if package.is_symlink() or not package.is_dir():
        raise ValueError("The implementation package must be a regular directory")
    files = {
        PROJECT / name
        for name in (
            "scripts/verify_native_generation.py",
            "scripts/verify_cli_policy.py",
            "scripts/verify_generation_policy.py",
        )
    }
    for directory, directories, names in os.walk(package, followlinks=False):
        parent = Path(directory)
        if any((parent / name).is_symlink() for name in directories):
            raise ValueError("Implementation source directories cannot be symlinks")
        for name in names:
            if name.endswith(".py"):
                files.add(parent / name)
                if len(files) > 256:
                    raise ValueError("Implementation source manifest exceeds its file-count bound")
    if len(files) <= 3:
        raise ValueError("Implementation source manifest must include the Clink package")
    total = 0
    manifest = []
    for path in sorted(files):
        content = regular_bytes(path)
        total += len(content)
        if total > 16 * 1024 * 1024:
            raise ValueError("Implementation source manifest exceeds its aggregate byte bound")
        manifest.append({"path": str(path.relative_to(PROJECT)), "sha256": digest(content)})
    return {"schema_version": 1, "files": manifest}


def submission_authorization(
    ledger: list[dict[str, Any]], check: str, prior: str | None, review: Path | None, review_sha256: str | None
) -> dict[str, Any] | None:
    validate_ledger(ledger)
    if len(ledger) >= MAX_SUBMISSIONS:
        raise ValueError("The four-submission lineage is exhausted")
    previous = [entry for entry in ledger if entry["check"] == check]
    if not previous:
        if review is not None:
            raise ValueError("Replacement review is valid only after a failed attempt")
        return None
    if len(previous) >= 2 or previous[-1]["passed"] or any(entry["replacement"] for entry in ledger):
        raise ValueError("A successful gate or spent replacement cannot be resubmitted")
    return replacement_proof(review, review_sha256, prior, previous[-1])


def recipe_proof() -> dict[str, Any]:
    _, _, policy = modules()
    arguments = policy.native_generation_arguments("@CLINK_NATIVE_FINAL_FILE@")
    valid = arguments == ("exec", "--json", "--color", "never", "--output-last-message", "@CLINK_NATIVE_FINAL_FILE@")
    return {
        "verified": valid,
        "permission_overrides": False,
        "configuration_overrides": False,
        "projected_catalog_injected": False,
        "native_trust_enrolled": False,
    }


class FixtureRegistry:
    """Supply independent Clink roots; never load manifests from the requested project."""

    project_dir = None

    def __init__(self, executable: str, root: Path):
        self.client = ResolvedCLIClient(
            name="codex",
            adapter="codex",
            executable=(executable,),
            working_dir=root,
            allowed_roots=(root,),
            allowed_roots_explicit=True,
            configuration_scope="explicit",
            timeout_seconds=180,
            parser="codex_native_generation",
            roles={},
        )

    def get_client(self, name: str) -> ResolvedCLIClient:
        if name != "codex":
            raise KeyError("Only the synthetic Codex client is available")
        return self.client


class ObservedPool(ProcessPool):
    """Retain production bounds/cleanup while projecting harmless lifecycle metadata."""

    def __init__(self) -> None:
        super().__init__(maximum=1)
        self.dispatches = self.spawns = 0
        self.turn_started = asyncio.Event()
        self.event_counts: dict[str, int] = {}
        self.item_counts: dict[str, int] = {}
        self.cleanup_confirmed = True
        self.private_file: Path | None = None
        self.private_directory_mode = self.private_file_mode = False

    def observe(self, data: bytes, pending: bytearray) -> None:
        pending.extend(data)
        while b"\n" in pending:
            line, _, rest = pending.partition(b"\n")
            pending[:] = rest
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(event, dict) and event.get("type") in {
                "thread.started",
                "turn.started",
                "turn.completed",
                "turn.failed",
                "item.started",
                "item.updated",
                "item.completed",
                "error",
            }:
                label = event["type"]
                self.event_counts[label] = self.event_counts.get(label, 0) + 1
                if label == "turn.started":
                    self.turn_started.set()
                item = event.get("item")
                if (
                    label == "item.completed"
                    and isinstance(item, dict)
                    and item.get("type")
                    in {
                        "agent_message",
                        "reasoning",
                        "command_execution",
                        "file_change",
                        "mcp_tool_call",
                        "collab_tool_call",
                        "web_search",
                        "todo_list",
                        "error",
                    }
                ):
                    item_type = item["type"]
                    self.item_counts[item_type] = self.item_counts.get(item_type, 0) + 1

    async def _read(self, stream: asyncio.StreamReader) -> bytes:
        if not any(process.stdout is stream for process in self._children):
            return await super()._read(stream)
        owner = self
        pending = bytearray()

        class Reader:
            async def read(self, size: int) -> bytes:
                data = await stream.read(size)
                owner.observe(data, pending)
                return data

        return await super()._read(Reader())

    async def execute(self, command: tuple[str, ...], **kwargs: Any) -> Any:
        if self.dispatches:
            raise RuntimeError("Only one native-generation submission is allowed per invocation")
        self.dispatches += 1
        if "--output-last-message" in command:
            self.private_file = Path(command[command.index("--output-last-message") + 1])
            self.private_directory_mode = stat.S_IMODE(self.private_file.parent.stat().st_mode) == 0o700
            self.private_file_mode = (
                not self.private_file.is_symlink()
                and self.private_file.is_file()
                and stat.S_IMODE(self.private_file.stat().st_mode) == 0o600
            )
        original = kwargs.get("on_started")

        def started() -> None:
            self.spawns += 1
            if original is not None:
                original()

        return await super().execute(command, **dict(kwargs, on_started=started))

    async def _cleanup(self, process: asyncio.subprocess.Process) -> None:
        await super()._cleanup(process)
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        self.cleanup_confirmed = False


def create_fixture(root: Path) -> tuple[Path, str]:
    workspace = root / "workspace"
    workspace.mkdir()
    token = "NATIVE_GENERATION_READ_" + uuid.uuid4().hex
    (workspace / "tool-context.txt").write_text(token + "\n")
    return workspace.resolve(strict=True), token


def request_body(check: str, workspace: Path) -> dict[str, Any]:
    instructions = "Improve the rough prompt. Preserve Atlas, Mira, Friday, exactly two risks, rollback and acceptance checks. Return only the improved prompt."
    content = "Need plan to move Atlas cache Friday. Mira owns it. Need two risks, rollback, checks."
    if check == "tool":
        instructions = "Read tool-context.txt using your native permitted tools and return exactly its full one-line text. Do not modify any file. If native permissions prevent reading, report that limitation."
        content = "The required source is the tool-context.txt file in the selected project. Its content is not supplied in this request."
    elif check == "cancel":
        instructions = (
            "Develop a detailed engineering migration plan. Use no external services and do not change files."
        )
        content = "Plan an Atlas cache migration including staged rollout, failure analysis, metrics, rollback, and detailed acceptance checks."
    return {
        "schema_version": 2,
        "execution_mode": "native",
        "project_dir": str(workspace),
        "request_id": str(uuid.uuid4()),
        "cli": "codex",
        "model": "gpt-6.1-sol",
        "instructions": instructions,
        "input": content,
        "timeout_seconds": 120,
    }


async def run_check(
    check: str, executable: str, root: Path, binding: dict[str, Any], preflight: Path, timeout: int
) -> dict[str, Any]:
    generation, contracts, policy = modules()
    workspace, token = create_fixture(root)
    body = request_body(check, workspace)
    body["timeout_seconds"] = timeout
    request = contracts.parse_native_request(json.dumps(body).encode())
    pool = ObservedPool()
    service = generation.NativeGenerationService(
        registry=FixtureRegistry(executable, workspace),
        pool=pool,
        admission=policy.NativeGenerationAdmission(
            version=VERSION,
            verified=True,
            executable_sha256=binding["executable_sha256"],
            recipe_sha256=binding["recipe_sha256"],
            parser_sha256=binding["parser_sha256"],
            evidence_path=str(preflight),
            evidence_sha256=digest(preflight.read_bytes()),
        ),
    )
    task = asyncio.create_task(service.run(request))
    turn_seen = False
    try:
        if check == "cancel":
            waiting = asyncio.create_task(pool.turn_started.wait())
            done, _ = await asyncio.wait(
                {task, waiting}, timeout=min(120, max(0, timeout - 15)), return_when=asyncio.FIRST_COMPLETED
            )
            turn_seen = waiting in done and waiting.result() is True
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            if not task.done():
                task.cancel()
        result = await asyncio.wait_for(asyncio.shield(task), timeout + 15)
    finally:
        if not task.done():
            task.cancel()
        await pool.shutdown()
        await asyncio.gather(task, return_exceptions=True)
    execution = result.get("execution", {})
    output = result.get("output")
    completed = result.get("status") == "completed" and result.get("truncated") is False and result.get("error") is None
    useful = (
        isinstance(output, str)
        and len(output.encode()) >= 100
        and all(word in output.lower() for word in ("atlas", "mira", "friday", "risk", "rollback", "check"))
        and re.search(r"\b(?:two|2)\b.{0,24}\brisks?\b", output.lower()) is not None
        and output != body["input"]
    )
    final_removed = (
        pool.private_file is not None and not pool.private_file.exists() and not pool.private_file.parent.exists()
    )
    cleanup = pool.cleanup_confirmed and pool.running == 0 and not pool._children and final_removed
    identity = (
        execution.get("profile") == PROFILE
        and execution.get("execution_mode") == "native"
        and execution.get("permission_observability") == "limited"
        and execution.get("fulfillment_verified") is False
        and execution.get("requested_model") == execution.get("effective_model") == "gpt-6.1-sol"
        and execution.get("actual_model") in {None, "gpt-6.1-sol"}
        and execution.get("catalog_sha256") is None
        and execution.get("side_effects_possible") is True
        and execution.get("project_dir") == str(workspace)
        and execution.get("admission") == "verified"
        and all(execution.get(key) == binding[key] for key in ("executable_sha256", "recipe_sha256"))
    )
    passed = (
        cleanup
        and identity
        and pool.dispatches == pool.spawns == 1
        and pool.private_directory_mode
        and pool.private_file_mode
    )
    if check == "cancel":
        passed = passed and turn_seen and result.get("status") == "cancelled" and output is None
    elif check == "tool":
        passed = passed and completed and isinstance(output, str) and output.strip() == token
    else:
        passed = passed and completed and useful
    return {
        "passed": bool(passed),
        "check": check,
        "production_service_used": True,
        "production_final_file_validation_used": True,
        "terminal_status": safe_identifier(result.get("status")),
        "error_code": safe_identifier((result.get("error") or {}).get("code")),
        "requested_model": "gpt-6.1-sol",
        "effective_model": safe_identifier(execution.get("effective_model")),
        "actual_model": safe_identifier(execution.get("actual_model")),
        "permission_observability": "limited",
        "fulfillment_verified": False,
        "side_effects_possible": execution.get("side_effects_possible") is True,
        "useful_transformation": useful if check == "text" else None,
        "unprovided_file_token_matches": (
            output.strip() == token if isinstance(output, str) and check == "tool" else None
        ),
        "turn_started_before_cancellation": turn_seen if check == "cancel" else None,
        "dispatch_count": pool.dispatches,
        "spawn_count": pool.spawns,
        "event_counts": pool.event_counts,
        "supported_item_counts": pool.item_counts,
        "supported_tool_item_count": sum(
            pool.item_counts.get(name, 0)
            for name in ("command_execution", "file_change", "mcp_tool_call", "collab_tool_call", "web_search")
        ),
        "private_directory_mode_0700": pool.private_directory_mode,
        "private_file_mode_0600": pool.private_file_mode,
        "private_output_removed_after_cleanup": final_removed,
        "process_groups_absent_after_cleanup": pool.cleanup_confirmed,
        "pool_running_after_shutdown": pool.running,
        "pool_children_after_shutdown": len(pool._children),
        "request_sha256": result.get("request_sha256"),
        "instructions_sha256": result.get("instructions_sha256"),
        "input_sha256": result.get("input_sha256"),
        "output_sha256": digest(output.encode()) if isinstance(output, str) else None,
        "output_bytes": len(output.encode()) if isinstance(output, str) else 0,
        "warnings": [safe_identifier(item.get("code")) or "unknown" for item in result.get("warnings", [])][:8],
        "raw_output_retained": False,
    }


async def _verify(args: argparse.Namespace) -> dict[str, Any]:
    if args.check not in CHECKS or not 1 <= args.timeout <= 180:
        raise ValueError("Select one bounded native-generation check")
    ledger, prior = load_history(args.prior_evidence)
    if args.output_dir.expanduser().resolve() != LINEAGE_ROOT.resolve():
        raise ValueError("Native-generation evidence uses its fixed durable lineage root")
    known = {item["entry"]["reservation_sha256"] for item in authoritative_history()}
    retained = {entry["reservation_sha256"] for entry in ledger}
    if known != retained:
        raise ValueError("Prior evidence must retain every submitted native-generation attempt")
    replacement = (
        submission_authorization(ledger, args.check, prior, args.replacement_review, args.replacement_review_sha256)
        if args.live
        else None
    )
    target = evidence_directory(args.output_dir, args.label)
    record = {
        "schema_version": 1,
        "scope": SCOPE,
        "profile": PROFILE,
        "label": args.label,
        "check": args.check,
        "live_requested": args.live,
        "live_submitted": False,
        "submission_ledger": ledger,
        "submissions_consumed_total": len(ledger),
        "submissions_in_this_invocation": 0,
        "maximum_submissions_per_invocation": 1,
        "submission_allowance_total": MAX_SUBMISSIONS,
        "prior_evidence_sha256": prior,
        "replacement_review": replacement,
        "automatic_retries": 0,
        "candidate_admission_override": "process_local_only_no_native_permission_configuration_or_trust_override",
        "production_admission_changed": False,
        "native_task_admission_changed": False,
        "raw_output_retained": False,
        "prompts_recorded": False,
        "environment_values_recorded": False,
        "native_sessions_recorded": False,
        "native_trust_enrolled": False,
        "host_configuration_changed": False,
        "limitations": list(LIMITATIONS),
        "admission": "policy_unverified",
        "gate_passed": False,
        "verifier_sha256": digest(Path(__file__).read_bytes()),
        "source_manifest_sha256": args.source_manifest_sha256,
        "ownership_appendix_sha256": OWNERSHIP_APPENDIX_SHA256,
        "login_appendix_sha256": LOGIN_APPENDIX_SHA256,
    }
    preflight = target / "preflight.json"
    try:
        _, _, policy = modules()
        from clink.native_generation_files import final_file_contract_digest
        from clink.parsers.codex_native_generation import parser_contract_digest

        record["historical_lineages"] = historical_lineages()
        record["source_proof"] = source_proof(args.source_manifest, args.source_manifest_sha256)
        record["source_appendices"] = [
            appendix_proof(args.ownership_appendix, OWNERSHIP_APPENDIX_SHA256),
            appendix_proof(args.login_appendix, LOGIN_APPENDIX_SHA256),
        ]
        record["implementation_source_manifest"] = implementation_manifest()
        record["implementation_source_sha256"] = digest(canonical_json(record["implementation_source_manifest"]))
        executable = probes.shutil.which("codex")
        if executable is None:
            raise RuntimeError("Installed Codex is unavailable")
        executable = str(Path(executable).resolve(strict=True))
        binary = executable_digest(executable)
        record.update(
            {
                "executable_sha256": binary,
                "researched_executable_sha256": RESEARCHED_EXECUTABLE_SHA256,
                "binary_matches_researched": binary == RESEARCHED_EXECUTABLE_SHA256,
            }
        )
        if binary != RESEARCHED_EXECUTABLE_SHA256:
            record["preflight_failure"] = "unresearched_binary"
            raise RuntimeError("Installed native Codex differs from the exact researched binary")
        code, version = probes.bounded_version(executable, probes.child_environment(dict(os.environ)))
        if code != 0 or version.strip() != VERSION.encode() or executable_digest(executable) != binary:
            raise RuntimeError("Installed native Codex identity differs from the researched release")
        record["authentication_preflight"] = authentication_preflight(executable, args.login_source)
        if not all(
            record["authentication_preflight"].get(name) is True
            for name in ("native_auth_configured", "chatgpt_status_reported")
        ):
            record["preflight_failure"] = "chatgpt_auth_not_ready"
            raise RuntimeError("This subscription-only lineage requires native ChatGPT login readiness")
        record.update(
            {
                "observed_version": VERSION,
                "executable_sha256": binary,
                "recipe_sha256": policy.native_generation_recipe_digest(),
                "parser_sha256": parser_contract_digest(),
                "final_file_sha256": final_file_contract_digest(),
                "catalog_sha256": None,
                "recipe_proof": recipe_proof(),
            }
        )
        if not record["recipe_proof"]["verified"]:
            raise RuntimeError("Native-generation recipe contains unsupported or authority-changing arguments")
        record["candidate_sha256"] = require_candidate_review(replacement, record)
        persist(preflight, record)
        if args.live:
            entry = {
                "ordinal": len(ledger) + 1,
                "check": args.check,
                "passed": False,
                "replacement": replacement is not None,
            }
            reservation = {
                "schema_version": 1,
                "scope": SCOPE,
                "profile": PROFILE,
                "ordinal": entry["ordinal"],
                "check": args.check,
                "replacement": entry["replacement"],
                "state": "consumed_before_dispatch",
                "preflight_sha256": digest(preflight.read_bytes()),
            }
            entry["reservation_sha256"] = persist(target / "submitted.json", reservation)
            ledger.append(entry)
            record.update(
                {"submissions_consumed_total": len(ledger), "submissions_in_this_invocation": 1, "live_submitted": True}
            )
            fixture_root = PROJECT / "test_output/native-generation" / args.label
            fixture_root.mkdir(parents=True, mode=0o700)
            record["observation"] = await run_check(
                args.check, executable, fixture_root, record, preflight, args.timeout
            )
            entry["passed"] = record["observation"]["passed"]
            record["gate_passed"] = entry["passed"]
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        record["error_class"] = type(error).__name__
        if not preflight.exists():
            persist(preflight, record)
    finally:
        passed = {entry["check"] for entry in ledger if entry["passed"]}
        record["all_native_generation_gates_passed"] = set(CHECKS) <= passed
        persist(target / "receipt.json", record)
    return record


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    """Serialize lineage reservations so concurrent invocations cannot reset the budget."""
    refuse_live_verification(args.live, PROJECT)
    directory = PROJECT / "test_output/native-generation"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(directory / ".verification.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another native-generation verifier owns this lineage") from None
        return await _verify(args)
    finally:
        os.close(descriptor)


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--check", choices=CHECKS, default="text")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Unavailable in public snapshot; continue only with the original private verifier and history",
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "docs/verification/native-generation")
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument(
        "--login-source", type=Path, default=LOGIN_SOURCE, help="Exact retained pinned native login-status source"
    )
    parser.add_argument("--ownership-appendix", type=Path, default=OWNERSHIP_APPENDIX)
    parser.add_argument("--login-appendix", type=Path, default=LOGIN_APPENDIX)
    parser.add_argument("--prior-evidence", type=Path)
    parser.add_argument("--replacement-review", type=Path)
    parser.add_argument("--replacement-review-sha256")
    return parser


def main() -> int:
    parser = argument_parser()
    try:
        args = parser.parse_args()
        if args.live:
            parser.error(LIVE_HISTORY_REFUSAL)
        record = asyncio.run(supervised_verify(args))
    except asyncio.CancelledError:
        return 130
    except (OSError, ValueError) as error:
        parser.error("Native-generation verification refused: " + type(error).__name__)
    print(
        json.dumps(
            {
                "profile": PROFILE,
                "check": args.check,
                "live_submitted": record["live_submitted"],
                "gate_passed": record["gate_passed"],
                "submissions_consumed_total": record["submissions_consumed_total"],
                "receipt": str(args.output_dir.resolve() / args.label / "receipt.json"),
            }
        )
    )
    return 0 if record["gate_passed"] or (not args.live and "error_class" not in record) else 2


async def supervised_verify(args: argparse.Namespace) -> dict[str, Any]:
    task = asyncio.create_task(verify(args))
    loop = asyncio.get_running_loop()
    cancellation_requested = False

    def cancel_once() -> None:
        nonlocal cancellation_requested
        if not cancellation_requested:
            cancellation_requested = True
            task.cancel()

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, cancel_once)
    try:
        return await task
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    raise SystemExit(main())
