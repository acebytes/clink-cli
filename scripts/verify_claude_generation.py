"""Verify Claude generation with its durable default four-submission lineage.

The public snapshot refuses live submissions to preserve archived call budgets.
Use the original private verifier and complete history for live continuation.

Each invocation is read-only unless --live explicitly reserves one top-level
submission. Native configuration, accounts and permissions remain authoritative.
No automatic retries, production promotion, trust enrollment or counter reset.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import importlib.abc
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import types
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
_HOST_SYS_PATH = tuple(sys.path)
_HOST_META_PATH = tuple(sys.meta_path)
_HOST_SCRIPTS = sys.modules.get("scripts")


def _source_bytes(path: Path) -> bytes:
    if path.resolve(strict=True) != path:
        raise ValueError("Source must use a canonical file path")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise ValueError("Source must be a bounded regular file")
        content = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()

    def fields(value: os.stat_result) -> tuple[int, int, int, int]:
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    if len(content) > 1024 * 1024 or fields(before) != fields(after) or fields(after) != fields(current):
        raise ValueError("Source changed while reading")
    return content


def _package_sources(package: Path) -> tuple[dict[str, Any], str]:
    if package.resolve(strict=True) != package or not (package / "__init__.py").is_file():
        raise ValueError("Clink package must use a canonical source directory")
    paths = []
    root = package.parent
    for name in ("clink", "utils"):
        tree = root / name
        if tree.resolve(strict=True) != tree or not (tree / "__init__.py").is_file():
            raise ValueError("Clink and utils must be canonical source packages")
        for directory, directories, names in os.walk(tree, followlinks=False):
            parent = Path(directory)
            if any((parent / name).is_symlink() for name in directories):
                raise ValueError("Package source directories cannot be symlinks")
            paths.extend(parent / name for name in names if name.endswith(".py"))
            if len(paths) > 256:
                raise ValueError("Package source exceeds its file-count bound")
    if (root / "config.py").exists() or (root / "config.py").is_symlink():
        paths.append(root / "config.py")
    if not paths:
        raise ValueError("Clink package source is empty")
    files = []
    total = 0
    for path in sorted(paths):
        content = _source_bytes(path)
        total += len(content)
        if total > 16 * 1024 * 1024:
            raise ValueError("Package source exceeds its aggregate byte bound")
        files.append({"path": str(path.relative_to(root)), "sha256": hashlib.sha256(content).hexdigest()})
    manifest = {"schema_version": 2, "files": files}
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return manifest, hashlib.sha256(encoded).hexdigest()


def _select_package(root: Path | None, expected: str | None) -> tuple[Path, str]:
    location = (root if root is not None else PROJECT).expanduser().absolute()
    if location.resolve(strict=True) != location or not location.is_dir():
        raise ValueError("Package root must be an existing canonical directory")
    _, checksum = _package_sources(location / "clink")
    if root is not None and (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        raise ValueError("An explicit package root needs its package source SHA-256")
    if expected is not None and expected != checksum:
        raise ValueError("Package source differs from its explicit pin")
    return location, checksum


def _bootstrap_package() -> tuple[Path, str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--package-source-sha256")
    args, _ = parser.parse_known_args()
    return _select_package(args.package_root, args.package_source_sha256)


PACKAGE_ROOT, PACKAGE_SOURCE_SHA256 = _bootstrap_package()
SOURCE_PROJECT = PROJECT
SCRIPT_ROOT = PROJECT / "scripts"
_PRODUCTION_ROOTS = frozenset({"clink", "utils", "config", "conf", "server", "systemprompts", "tools"})
_HARNESS_SOURCES = (
    "scripts/verify_claude_generation.py",
    "scripts/verify_native_generation.py",
    "scripts/verify_cli_policy.py",
    "scripts/verify_generation_policy.py",
    "scripts/claude_generation_review.py",
    "scripts/private_evidence.py",
)
_PACKAGE_MANIFEST, _PACKAGE_CHECKSUM = _package_sources(PACKAGE_ROOT / "clink")
if _PACKAGE_CHECKSUM != PACKAGE_SOURCE_SHA256:
    raise ValueError("Selected package source changed during bootstrap")
_PINNED_SOURCES = {PACKAGE_ROOT / item["path"]: item["sha256"] for item in _PACKAGE_MANIFEST["files"]}
_PINNED_SOURCES.update(
    {
        SOURCE_PROJECT / name: hashlib.sha256(_source_bytes(SOURCE_PROJECT / name)).hexdigest()
        for name in _HARNESS_SOURCES
    }
)
_EXECUTED_SOURCES: dict[str, tuple[Any, Path, str]] = {}
_CAPTURED_MODULES: dict[str, Any] = {}
_CAPTURED_UNPROVEN: tuple[str, ...] = ()


def _checked_source(path: Path) -> bytes:
    expected = _PINNED_SOURCES.get(path)
    if expected is None:
        raise ModuleNotFoundError("Selected source is outside the frozen manifest")
    content = _source_bytes(path)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Selected source differs from its frozen manifest")
    return content


def _audit_imports() -> list[dict[str, str]]:
    observed = []
    for name, module in tuple(sys.modules.items()):
        production = name.split(".", 1)[0] in _PRODUCTION_ROOTS
        helper = name == "scripts" or name.startswith("scripts.")
        if not production and not helper:
            continue
        selected = PACKAGE_ROOT if production else SOURCE_PROJECT
        file = getattr(module, "__file__", None)
        if (production or (helper and name != "scripts")) and file is None:
            raise ValueError("Preloaded source identity is unavailable")
        if file is not None:
            location = Path(file).absolute()
            expected = selected / name.replace(".", "/")
            if (
                location.resolve(strict=True) != location
                or (production and location not in {expected.with_suffix(".py"), expected / "__init__.py"})
                or (helper and not location.is_relative_to(SCRIPT_ROOT))
            ):
                raise ValueError("Imported source escaped its selected package or scripts root")
            observed.append({"module": name, "path": str(location)})
        for path in getattr(module, "__path__", ()):
            directory = Path(path).absolute()
            wanted = selected / name.replace(".", "/")
            if directory.resolve(strict=True) != directory or directory != wanted:
                raise ValueError("Imported package search path escaped its selected source root")
    return sorted(observed, key=lambda item: item["module"])


def _protected_module(name: str) -> bool:
    return name.split(".", 1)[0] in _PRODUCTION_ROOTS or name.replace(".", "/") + ".py" in _HARNESS_SOURCES


def _has_execution_provenance(name: str, module: Any) -> bool:
    executed = _EXECUTED_SOURCES.get(name)
    return executed is not None and executed[0] is module and _PINNED_SOURCES.get(executed[1]) == executed[2]


def _assert_execution_provenance() -> None:
    if _CAPTURED_UNPROVEN or any(sys.modules.get(name) is not module for name, module in _CAPTURED_MODULES.items()):
        raise ValueError("Authoritative verification requires checked source execution in a fresh interpreter")
    for name, module in tuple(sys.modules.items()):
        if _protected_module(name) and not _has_execution_provenance(name, module):
            raise ValueError("Protected preloaded code has no matching checked-source execution provenance")


class _PinnedSourceLoader(importlib.abc.Loader):
    def __init__(self, path: Path, name: str) -> None:
        self.path = path
        self.name = name

    def create_module(self, spec: Any) -> None:
        return None

    def exec_module(self, module: Any) -> None:
        content = _checked_source(self.path)
        exec(compile(content, str(self.path), "exec", dont_inherit=True), module.__dict__)
        _EXECUTED_SOURCES[self.name] = (module, self.path, hashlib.sha256(content).hexdigest())


class _ProductionImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        production = fullname.split(".", 1)[0] in _PRODUCTION_ROOTS
        helper = str(fullname.replace(".", "/") + ".py") in _HARNESS_SOURCES
        if not production and not helper:
            return None
        root = PACKAGE_ROOT if production else SOURCE_PROJECT
        if path is not None:
            expected = root / fullname.rsplit(".", 1)[0].replace(".", "/")
            if any(Path(entry).absolute().resolve(strict=True) != expected for entry in path):
                raise ModuleNotFoundError("Production import escaped its selected package root")
        location = root / fullname.replace(".", "/")
        for source, package in ((location / "__init__.py", True), (location.with_suffix(".py"), False)):
            if source in _PINNED_SOURCES:
                return importlib.util.spec_from_file_location(
                    fullname,
                    source,
                    loader=_PinnedSourceLoader(source, fullname),
                    submodule_search_locations=[str(location)] if package else None,
                )
        raise ModuleNotFoundError("A selected pinned source module is unavailable")


@contextmanager
def _source_imports():
    original_path = list(sys.path)
    finder = _ProductionImports()
    sys.meta_path.insert(0, finder)
    try:
        _remove_checkout_fallback()
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.path[:] = original_path


def _remove_checkout_fallback() -> None:
    if PACKAGE_ROOT != SOURCE_PROJECT:
        sys.path[:] = [entry for entry in sys.path if Path(entry or os.getcwd()).absolute().resolve() != SOURCE_PROJECT]
    if str(PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(PACKAGE_ROOT))


_audit_imports()
_INITIAL_IMPORT_GUARD = _ProductionImports()
sys.meta_path.insert(0, _INITIAL_IMPORT_GUARD)
_remove_checkout_fallback()
_scripts = types.ModuleType("scripts")
_scripts.__path__ = [str(SCRIPT_ROOT)]
sys.modules["scripts"] = _scripts

from clink.generation_contracts import canonical_json, sha256  # noqa: E402
from clink.models import ResolvedCLIClient  # noqa: E402
from clink.native_generation import NativeGenerationService  # noqa: E402
from clink.native_generation_claude_policy import (  # noqa: E402
    DEFAULT_CLAUDE_NATIVE_ADMISSION,
    claude_generation_arguments,
    claude_generation_recipe_digest,
)
from clink.native_generation_contracts import parse_native_request  # noqa: E402
from clink.native_generation_policy import NativeGenerationAdmission  # noqa: E402
from clink.native_policy import native_environment_names  # noqa: E402
from clink.parsers.claude_native_generation import parser_contract_digest, protocol_diagnostic  # noqa: E402
from clink.policy import executable_digest  # noqa: E402
from clink.runner import STREAM_BYTES, ProcessPool  # noqa: E402
from scripts.claude_generation_review import completion_proof  # noqa: E402
from scripts.private_evidence import LIVE_HISTORY_REFUSAL, refuse_live_verification  # noqa: E402
from scripts.verify_cli_policy import bounded_version  # noqa: E402
from scripts.verify_generation_policy import evidence_directory, persist, safe_identifier  # noqa: E402
from scripts.verify_native_generation import evidence_json, regular_bytes, regular_json, valid_sha256  # noqa: E402

_remove_checkout_fallback()
_audit_imports()
_CAPTURED_MODULES = {name: module for name, module in tuple(sys.modules.items()) if _protected_module(name)}
_CAPTURED_UNPROVEN = tuple(
    name for name, module in _CAPTURED_MODULES.items() if not _has_execution_provenance(name, module)
)
if __name__ == "scripts.verify_claude_generation":
    sys.meta_path[:] = _HOST_META_PATH
    sys.path[:] = _HOST_SYS_PATH
    if _HOST_SCRIPTS is not None:
        sys.modules["scripts"] = _HOST_SCRIPTS

PROFILE = "generation_native_v1"
SCOPE = "candidate_claude_native_generation"
VERSION = "2.1.304 (Claude Code)"
RESEARCHED_EXECUTABLE_SHA256 = "b9ac5374839f14abf3f1f45610637c332b5daee5ce0d1634726cef87aab78e6a"
DEFAULT_MODEL = "claude-opus-5-5"
CHECKS = ("text", "read", "cancel")
MAX_SUBMISSIONS = 4
LINEAGE_ROOT = PROJECT / "docs/verification/claude-generation"
REVIEW_TASK = "fn-3-bundle-claude-and-codex-clink.1"
REVIEW_CORE = {
    "type": "impl_review",
    "id": REVIEW_TASK,
    "mode": "codex",
    "verdict": "SHIP",
    "model": "gpt-6.1-sol",
    "effort": "high",
    "spec": "codex:gpt-6.1-sol:high",
}
APPROVAL_MARKER = "CLAUDE_GENERATION_REPLACEMENT_APPROVED_SHA256="
EXCEPTION_POLICY = "claude_text_third_attempt_v1"
EXCEPTION_REVIEW_TASK = "fn-3-bundle-claude-and-codex-clink.6"
EXCEPTION_MARKER = "CLAUDE_GENERATION_BOUNDED_EXCEPTION_APPROVED_SHA256="
EXCEPTION_LEGACY_PACKET = "test_output/bundled-generation/claude-text-replacement/packet.json"
EXCEPTION_LEGACY_PACKET_SHA256 = "d059e0a7faccb72307d3116d4e8ed49d1b92c8e0f44314be504c6427f589e690"
EXCEPTION_LEGACY_REVIEW = "test_output/bundled-generation/review-task1-r1/receipt.json"
EXCEPTION_LEGACY_REVIEW_SHA256 = "3426fcf87e5f1de848c160b8cedea50210e59840fd534352e4173d816ac23227"
EXCEPTION_BUDGET = {
    "max_total": 5,
    "max_per_gate": {"text": 3, "read": 1, "cancel": 1},
    "max_replacements": 2,
    "max_per_invocation": 1,
    "automatic_retries": 0,
}
EXECUTION_FIELDS = (
    "executable_sha256",
    "recipe_sha256",
    "parser_sha256",
    "model",
    "package_source_sha256",
    "python_executable_sha256",
)
CANDIDATE_FIELDS = (
    *EXECUTION_FIELDS,
    "implementation_source_sha256",
    "verifier_sha256",
)
SCHEMA2_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "scope",
        "profile",
        "cli",
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
        "automatic_retries",
        "production_admission_changed",
        "native_task_admission_reused",
        "host_configuration_changed",
        "native_trust_enrolled",
        "raw_output_retained",
        "native_sessions_recorded",
        "environment_values_recorded",
        "prompts_recorded",
        "limitations",
        "budget_exception",
        "preflight_phase",
        "fixture_controls",
        "native_hook_policy",
        "replacement_review",
        "observation",
        "error_class",
        "all_claude_generation_gates_passed",
        "candidate_sha256",
        "observed_version",
        "implementation_source_manifest",
        "runtime_source_modules",
        "python_executable",
        "python_version",
        "clink_package_directory",
        "harness_project_directory",
        *CANDIDATE_FIELDS,
    }
)
RUNTIME_FIELDS = (
    "python_executable",
    "python_version",
    "python_executable_sha256",
    "clink_package_directory",
    "package_source_sha256",
    "harness_project_directory",
)
LIMITATIONS = [
    "Native tools, hooks, integrations, subagents and provider-internal retries may perform additional work.",
    "Completion validates a final response, not fulfillment or complete permission telemetry.",
    "Cancellation does not undo effects or prove detached or remote work stopped.",
    "Native login stores are retained; account readiness and provider billing are not independently inspected.",
    "Candidate admission is process-local and never enables production or native tasks.",
    "Fixture hooks are installed, bound and selftested before dispatch; native honoring requires fresh runtime observations.",
    "Read-only local hook metadata does not observe every server-managed or MDM policy source.",
]


def _candidate_digest(binding: dict[str, Any]) -> str:
    values = {key: binding.get(key) for key in CANDIDATE_FIELDS}
    if not isinstance(values["model"], str) or any(
        not valid_sha256(value) for key, value in values.items() if key != "model"
    ):
        raise ValueError("Candidate must bind every current implementation and execution digest")
    return sha256(canonical_json(values))


def _lineage_path(path: Path) -> Path:
    location = path.absolute()
    if location.parent.parent != LINEAGE_ROOT or location.resolve(strict=True) != location:
        raise ValueError("Evidence must retain its original canonical lineage location")
    return location


def _validate_entry(entry: Any, ordinal: int) -> None:
    if (
        not isinstance(entry, dict)
        or set(entry) != {"ordinal", "check", "passed", "replacement", "reservation_sha256"}
        or type(entry.get("ordinal")) is not int
        or entry["ordinal"] != ordinal
        or entry.get("check") not in CHECKS
        or type(entry.get("passed")) is not bool
        or type(entry.get("replacement")) is not bool
        or not valid_sha256(entry.get("reservation_sha256"))
    ):
        raise ValueError("Malformed Claude generation submission ledger")


def validate_ledger(ledger: Any) -> None:
    if not isinstance(ledger, list) or len(ledger) > MAX_SUBMISSIONS:
        raise ValueError("Claude generation permits at most four submitted calls")
    counts = dict.fromkeys(CHECKS, 0)
    previous = {}
    replacements = 0
    for ordinal, entry in enumerate(ledger, 1):
        _validate_entry(entry, ordinal)
        check = entry["check"]
        counts[check] += 1
        replacements += int(entry["replacement"])
        if counts[check] > 2 or entry["replacement"] != (counts[check] == 2) or previous.get(check, False):
            raise ValueError("A gate permits two attempts and cannot repeat success")
        previous[check] = entry["passed"]
    if replacements > 1:
        raise ValueError("Only one independently reviewed replacement is allowed")


def _exception_ledger(ledger: Any) -> None:
    """Check structure only; original proof validation separately grants authority."""
    if not isinstance(ledger, list) or not 2 <= len(ledger) <= EXCEPTION_BUDGET["max_total"]:
        raise ValueError("The bounded exception requires its original two failures and at most five calls")
    validate_ledger(ledger[:2])
    schedule = (("text", False), ("text", True), ("text", True), ("read", False), ("cancel", False))
    for ordinal, row in enumerate(ledger, 1):
        _validate_entry(row, ordinal)
        check, replacement = schedule[ordinal - 1]
        if row["check"] != check or row["replacement"] is not replacement:
            raise ValueError("The bounded exception permits only text3, read4, cancel5 in order")
        if ordinal <= 2 and row["passed"]:
            raise ValueError("The bounded exception requires both original text failures")
        if ordinal > 3 and not ledger[ordinal - 2]["passed"]:
            raise ValueError("A failed or interrupted exception gate stops the remaining schedule")


def _exception_binding(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"policy", "proof", "reviewed_input_sha256"}
        or value.get("policy") != EXCEPTION_POLICY
        or not valid_sha256(value.get("reviewed_input_sha256"))
    ):
        raise ValueError("Unsupported bounded exception identity")
    reference = value["proof"]
    if (
        not isinstance(reference, dict)
        or set(reference) != {"path", "sha256"}
        or not isinstance(reference.get("path"), str)
        or not Path(reference["path"]).is_absolute()
        or not valid_sha256(reference.get("sha256"))
    ):
        raise ValueError("The bounded exception needs its pinned original proof")
    return value


def _exception_limits(record: dict[str, Any]) -> None:
    record.update(
        schema_version=2,
        submission_allowance_total=EXCEPTION_BUDGET["max_total"],
        maximum_submissions_per_gate=dict(EXCEPTION_BUDGET["max_per_gate"]),
        maximum_replacements=EXCEPTION_BUDGET["max_replacements"],
    )


def _ledger(record: dict[str, Any]) -> list[dict[str, Any]]:
    version = record.get("schema_version")
    if (
        type(version) is not int
        or version not in {1, 2}
        or record.get("scope") != SCOPE
        or record.get("profile") != PROFILE
        or record.get("cli") != "claude"
        or type(record.get("submissions_consumed_total")) is not int
    ):
        raise ValueError("Evidence belongs to another verification lineage")
    ledger = record.get("submission_ledger")
    if version == 1:
        if "budget_exception" in record:
            raise ValueError("Legacy evidence cannot select exception authority")
        validate_ledger(ledger)
    else:
        if set(record) - SCHEMA2_RECORD_FIELDS:
            raise ValueError("Exception evidence has unsupported record fields")
        _exception_binding(record.get("budget_exception"))
        _exception_ledger(ledger)
        limits = {
            "submission_allowance_total": EXCEPTION_BUDGET["max_total"],
            "maximum_submissions_per_gate": EXCEPTION_BUDGET["max_per_gate"],
            "maximum_replacements": EXCEPTION_BUDGET["max_replacements"],
            "maximum_submissions_per_invocation": 1,
            "automatic_retries": 0,
        }
        if any(canonical_json(record.get(key)) != canonical_json(value) for key, value in limits.items()):
            raise ValueError("Exception evidence changed its fixed bounded limits")
    if record["submissions_consumed_total"] != len(ledger):
        raise ValueError("Submission count differs from its ledger")
    return ledger


def authoritative_history() -> list[dict[str, Any]]:
    try:
        return _authoritative_history()
    except RecursionError:
        raise ValueError("Claude generation history exceeds its JSON nesting bound") from None


def _authoritative_history() -> list[dict[str, Any]]:
    paths = sorted(LINEAGE_ROOT.glob("*/submitted.json"))
    if len(paths) > EXCEPTION_BUDGET["max_total"]:
        raise ValueError("Claude generation submission allowance is exhausted")
    history = []
    for path in paths:
        reservation, raw = regular_json(_lineage_path(path))
        if (
            type(reservation.get("schema_version")) is not int
            or reservation["schema_version"] not in {1, 2}
            or reservation.get("scope") != SCOPE
            or reservation.get("profile") != PROFILE
            or reservation.get("cli") != "claude"
            or reservation.get("state") != "consumed_before_dispatch"
            or type(reservation.get("ordinal")) is not int
            or type(reservation.get("replacement")) is not bool
            or reservation.get("check") not in CHECKS
            or not valid_sha256(reservation.get("preflight_sha256"))
            or not valid_sha256(reservation.get("candidate_sha256"))
        ):
            raise ValueError("Invalid durable Claude generation reservation")
        version = reservation["schema_version"]
        if version == 1:
            if "budget_exception" in reservation:
                raise ValueError("Legacy reservation cannot select exception authority")
        else:
            if set(reservation) != {
                "schema_version",
                "scope",
                "profile",
                "cli",
                "ordinal",
                "check",
                "replacement",
                "candidate_sha256",
                "state",
                "preflight_sha256",
                "budget_exception",
            }:
                raise ValueError("Exception reservation has unsupported fields")
            _exception_binding(reservation.get("budget_exception"))
        before, before_raw = regular_json(_lineage_path(path.parent / "preflight.json"))
        before_ledger = _ledger(before)
        if (
            sha256(before_raw) != reservation["preflight_sha256"]
            or before["schema_version"] != version
            or before.get("budget_exception") != reservation.get("budget_exception")
            or len(before_ledger) != reservation["ordinal"] - 1
            or before.get("check") != reservation["check"]
            or before.get("live_submitted") is not False
            or type(before.get("submissions_in_this_invocation")) is not int
            or before["submissions_in_this_invocation"] != 0
            or _candidate_digest(before) != before.get("candidate_sha256")
            or before.get("candidate_sha256") != reservation.get("candidate_sha256")
        ):
            raise ValueError("Reservation changed its immutable preflight binding")
        terminal_path = path.parent / "receipt.json"
        terminal = None
        terminal_sha = None
        passed = False
        if terminal_path.exists() or terminal_path.is_symlink():
            terminal, terminal_raw = regular_json(_lineage_path(terminal_path))
            after = _ledger(terminal)
            passed = terminal.get("gate_passed")
            observation = terminal.get("observation")
            if (
                terminal.get("check") != reservation["check"]
                or terminal["schema_version"] != version
                or terminal.get("budget_exception") != reservation.get("budget_exception")
                or terminal.get("live_submitted") is not True
                or type(terminal.get("submissions_in_this_invocation")) is not int
                or terminal["submissions_in_this_invocation"] != 1
                or _candidate_digest(terminal) != terminal.get("candidate_sha256")
                or len(after) != reservation["ordinal"]
                or type(passed) is not bool
                or terminal.get("candidate_sha256") != reservation.get("candidate_sha256")
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
                raise ValueError("Terminal observation does not bind its original reservation")
            terminal_sha = sha256(terminal_raw)
        entry = {
            "ordinal": reservation["ordinal"],
            "check": reservation["check"],
            "replacement": reservation["replacement"],
            "passed": passed,
            "reservation_sha256": sha256(raw),
        }
        history.append(
            {
                "entry": entry,
                "reservation": reservation,
                "preflight": before,
                "terminal": terminal,
                "terminal_sha256": terminal_sha,
                "preflight_sha256": sha256(before_raw),
                "directory": path.parent,
            }
        )
    history.sort(key=lambda item: item["entry"]["ordinal"])
    entries = [item["entry"] for item in history]
    if any(item["preflight"]["schema_version"] == 2 for item in history):
        _exception_ledger(entries)
        if any(item["preflight"]["schema_version"] != (1 if index < 2 else 2) for index, item in enumerate(history)):
            raise ValueError("Exception history must preserve the original legacy prefix without downgrade")
        binding = _exception_binding(history[2]["preflight"].get("budget_exception"))
        candidate = history[2]["preflight"]["candidate_sha256"]
        proof = bounded_exception_proof(
            Path(binding["proof"]["path"]), binding["proof"]["sha256"], history[:2], candidate
        )
        if proof["budget_exception"] != binding:
            raise ValueError("Exception history differs from its original reviewed input")
        if any(
            item["preflight"].get("budget_exception") != binding
            or item["preflight"].get("candidate_sha256") != candidate
            for item in history[2:]
        ):
            raise ValueError("Exception continuation changed its authority or exact candidate")
    else:
        validate_ledger(entries)
    for item in history:
        for record in (item["preflight"], item["terminal"]):
            if record is not None and _ledger(record) != entries[: len(_ledger(record))]:
                raise ValueError("Historical ledger differs from original durable outcomes")
    return history


def _review_marker(text: str, checksum: str) -> None:
    from scripts.verify_native_generation import APPROVAL_MARKER as native_marker
    from scripts.verify_native_generation import require_reviewer_marker

    if text.count(APPROVAL_MARKER) != 1 or native_marker in text:
        raise ValueError("Review must explicitly approve one Claude replacement input")
    require_reviewer_marker(text.replace(APPROVAL_MARKER, native_marker), checksum)


def replacement_proof(
    path: Path | None, expected: str | None, failed: dict[str, Any], candidate: str
) -> dict[str, Any]:
    if path is None or not valid_sha256(expected):
        raise ValueError("A failed or interrupted gate needs a pinned independently reviewed diagnosis")
    packet, raw = regular_json(path)
    if (
        sha256(raw) != expected
        or set(packet)
        != {
            "schema_version",
            "scope",
            "profile",
            "diagnosis",
            "reviewed_input",
            "independent_review",
            "completion_attestation_sha256",
        }
        or type(packet.get("schema_version")) is not int
        or packet["schema_version"] != 2
        or packet.get("scope") != "claude_generation_replacement_review"
        or packet.get("profile") != PROFILE
    ):
        raise ValueError("Replacement packet differs from its explicit pin or Claude scope")
    entry = failed["entry"]
    failure_sha = sha256(
        canonical_json(
            {
                "reservation_sha256": entry["reservation_sha256"],
                "preflight_sha256": failed["preflight_sha256"],
                "terminal_sha256": failed["terminal_sha256"],
            }
        )
    )
    links = {}
    locations = {}
    for key in ("diagnosis", "reviewed_input", "independent_review"):
        reference = packet.get(key)
        if (
            not isinstance(reference, dict)
            or set(reference) != {"path", "sha256"}
            or not valid_sha256(reference["sha256"])
        ):
            raise ValueError("Replacement evidence must bind bounded regular files")
        name = reference["path"]
        if not isinstance(name, str) or not name:
            raise ValueError("Replacement evidence needs an explicit file path")
        location = Path(name)
        location = location if location.is_absolute() else path.parent / location
        location = location.absolute()
        if location.resolve(strict=True) != location:
            raise ValueError("Replacement evidence must use canonical original file paths")
        content = regular_bytes(location)
        if sha256(content) != reference["sha256"]:
            raise ValueError("Reviewed replacement evidence changed")
        links[key] = content
        locations[key] = location
    wanted = {
        "schema_version": 1,
        "scope": "claude_generation_replacement_input",
        "profile": PROFILE,
        "failed_ordinal": entry["ordinal"],
        "check": entry["check"],
        "failed_evidence_sha256": failure_sha,
        "diagnosis_sha256": packet["diagnosis"]["sha256"],
        "candidate_sha256": candidate,
    }
    if links["reviewed_input"] != canonical_json(wanted):
        raise ValueError("Reviewed input does not bind the exact failure, diagnosis and current candidate")
    review = evidence_json(links["independent_review"])
    if not isinstance(review, dict) or any(review.get(key) != value for key, value in REVIEW_CORE.items()):
        raise ValueError("Replacement requires the configured native Sol/high SHIP review")
    session = review.get("session_id")
    reservation_id = review.get("review_reservation_id")
    try:
        valid_session = isinstance(session, str) and str(uuid.UUID(session)) == session
        timestamp = datetime.fromisoformat(review["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError, TypeError):
        raise ValueError("Review identity or timestamp is invalid") from None
    if (
        not valid_session
        or timestamp.utcoffset() is None
        or not isinstance(reservation_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reservation_id) is None
        or not isinstance(review.get("review"), str)
    ):
        raise ValueError("Review identity or reviewer output is invalid")
    _review_marker(review["review"], sha256(links["reviewed_input"]))
    completion = completion_proof(
        PROJECT,
        review,
        locations["independent_review"],
        sha256(links["reviewed_input"]),
        packet["completion_attestation_sha256"],
    )
    if completion["original_receipt_sha256"] != sha256(links["independent_review"]):
        raise ValueError("Independent review bytes differ from the original receipt attestation")
    return {
        "packet_sha256": expected,
        "failed_evidence_sha256": failure_sha,
        "candidate_sha256": candidate,
        "diagnosis_sha256": wanted["diagnosis_sha256"],
        "review_sha256": sha256(links["independent_review"]),
        **completion,
    }


def _original_reference(path: Path, expected: str | None = None) -> tuple[dict[str, str], bytes]:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Exception evidence must retain its canonical original absolute path")
    raw = regular_bytes(path)
    checksum = sha256(raw)
    if expected is not None and checksum != expected:
        raise ValueError("Pinned original exception evidence changed")
    return {"path": str(path), "sha256": checksum}, raw


def _prior_submissions(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(history) != 2:
        raise ValueError("The bounded exception requires exactly two original failures")
    _exception_ledger([item["entry"] for item in history])
    rows = []
    for item in history:
        if item["preflight"].get("schema_version") != 1 or item["terminal"] is None:
            raise ValueError("Both original failures need unchanged legacy terminal receipts")
        refs = {}
        for key, name, expected in (
            ("preflight", "preflight.json", item["preflight_sha256"]),
            ("reservation", "submitted.json", item["entry"]["reservation_sha256"]),
            ("receipt", "receipt.json", item["terminal_sha256"]),
        ):
            refs[key], _ = _original_reference(item["directory"] / name, expected)
        rows.append(
            {
                **{key: item["entry"][key] for key in ("ordinal", "check", "passed", "replacement")},
                "candidate_sha256": item["preflight"]["candidate_sha256"],
                **refs,
            }
        )
    return rows


def _exception_marker(text: str, checksum: str) -> None:
    from scripts.verify_native_generation import APPROVAL_MARKER as native_marker

    lines = [line.rstrip("\r") for line in text.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    if (
        text.count(EXCEPTION_MARKER) != 1
        or APPROVAL_MARKER in text
        or native_marker in text
        or text.count("<verdict>") != 1
        or text.count("</verdict>") != 1
        or lines[-3:] != [EXCEPTION_MARKER + checksum, "", "<verdict>SHIP</verdict>"]
        or (len(lines) > 3 and lines[-4].strip())
    ):
        raise ValueError("Task6 must explicitly approve the exact exception input immediately before terminal SHIP")
    fence: tuple[str, int] | None = None
    for line in lines[:-3]:
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
        raise ValueError("The bounded exception approval cannot appear in a code fence")


def bounded_exception_proof(
    path: Path | None, expected: str | None, history: list[dict[str, Any]], candidate: str
) -> dict[str, Any]:
    try:
        return _bounded_exception_proof(path, expected, history, candidate)
    except RecursionError:
        raise ValueError("Bounded exception evidence exceeds its JSON nesting bound") from None


def _bounded_exception_proof(
    path: Path | None, expected: str | None, history: list[dict[str, Any]], candidate: str
) -> dict[str, Any]:
    if path is None or not valid_sha256(expected) or not valid_sha256(candidate):
        raise ValueError("The final text exception needs its pinned independent review")
    reference, raw = _original_reference(path, expected)
    packet = evidence_json(raw)
    if (
        set(packet)
        != {
            "schema_version",
            "scope",
            "profile",
            "diagnosis",
            "reviewed_input",
            "legacy_replacement_review",
            "independent_review",
            "completion_attestation_sha256",
        }
        or type(packet.get("schema_version")) is not int
        or packet["schema_version"] != 1
        or packet.get("scope") != "claude_generation_bounded_exception_review"
        or packet.get("profile") != PROFILE
    ):
        raise ValueError("Unsupported bounded exception proof packet")
    links = {}
    locations = {}
    for key in ("diagnosis", "reviewed_input", "legacy_replacement_review", "independent_review"):
        link = packet[key]
        if (
            not isinstance(link, dict)
            or set(link) != {"path", "sha256"}
            or not isinstance(link.get("path"), str)
            or not valid_sha256(link.get("sha256"))
        ):
            raise ValueError("Exception links must identify pinned original bounded files")
        locations[key] = Path(link["path"])
        _, links[key] = _original_reference(locations[key], link["sha256"])
    prior = _prior_submissions(history)
    legacy_packet = evidence_json(links["legacy_replacement_review"])
    if (
        locations["legacy_replacement_review"] != PROJECT / EXCEPTION_LEGACY_PACKET
        or packet["legacy_replacement_review"]["sha256"] != EXCEPTION_LEGACY_PACKET_SHA256
        or legacy_packet.get("independent_review")
        != {"path": str(PROJECT / EXCEPTION_LEGACY_REVIEW), "sha256": EXCEPTION_LEGACY_REVIEW_SHA256}
    ):
        raise ValueError("The exception must retain the fixed original R5 packet and review bytes")
    legacy = replacement_proof(
        locations["legacy_replacement_review"],
        packet["legacy_replacement_review"]["sha256"],
        history[0],
        history[1]["preflight"]["candidate_sha256"],
    )
    if any(record.get("replacement_review") != legacy for record in (history[1]["preflight"], history[1]["terminal"])):
        raise ValueError("The original R5 replacement authority differs from ordinal2")
    wanted = {
        "schema_version": 1,
        "scope": "claude_generation_bounded_exception_input",
        "profile": PROFILE,
        "policy": EXCEPTION_POLICY,
        "diagnosed_failure_ordinal": 2,
        "prior_submissions": prior,
        "legacy_replacement_review_sha256": packet["legacy_replacement_review"]["sha256"],
        "diagnosis_sha256": packet["diagnosis"]["sha256"],
        "candidate_sha256": candidate,
        "budget": EXCEPTION_BUDGET,
    }
    reviewed_sha = sha256(links["reviewed_input"])
    if links["reviewed_input"] != canonical_json(wanted):
        raise ValueError("Exception input does not bind both original failures, authority and current candidate")
    review = evidence_json(links["independent_review"])
    core = {**REVIEW_CORE, "id": EXCEPTION_REVIEW_TASK}
    if any(review.get(key) != value for key, value in core.items()):
        raise ValueError("The bounded exception needs its genuine configured task6 Sol/high SHIP review")
    try:
        session = review.get("session_id")
        valid_session = isinstance(session, str) and str(uuid.UUID(session)) == session
        timestamp = datetime.fromisoformat(review["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError, TypeError):
        raise ValueError("Exception review identity or timestamp is invalid") from None
    reservation = review.get("review_reservation_id")
    if (
        not valid_session
        or timestamp.utcoffset() is None
        or not isinstance(reservation, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reservation) is None
        or not isinstance(review.get("review"), str)
    ):
        raise ValueError("Exception review identity or reviewer output is invalid")
    _exception_marker(review["review"], reviewed_sha)
    completion = completion_proof(
        PROJECT,
        review,
        locations["independent_review"],
        reviewed_sha,
        packet["completion_attestation_sha256"],
        review_kind="bounded_exception",
    )
    if completion["original_receipt_sha256"] != sha256(links["independent_review"]):
        raise ValueError("Exception review bytes differ from the original conductor-attested receipt")
    return {
        "budget_exception": {"policy": EXCEPTION_POLICY, "proof": reference, "reviewed_input_sha256": reviewed_sha},
        "candidate_sha256": candidate,
        "legacy_replacement_review_sha256": packet["legacy_replacement_review"]["sha256"],
        "diagnosis_sha256": wanted["diagnosis_sha256"],
        **completion,
    }


def submission_authorization(
    history: list[dict[str, Any]],
    check: str,
    candidate: str,
    review: Path | None,
    review_sha256: str | None,
    *,
    exception_review: Path | None = None,
    exception_review_sha256: str | None = None,
) -> dict[str, Any] | None:
    entries = [item["entry"] for item in history]
    supplied_exception = exception_review is not None or exception_review_sha256 is not None
    exception_history = len(history) >= 3 and history[2]["preflight"].get("schema_version") == 2
    if exception_history:
        _exception_ledger(entries)
        if review is not None or review_sha256 is not None or supplied_exception:
            raise ValueError("Exception continuation reopens its original proof and rejects new review arguments")
        binding = _exception_binding(history[2]["preflight"].get("budget_exception"))
        proof = bounded_exception_proof(
            Path(binding["proof"]["path"]), binding["proof"]["sha256"], history[:2], candidate
        )
        if proof["budget_exception"] != binding or any(
            item["preflight"].get("candidate_sha256") != candidate for item in history[2:]
        ):
            raise ValueError("Exception continuation changed its exact approved candidate")
        if len(entries) >= 5 or not entries[-1]["passed"] or check != ("read" if len(entries) == 3 else "cancel"):
            raise ValueError("The bounded exception is exhausted or its preceding gate did not pass")
        return proof
    validate_ledger(entries)
    if supplied_exception:
        if review is not None or review_sha256 is not None or check != "text" or len(history) != 2:
            raise ValueError("The explicit exception can authorize only the final text ordinal3")
        return bounded_exception_proof(exception_review, exception_review_sha256, history, candidate)
    if len(entries) >= MAX_SUBMISSIONS:
        raise ValueError("The four-submission Claude lineage is exhausted")
    previous = [item for item in history if item["entry"]["check"] == check]
    if not previous:
        if review is not None or review_sha256 is not None:
            raise ValueError("Replacement review is only valid for a failed gate")
        return None
    if len(previous) >= 2 or previous[-1]["entry"]["passed"] or any(item["replacement"] for item in entries):
        raise ValueError("A successful gate or spent replacement cannot be resubmitted")
    return replacement_proof(review, review_sha256, previous[-1], candidate)


def _runtime_identity() -> dict[str, Any]:
    _assert_execution_provenance()
    import clink

    imports = _audit_imports()
    package = Path(clink.__file__).resolve(strict=True).parent
    if package != PACKAGE_ROOT / "clink":
        raise ValueError("Imported Clink differs from the selected package root")
    _, checksum = _package_sources(package)
    if checksum != PACKAGE_SOURCE_SHA256:
        raise ValueError("Selected package source changed after bootstrap")
    for name in _HARNESS_SOURCES:
        _checked_source(SOURCE_PROJECT / name)
    python = str(Path(sys.executable).resolve(strict=True))
    return {
        "python_executable": python,
        "python_version": sys.version.split()[0],
        "python_executable_sha256": executable_digest(python),
        "clink_package_directory": str(package),
        "package_source_sha256": checksum,
        "harness_project_directory": str(SOURCE_PROJECT),
        "runtime_source_modules": imports,
    }


def _assert_runtime_identity(binding: dict[str, Any]) -> None:
    observed = _runtime_identity()
    if any(key not in binding or observed[key] != binding[key] for key in RUNTIME_FIELDS):
        raise ValueError("Runtime identity differs from its frozen preflight binding")


def implementation_manifest() -> dict[str, Any]:
    manifest, _ = _package_sources(PACKAGE_ROOT / "clink")
    files = list(manifest["files"])
    for name in _HARNESS_SOURCES:
        files.append({"path": name, "sha256": sha256(_checked_source(SOURCE_PROJECT / name))})
    return {"schema_version": 2, "files": sorted(files, key=lambda item: item["path"])}


def preflight(model: str) -> tuple[str, dict[str, Any]]:
    _assert_execution_provenance()
    parse_native_request(canonical_json(request_body("text", PROJECT.resolve(strict=True), model, 120)))
    if DEFAULT_CLAUDE_NATIVE_ADMISSION.verified:
        raise ValueError("The candidate verifier cannot operate as production admission")
    found = shutil.which("claude")
    if found is None:
        raise ValueError("Installed Claude Code is unavailable")
    executable = str(Path(found).resolve(strict=True))
    binary = executable_digest(executable)
    if binary != RESEARCHED_EXECUTABLE_SHA256:
        raise ValueError("Installed Claude differs from the exact researched binary")
    names = native_environment_names("claude")
    environment = {key: value for key, value in os.environ.items() if key in names}
    code, output = bounded_version(executable, environment)
    if code != 0 or output.strip() != VERSION.encode() or executable_digest(executable) != binary:
        raise ValueError("Claude version or binary changed during preflight")
    if claude_generation_arguments() != (
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-prompts",
        "none",
    ):
        raise ValueError("Claude generation recipe changed its researched arguments")
    manifest = implementation_manifest()
    binding = {
        "executable_sha256": binary,
        "recipe_sha256": claude_generation_recipe_digest(),
        "parser_sha256": parser_contract_digest(),
        "model": model,
        "implementation_source_manifest": manifest,
        "implementation_source_sha256": sha256(canonical_json(manifest)),
        "verifier_sha256": sha256(regular_bytes(Path(__file__))),
        "observed_version": VERSION,
        **_runtime_identity(),
    }
    binding["candidate_sha256"] = _candidate_digest(binding)
    return executable, binding


FIXTURE_HOOK = r'''"""Deny unrelated tools in this synthetic verification project."""
import fcntl
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOKEN = ROOT / "tool-context.txt"
IDENTITY = @TOKEN_IDENTITY@
TOKEN_SHA256 = @TOKEN_SHA256@
NONCE = @NONCE@
SELF_TEST = sys.argv[1:] == ["--self-test"]

def deny():
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
          "permissionDecisionReason": "Outside the synthetic token-read verification scope."}}))

try:
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536 or sys.argv[1:] not in ([], ["--self-test"]):
        raise ValueError()
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError()
    if event.get("hook_event_name") == "SessionStart":
        if not SELF_TEST:
            descriptor = os.open(ROOT / "session-start.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(json.dumps({"event": "SessionStart", "nonce": NONCE}) + "\n")
                output.flush()
                os.fsync(output.fileno())
        print("{}")
    elif event.get("hook_event_name") == "PreToolUse":
        data = event.get("tool_input")
        name = data.get("file_path") if isinstance(data, dict) else None
        allowed = False
        if event.get("tool_name") == "Read" and name == str(TOKEN):
            current = TOKEN.lstat()
            if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == IDENTITY:
                descriptor = os.open(TOKEN, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as source:
                    content = source.read(4097)
                allowed = len(content) <= 4096 and hashlib.sha256(content).hexdigest() == TOKEN_SHA256
        if not SELF_TEST:
            descriptor = os.open(ROOT / "hook-observations.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "a") as output:
                fcntl.flock(output.fileno(), fcntl.LOCK_EX)
                current = os.fstat(output.fileno())
                if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or current.st_size > 16384:
                    raise ValueError()
                output.write(json.dumps({"event": "PreToolUse", "token_read": allowed, "denied": not allowed}) + "\n")
                output.flush()
                os.fsync(output.fileno())
        if allowed:
            print("{}")
        else:
            deny()
    else:
        raise ValueError()
except (OSError, ValueError, TypeError, RecursionError):
    deny()
    raise SystemExit(2)
'''


def _private_identity(path: Path) -> tuple[int, int]:
    current = path.lstat()
    if not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o600 or current.st_nlink != 1:
        raise ValueError("Fixture controls must be private regular files")
    return current.st_dev, current.st_ino


@dataclass(frozen=True)
class _FixtureControls:
    root: Path
    token: str
    nonce: str
    files: dict[str, tuple[tuple[int, int], str]]
    directories: dict[str, tuple[int, int]]
    log_identity: tuple[int, int]
    python_executable: str
    python_executable_sha256: str
    selftest_passed: bool = False

    def validate(self, *, require_fresh: bool = False) -> None:
        if (
            str(Path(sys.executable).resolve(strict=True)) != self.python_executable
            or executable_digest(self.python_executable) != self.python_executable_sha256
        ):
            raise ValueError("Synthetic hook interpreter changed after fixture preparation")
        for name, expected in self.directories.items():
            path = self.root / name
            current = path.lstat()
            if (
                path.resolve(strict=True) != path
                or not stat.S_ISDIR(current.st_mode)
                or (current.st_dev, current.st_ino) != expected
            ):
                raise ValueError("Synthetic fixture directory identity changed")
        for name, (identity, checksum) in self.files.items():
            path = self.root / name
            if _private_identity(path) != identity or sha256(regular_bytes(path)) != checksum:
                raise ValueError("Synthetic fixture control changed")
        if _private_identity(self.root / "hook-observations.jsonl") != self.log_identity:
            raise ValueError("Synthetic hook observation identity changed")
        marker = self.root / "session-start.json"
        if require_fresh and (marker.exists() or marker.is_symlink()):
            raise ValueError("Synthetic SessionStart marker must be fresh before dispatch")

    def summary(self) -> dict[str, Any]:
        return {
            "fixture_root": str(self.root),
            "selftest_passed": self.selftest_passed,
            "permission_grants": False,
            "default_tool_decision": "deny_except_native_token_read_without_grant",
            "files": [
                {"path": name, "sha256": checksum, "device": identity[0], "inode": identity[1]}
                for name, (identity, checksum) in sorted(self.files.items())
            ],
            "session_start_nonce_sha256": sha256(self.nonce.encode()),
            "hook_python_executable": self.python_executable,
            "hook_python_executable_sha256": self.python_executable_sha256,
        }

    def observation(self) -> dict[str, Any]:
        self.validate()
        marker_path = self.root / "session-start.json"
        started = False
        if marker_path.exists() or marker_path.is_symlink():
            _private_identity(marker_path)
            marker, _ = regular_json(marker_path)
            if marker != {"event": "SessionStart", "nonce": self.nonce}:
                raise ValueError("Synthetic SessionStart observation is invalid")
            started = True
        raw = regular_bytes(self.root / "hook-observations.jsonl")
        if len(raw) > 32768:
            raise ValueError("Synthetic hook observations exceeded their bound")
        lines = raw.splitlines()
        if len(lines) > 256:
            raise ValueError("Synthetic hook observations exceeded their count bound")
        reads = denials = 0
        for line in lines:
            event = evidence_json(line)
            if (
                set(event) != {"event", "token_read", "denied"}
                or event["event"] != "PreToolUse"
                or type(event["token_read"]) is not bool
                or type(event["denied"]) is not bool
                or event["token_read"] == event["denied"]
            ):
                raise ValueError("Synthetic hook observation is malformed")
            reads += int(event["token_read"])
            denials += int(event["denied"])
        return {
            "session_start_observed": started,
            "token_read_observations": reads,
            "tool_denials": denials,
            "controls_unchanged": True,
        }


def create_fixture(workspace: Path) -> _FixtureControls:
    python = str(Path(sys.executable).resolve(strict=True))
    python_sha256 = executable_digest(python)
    workspace.mkdir(mode=0o700)
    if workspace.resolve(strict=True) != workspace:
        raise ValueError("Synthetic fixture must use a canonical directory")
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=" + os.devnull, "init", "-q", str(workspace)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    (workspace / ".claude").mkdir(mode=0o700)
    token = "CLAUDE_GENERATION_READ_" + uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    target = workspace / "tool-context.txt"
    target.write_text(token + "\n")
    target.chmod(0o600)
    current = target.stat()
    hook = FIXTURE_HOOK.replace("@TOKEN_IDENTITY@", repr((current.st_dev, current.st_ino)))
    hook = hook.replace("@TOKEN_SHA256@", repr(sha256(target.read_bytes()))).replace("@NONCE@", repr(nonce))
    (workspace / "scope_hook.py").write_text(hook)
    (workspace / "scope_hook.py").chmod(0o600)
    command = shlex.quote(python) + " " + shlex.quote(str(workspace / "scope_hook.py"))
    settings = {
        "hooks": {
            name: [{"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 5}]}]
            for name in ("SessionStart", "PreToolUse")
        }
    }
    (workspace / ".claude/settings.json").write_text(json.dumps(settings))
    (workspace / ".claude/settings.json").chmod(0o600)
    (workspace / "hook-observations.jsonl").touch(mode=0o600, exist_ok=False)
    controls = _FixtureControls(
        workspace,
        token,
        nonce,
        {
            name: (_private_identity(workspace / name), sha256(regular_bytes(workspace / name)))
            for name in ("tool-context.txt", "scope_hook.py", ".claude/settings.json")
        },
        {
            name: ((workspace / name).stat().st_dev, (workspace / name).stat().st_ino)
            for name in (".", ".claude", ".git")
        },
        _private_identity(workspace / "hook-observations.jsonl"),
        python,
        python_sha256,
    )
    for event, allowed in (
        ({"hook_event_name": "SessionStart"}, True),
        *[
            ({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"file_path": path}}, allowed)
            for tool, path, allowed in (
                ("Read", str(target), True),
                ("Read", "tool-context.txt", False),
                ("Read", str(workspace.parent / "outside.txt"), False),
                ("Write", str(target), False),
                ("Bash", str(target), False),
                ("Agent", str(target), False),
                ("mcp__unrelated__write", str(target), False),
            )
        ],
    ):
        tested = subprocess.run(
            [python, str(workspace / "scope_hook.py"), "--self-test"],
            input=canonical_json(event),
            capture_output=True,
            timeout=5,
            check=True,
        )
        result = evidence_json(tested.stdout)
        decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
        if (allowed and decision is not None) or (not allowed and decision != "deny"):
            raise ValueError("Synthetic restrictive hook selftest failed")
    controls.validate(require_fresh=True)
    if regular_bytes(workspace / "hook-observations.jsonl"):
        raise ValueError("Offline selftests must not create native hook observations")
    return replace(controls, selftest_passed=True)


def hook_policy(workspace: Path) -> dict[str, Any]:
    home = Path(os.environ.get("HOME") or str(Path.home()))
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(home / ".claude"))
    if not config.is_absolute():
        raise ValueError("Native config metadata location must be absolute")
    managed = Path("/Library/Application Support/ClaudeCode") if sys.platform == "darwin" else Path("/etc/claude-code")
    sources = [
        ("user", config / "settings.json"),
        ("managed", managed / "managed-settings.json"),
        ("project", workspace / ".claude/settings.json"),
        ("local", workspace / ".claude/settings.local.json"),
    ]
    dropins = managed / "managed-settings.d"
    if dropins.is_symlink():
        raise ValueError("Managed hook metadata directory is unavailable")
    if dropins.exists():
        extra = sorted(dropins.glob("*.json"))
        if len(extra) > 64:
            raise ValueError("Managed hook metadata exceeds its file-count bound")
        sources.extend(("managed_dropin", path) for path in extra)

    def reject_constant(_value: str) -> None:
        raise ValueError("Native hook metadata must use finite JSON values")

    observations = []
    for scope, path in sources:
        if not path.exists() and not path.is_symlink():
            observations.append({"scope": scope, "present": False})
            continue
        value, raw = regular_json(path)
        json.loads(raw, parse_constant=reject_constant)
        for name in ("disableAllHooks", "allowManagedHooksOnly"):
            if name in value and type(value[name]) is not bool:
                raise ValueError("Native hook availability metadata is malformed")
            if value.get(name) is True:
                raise ValueError("Native policy disables the required synthetic project hooks")
        observations.append(
            {
                "scope": scope,
                "present": True,
                "sha256": sha256(raw),
                "disable_all_hooks": False,
                "managed_hooks_only": False,
            }
        )
    return {
        "known_local_hook_policy_available": True,
        "sources": observations,
        "auth_stores_read": False,
        "effective_remote_or_mdm_policy_observed": False,
    }


class ObservedPool(ProcessPool):
    """Observe bounded event labels while retaining production supervision."""

    def __init__(
        self,
        controls: _FixtureControls | None = None,
        policy: dict[str, Any] | None = None,
        *,
        runtime_identity: dict[str, Any],
    ) -> None:
        super().__init__(maximum=1)
        self.controls = controls
        self.policy = policy
        self.runtime_identity = {key: runtime_identity.get(key) for key in RUNTIME_FIELDS}
        self.activity = asyncio.Event()
        self.native_terminal_observed = False
        self.dispatches = self.spawns = 0
        self.cleanup_confirmed = True
        self.event_counts = dict.fromkeys(("system", "assistant", "user", "result", "rate_limit_event", "error"), 0)
        self.read_uses = 0

    async def _read(self, stream: asyncio.StreamReader) -> bytes:
        if not any(process.stdout is stream for process in self._children):
            return await super()._read(stream)
        owner = self
        pending = bytearray()

        class Reader:
            async def read(self, size: int) -> bytes:
                data = await stream.read(size)
                pending.extend(data)
                if len(pending) > STREAM_BYTES:
                    raise ValueError("Observed stream exceeded its capture bound")
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError):
                        continue
                    if isinstance(event, dict) and event.get("type") in owner.event_counts:
                        kind = event["type"]
                        owner.event_counts[kind] += 1
                        if kind in {"result", "error"}:
                            owner.native_terminal_observed = True
                        if kind == "assistant":
                            owner.activity.set()
                            message = event.get("message")
                            blocks = message.get("content", []) if isinstance(message, dict) else []
                            if isinstance(blocks, list):
                                owner.read_uses += sum(
                                    isinstance(block, dict)
                                    and block.get("type") == "tool_use"
                                    and block.get("name") == "Read"
                                    for block in blocks
                                )
                return data

        return await super()._read(Reader())

    async def execute(self, command: tuple[str, ...], **kwargs: Any) -> Any:
        if self.dispatches:
            raise ValueError("Only one dispatch is allowed per verifier invocation")
        self.dispatches += 1
        original = kwargs.get("on_started")

        def started() -> None:
            self.spawns += 1
            if original is not None:
                original()

        original_prelaunch = kwargs.get("prelaunch")

        def guarded() -> None:
            _assert_runtime_identity(self.runtime_identity)
            if self.controls is not None:
                self.controls.validate(require_fresh=True)
                if self.policy is not None and hook_policy(self.controls.root) != self.policy:
                    raise ValueError("Native hook policy metadata changed before dispatch")
            if original_prelaunch is not None:
                original_prelaunch()

        return await super().execute(command, **dict(kwargs, on_started=started, prelaunch=guarded))

    async def _cleanup(self, process: asyncio.subprocess.Process) -> None:
        await super()._cleanup(process)
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        self.cleanup_confirmed = False


def request_body(check: str, workspace: Path, model: str, timeout: int) -> dict[str, Any]:
    instructions = "Improve the rough prompt. Preserve Atlas, Mira, Friday, exactly two risks, rollback and acceptance checks. Return only the improved prompt."
    content = "Need plan to move Atlas cache Friday. Mira owns it. Need two risks, rollback, checks."
    if check == "read":
        instructions = "Read tool-context.txt using your native permitted Read tool and return exactly its one-line text. Do not modify files. If native permissions prevent reading, report that limitation."
        content = (
            "The required source is tool-context.txt in the selected project; its text is not supplied in this request."
        )
    elif check == "cancel":
        instructions = "First read tool-context.txt using your native permitted Read tool, then develop a very detailed engineering migration plan with staged rollout, failure analysis, rollback and acceptance checks. Do not change files or use external services."
        content = "Plan an Atlas cache migration with extensive risk analysis."
    return {
        "schema_version": 2,
        "execution_mode": "native",
        "project_dir": str(workspace),
        "request_id": str(uuid.uuid4()),
        "cli": "claude",
        "model": model,
        "instructions": instructions,
        "input": content,
        "timeout_seconds": timeout,
    }


async def run_check(
    check: str, executable: str, workspace: Path, binding: dict[str, Any], preflight_path: Path, timeout: int
) -> dict[str, Any]:
    _assert_execution_provenance()
    with _source_imports():
        return await _run_check(check, executable, workspace, binding, preflight_path, timeout)


async def _run_check(
    check: str, executable: str, workspace: Path, binding: dict[str, Any], preflight_path: Path, timeout: int
) -> dict[str, Any]:
    controls = binding.get("_fixture_controls")
    if controls is None:
        controls = create_fixture(workspace)
    controls.validate(require_fresh=True)
    token = controls.token
    client = ResolvedCLIClient(
        name="claude",
        adapter="claude",
        executable=(executable,),
        working_dir=workspace,
        allowed_roots=(workspace,),
        allowed_roots_explicit=True,
        configuration_scope="explicit",
        timeout_seconds=180,
        parser="claude_native_generation",
        roles={},
    )

    class Registry:
        project_dir = None

        def get_client(self, name: str) -> ResolvedCLIClient:
            if name != "claude":
                raise KeyError(name)
            return client

    pool = ObservedPool(controls, binding.get("native_hook_policy"), runtime_identity=binding)
    service = NativeGenerationService(
        Registry(),
        pool,
        claude_admission=NativeGenerationAdmission(
            version=VERSION,
            verified=True,
            executable_sha256=binding["executable_sha256"],
            recipe_sha256=binding["recipe_sha256"],
            parser_sha256=binding["parser_sha256"],
            evidence_path=str(preflight_path),
            evidence_sha256=sha256(regular_bytes(preflight_path)),
        ),
    )
    request = parse_native_request(canonical_json(request_body(check, workspace, binding["model"], timeout)))
    task = asyncio.create_task(service.run(request))
    activity = False
    waiting = None
    try:
        if check == "cancel":

            async def activity_with_hook() -> bool:
                await pool.activity.wait()
                return controls.observation()["session_start_observed"] and not pool.native_terminal_observed

            waiting = asyncio.create_task(activity_with_hook())
            done, _ = await asyncio.wait(
                {task, waiting}, timeout=max(0, timeout - 5), return_when=asyncio.FIRST_COMPLETED
            )
            activity = waiting in done and waiting.result() is True
            if not task.done() and not pool.native_terminal_observed:
                task.cancel()
        result = await asyncio.wait_for(asyncio.shield(task), timeout + 10)
    finally:
        if waiting is not None:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        if not task.done():
            task.cancel()
        await pool.shutdown()
        await asyncio.gather(task, return_exceptions=True)
    output = result.get("output")
    execution = result["execution"]
    hooks = controls.observation()
    cleanup = pool.cleanup_confirmed and pool.running == 0 and not pool._children
    identity = (
        execution.get("cli") == "claude"
        and execution.get("profile") == PROFILE
        and execution.get("project_dir") == str(workspace)
        and execution.get("admission") == "verified"
        and execution.get("permission_observability") == "limited"
        and execution.get("fulfillment_verified") is False
        and execution.get("side_effects_possible") is True
        and execution.get("catalog_sha256") is None
        and execution.get("requested_model") == execution.get("effective_model") == binding["model"]
        and all(execution.get(key) == binding[key] for key in ("executable_sha256", "recipe_sha256"))
    )
    completed = result["status"] == "completed" and result["error"] is None and result["truncated"] is False
    useful = (
        isinstance(output, str)
        and len(output.encode()) >= 100
        and all(word in output.lower() for word in ("atlas", "mira", "friday", "risk", "rollback", "check"))
        and re.search(r"\b(?:two|2)\b.{0,24}\brisks?\b", output.lower()) is not None
    )
    passed = (
        cleanup
        and identity
        and pool.dispatches == pool.spawns == 1
        and hooks["session_start_observed"]
        and hooks["tool_denials"] == 0
    )
    if check == "cancel":
        passed = (
            passed
            and activity
            and not pool.native_terminal_observed
            and result["status"] == "cancelled"
            and output is None
        )
    else:
        passed = passed and completed and execution.get("actual_model") == binding["model"]
        passed = passed and (
            useful
            if check == "text"
            else isinstance(output, str)
            and output.strip() == token
            and pool.read_uses > 0
            and hooks["token_read_observations"] > 0
        )
    return {
        "check": check,
        "passed": bool(passed),
        "terminal_status": safe_identifier(result["status"]),
        "error_code": safe_identifier((result.get("error") or {}).get("code")),
        "parser_diagnostic": protocol_diagnostic((result.get("error") or {}).get("message")),
        "actual_model": safe_identifier(execution.get("actual_model")),
        "production_service_used": True,
        "assistant_activity_before_cancellation": activity if check == "cancel" else None,
        "native_terminal_observed": pool.native_terminal_observed,
        "useful_transformation": useful if check == "text" else None,
        "unprovided_file_token_matches": (
            output.strip() == token if isinstance(output, str) and check == "read" else None
        ),
        "read_tool_uses": pool.read_uses,
        "fixture_hook_observations": hooks,
        "dispatch_count": pool.dispatches,
        "spawn_count": pool.spawns,
        "event_counts": pool.event_counts,
        "process_groups_absent_after_cleanup": pool.cleanup_confirmed,
        "pool_running_after_shutdown": pool.running,
        "pool_children_after_shutdown": len(pool._children),
        "output_sha256": sha256(output.encode()) if isinstance(output, str) else None,
        "output_bytes": len(output.encode()) if isinstance(output, str) else 0,
        "raw_output_retained": False,
    }


async def _verify(args: argparse.Namespace) -> dict[str, Any]:
    if args.check not in CHECKS or not 1 <= args.timeout <= 180:
        raise ValueError("Select one bounded Claude generation gate")
    if args.output_dir.absolute() != LINEAGE_ROOT or args.output_dir.resolve() != LINEAGE_ROOT:
        raise ValueError("Claude generation uses its fixed durable lineage root")
    history = authoritative_history()
    ledger = [dict(item["entry"]) for item in history]
    target = evidence_directory(LINEAGE_ROOT, args.label)
    record = {
        "schema_version": 1,
        "scope": SCOPE,
        "profile": PROFILE,
        "cli": "claude",
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
        "automatic_retries": 0,
        "production_admission_changed": False,
        "native_task_admission_reused": False,
        "host_configuration_changed": False,
        "native_trust_enrolled": False,
        "raw_output_retained": False,
        "native_sessions_recorded": False,
        "environment_values_recorded": False,
        "prompts_recorded": False,
        "limitations": LIMITATIONS,
    }
    if len(history) >= 3 and history[2]["preflight"]["schema_version"] == 2:
        record["budget_exception"] = history[2]["preflight"]["budget_exception"]
        _exception_limits(record)
    preflight_path = target / "preflight.json"
    try:
        legacy_arguments = args.replacement_review is not None or args.replacement_review_sha256 is not None
        exception_arguments = args.exception_review is not None or args.exception_review_sha256 is not None
        if legacy_arguments and exception_arguments:
            raise ValueError("Legacy and bounded exception review arguments are mutually exclusive")
        if record["schema_version"] == 2 and (legacy_arguments or exception_arguments):
            raise ValueError("Exception continuation rejects new review arguments, including nonlive preparation")
        record["preflight_phase"] = "fixture_preparation"
        fixture = PROJECT / "test_output/claude-generation" / args.label
        fixture.mkdir(mode=0o700, parents=True)
        controls = create_fixture(fixture / "workspace")
        record["preflight_phase"] = "native_hook_metadata"
        policy = hook_policy(controls.root)
        record["preflight_phase"] = "native_identity"
        executable, binding = preflight(args.model)
        controls.validate(require_fresh=True)
        record.update(binding)
        record["fixture_controls"] = controls.summary()
        record["native_hook_policy"] = policy
        record["preflight_phase"] = "replacement_authorization"
        replacement = (
            submission_authorization(
                history,
                args.check,
                binding["candidate_sha256"],
                args.replacement_review,
                args.replacement_review_sha256,
                exception_review=args.exception_review,
                exception_review_sha256=args.exception_review_sha256,
            )
            if args.live
            else None
        )
        exception = replacement is not None and "budget_exception" in replacement
        if exception:
            record["budget_exception"] = replacement["budget_exception"]
            _exception_limits(record)
        record["replacement_review"] = replacement if not exception else None
        is_replacement = replacement is not None and (not exception or len(ledger) == 2)
        record["preflight_phase"] = "ready"
        persist(preflight_path, record)
        if args.live:
            reservation = {
                "schema_version": record["schema_version"],
                "scope": SCOPE,
                "profile": PROFILE,
                "cli": "claude",
                "ordinal": len(ledger) + 1,
                "check": args.check,
                "replacement": is_replacement,
                "candidate_sha256": binding["candidate_sha256"],
                "state": "consumed_before_dispatch",
                "preflight_sha256": sha256(regular_bytes(preflight_path)),
            }
            if exception:
                reservation["budget_exception"] = record["budget_exception"]
            reservation_sha = persist(target / "submitted.json", reservation)
            entry = {
                "ordinal": reservation["ordinal"],
                "check": args.check,
                "passed": False,
                "replacement": is_replacement,
                "reservation_sha256": reservation_sha,
            }
            ledger.append(entry)
            record.update(live_submitted=True, submissions_consumed_total=len(ledger), submissions_in_this_invocation=1)
            _assert_runtime_identity(binding)
            record["observation"] = await run_check(
                args.check,
                executable,
                fixture / "workspace",
                {**binding, "_fixture_controls": controls, "native_hook_policy": policy},
                preflight_path,
                args.timeout,
            )
            entry["passed"] = record["gate_passed"] = record["observation"]["passed"]
    except asyncio.CancelledError:
        record["error_class"] = "CancelledError"
        raise
    except (OSError, RuntimeError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        record["error_class"] = type(error).__name__
    finally:
        passed = {
            item["entry"]["check"]
            for item in history
            if item["entry"]["passed"]
            and (
                item["preflight"].get("candidate_sha256") == record.get("candidate_sha256")
                if record["schema_version"] == 2
                else all(item["preflight"].get(key) == record.get(key) for key in EXECUTION_FIELDS)
            )
        }
        if record["gate_passed"]:
            passed.add(args.check)
        record["all_claude_generation_gates_passed"] = set(CHECKS) <= passed
        if not preflight_path.exists():
            persist(preflight_path, record)
        persist(target / "receipt.json", record)
    return record


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    _assert_execution_provenance()
    refuse_live_verification(args.live, PROJECT)
    directory = PROJECT / "test_output/claude-generation"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.resolve(strict=True) != directory:
        raise ValueError("Verifier lock directory must be canonical")
    descriptor = os.open(directory / ".verification.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Verifier lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another Claude verifier owns this lineage") from None
        with _source_imports():
            return await _verify(args)
    finally:
        os.close(descriptor)


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--package-root", type=Path, help="Canonical root containing staged Clink source; explicit pin required"
    )
    parser.add_argument("--package-source-sha256", help="SHA-256 of canonical package source manifest")
    parser.add_argument("--check", choices=CHECKS, default="text")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Full native model identifier; no aliases")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", type=Path, default=LINEAGE_ROOT)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Unavailable in public snapshot; continue only with the original private verifier and history",
    )
    parser.add_argument("--replacement-review", type=Path)
    parser.add_argument("--replacement-review-sha256")
    parser.add_argument(
        "--exception-review", type=Path, help="Pinned task6 exception proof, only for final text ordinal3"
    )
    parser.add_argument("--exception-review-sha256")
    return parser


async def supervised_verify(args: argparse.Namespace) -> dict[str, Any]:
    task = asyncio.create_task(verify(args))
    loop = asyncio.get_running_loop()
    cancelled = False

    def cancel_once() -> None:
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            task.cancel()

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, cancel_once)
    try:
        return await task
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


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
        parser.error("Claude generation verification refused: " + type(error).__name__)
    print(
        json.dumps(
            {
                "check": args.check,
                "live_submitted": record["live_submitted"],
                "gate_passed": record["gate_passed"],
                "submissions_consumed_total": record["submissions_consumed_total"],
                "receipt": str(LINEAGE_ROOT / args.label / "receipt.json"),
            }
        )
    )
    return 0 if record["gate_passed"] or (not args.live and "error_class" not in record) else 2


if __name__ == "__main__":
    raise SystemExit(main())
