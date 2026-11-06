"""Check the bounded observation projection; genuine review authority stays external."""

import copy
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

import clink.native_generation as runtime
from clink.contracts import BackendError
from clink.generation_contracts import OUTPUT_BYTES, canonical_json, sha256
from clink.native_generation import NativeGenerationService
from clink.native_generation_families import family_generation_parser_digest, family_generation_recipe_digest
from clink.native_generation_family_policy import DEFAULT_AGY_NATIVE_ADMISSION, DEFAULT_GROK_NATIVE_ADMISSION
from clink.native_generation_policy import NativeGenerationAdmission
from clink.parsers.base import MAX_METADATA_NUMBER

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = "docs/verification/family-generation/grok"
RESOURCE = ROOT / "clink/resources/native-generation-grok-1.0.70-admission.json"
PROOFS = f"{DIRECTORY}/reconciliation"
CHECKS = ("text", "action", "permission", "cancel")
ORDINALS = (1, 2, 3, 5)
ORIGINAL_COMMIT = "e9feea12fcdaafbdef91f570a924f166563409bc"
CORRECTED_COMMIT = "ab01466c84ba3661e8807b54d26d53b78c94e3bf"
PACKET_SHA256 = "6fc0d00bb6c1624359d409a6afb61fd266a445bbbdee27d4475ac41a93e67d36"
AGY_DIRECTORY = "docs/verification/family-generation/agy"
AGY_PROOFS = f"{AGY_DIRECTORY}/reconciliation"
AGY_RESOURCE_PATH = f"{AGY_DIRECTORY}/native-generation-agy-1.4.12-admission.json"
DIAGNOSTIC_COMMIT = "91681e6a9cfd44e07563f15cd0ff8d90130eb7f5"
AGY_PACKET_PINS = (
    "416dcd22c5ba6056e9f286e155bc83cc4b253c88baa4929b593e4459e9d4aac9",
    "f4762df81a039b98c560d85325406e3de13bb060d5b5f98ef9436aa6ad4d4f40",
)
SIXTH_PINS = {
    "preflight": "085f3f241de2f4bc95b6a6bd1161a69323162822e88e856502202cd8b5895275",
    "reservation": "dd745686c35d9dd78a62bd5645ffd5b23c5336e3ff47927ca96eb85b643896db",
    "receipt": "e1414f5f4c51efa4c9b0646a9ab49710bc1c5f031f7799602fcd60c781bc249f",
}
AGY_SIDECAR_SHA256 = "3a56905669b70a26e3831a4593661ad1356c386faa6c3beb07876903bc4286f7"
AGY_EQUIVALENCE_SHA256 = "3374c067f5a97496a0944e8defb042da9f8f7f0d985986407142e0b1613e2a78"
AGY_INTERPRETATION_SHA256 = "83574820a659db52a75bc803fb584e9eb8cdd0b0d1a8ed80befdac7679c09cb7"
GROK_HELPER_EQUIVALENCE_SHA256 = "3fc324ec6c1e71f5bfae11af02acc15386ead2fbf70e59aafe08ffb381ead4d6"
GROK_NONLIVE_GUARD_EQUIVALENCE_SHA256 = "cf1261f213e57d213f81fcc862532d8b21c0f0bc62d6e5af2380490349c32cbb"
FIFTH_PINS = {
    "grok": {
        "preflight": "1180ff2121d4b42d643ea64f351d7e614739fda65b9b2927c941d5d1f1d87979",
        "reservation": "01af37d7d9255119654ed6ab93571270e5674e1a54fdea766b8b01a9c2b22f1d",
        "receipt": "e39c3509413188807f5ca045143f9e841ab52c8440c272e9d6e32691c3862a28",
    },
    "agy": {
        "preflight": "3d7bd14af8c1f92030bbba81268f5966260287510ac7f6f7fbbd97bcd6a79a5c",
        "reservation": "37e3da650203260c7a456990af12e22b197f7e55dfa4495d9619a02eaa9db106",
        "receipt": "6e6f91d54380408261214ae2ef5cda22751f98d550512299512b9f369a78fb8c",
    },
}
TOP_FIELDS = {
    "schema_version",
    "scope",
    "profile",
    "adapter",
    "observed_version",
    "executable_sha256",
    "recipe_sha256",
    "parser_sha256",
    "tested_candidates",
    "budget",
    "submissions",
    "gates",
    "replacement_authorizations",
    "reconciliation",
    "limitations",
}
OBSERVATION_FIELDS = {
    "actual_model",
    "actual_model_origin",
    "check",
    "dispatch_count",
    "error_code",
    "identity_bound",
    "observations",
    "output_bytes",
    "output_sha256",
    "passed",
    "pool_running_after_shutdown",
    "private_input_removed",
    "process_groups_absent_after_cleanup",
    "production_service_used",
    "raw_output_retained",
    "spawn_count",
    "terminal_status",
}
DIAGNOSTIC_FIELDS = {"pool_exception_diagnostic_capture_failed", "pool_exception_observation"}
GATE_FIELDS = {
    "check",
    "ordinal",
    "fixture_trust_requested",
    "fixture_trust",
    "host_configuration_changed",
    "host_configuration_restored",
    "observation",
}
DETAILS = (
    {"fixture_configuration_intact", "useful_transformation"},
    {"arithmetic_fixed", "fixture_configuration_intact", "independent_fixture_test_passed"},
    {"fixture_configuration_intact", "protected_file_untouched"},
    {
        "fixture_configuration_intact",
        "partial_effect_observed",
        "synthetic_child_absent",
        "synthetic_child_identity_observed",
        "synthetic_child_ready",
    },
)
LIMITATIONS = {
    "candidate_only_live_coverage": True,
    "permission_observability": "limited",
    "fulfillment_verified": False,
    "side_effects_possible": True,
    "cancellation_rolls_back_effects": False,
    "detached_or_remote_cleanup_verified": False,
    "native_internal_work_bounded_by_top_level_budget": False,
    "native_authentication_independently_verified": False,
    "native_task_admission_reused": False,
    "native_default_identifies_actual_model": False,
}
AGY_LIMITATIONS = {
    **LIMITATIONS,
    "prior_cleanup_failure_cause_known": False,
    "prior_cleanup_failure_resolved": False,
    "diagnostic_child_identity_known": False,
}


def _fields(value, expected):
    assert type(value) is dict and set(value) == set(expected)


def _same(actual, expected):
    """JSON equality must not treat bools as counts or replace historical shapes."""
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        _fields(actual, expected)
        for key, value in expected.items():
            _same(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            _same(left, right)
    else:
        assert actual == expected


def _count(value, maximum=MAX_METADATA_NUMBER):
    assert type(value) is int and 0 <= value <= maximum


def _digest(value):
    assert isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _reference(value):
    _fields(value, {"path", "sha256"})
    path = value["path"]
    assert isinstance(path, str) and 0 < len(path) <= 512 and path.isascii()
    assert not any(ord(char) < 32 or ord(char) == 127 for char in path) and "\\" not in path and ":" not in path
    assert not PurePosixPath(path).is_absolute() and not path.startswith("-")
    assert all(part not in {"", ".", ".."} for part in path.split("/"))
    _digest(value["sha256"])


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate observation field")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("Nonfinite observation value")


def _load(content):
    assert len(content) <= 65536
    return json.loads(content.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)


def _copy_mutable_evidence(source, destination, *, dirs_exist_ok=False):
    """Mutate disposable copies while preserving the immutable archive's permissions."""
    shutil.copytree(source, destination, copy_function=shutil.copyfile, dirs_exist_ok=dirs_exist_ok)
    destination.chmod(0o700)
    for path in destination.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _pinned(root, filename, expected):
    content = (root / filename).read_bytes()
    assert sha256(content) == expected
    return _load(content)


def _proofs(root):
    """Read exact technical copies without reinterpreting native review/session bodies."""
    packet = _pinned(root, f"{PROOFS}/replacement-review.json", PACKET_SHA256)
    compatibility = _pinned(root, f"{PROOFS}/compatibility.json", packet["compatibility_binding"]["sha256"])
    binding = _pinned(root, f"{PROOFS}/source-binding.json", packet["source_binding"]["sha256"])
    reviewed = _pinned(root, f"{PROOFS}/reviewed-input.json", packet["reviewed_input"]["sha256"])
    runtime_binding = _pinned(root, f"{PROOFS}/runtime-attestation.json", packet["runtime_attestation"]["sha256"])
    equivalence = _pinned(
        root, f"{PROOFS}/production-equivalence.json", compatibility["production_equivalence"]["sha256"]
    )
    for field, filename in (
        ("observer_counterexample", "observer-counterexample.json"),
        ("non_cancellation_proof", "non-cancellation-proof.md"),
    ):
        assert sha256((root / PROOFS / filename).read_bytes()) == compatibility[field]["sha256"]
    for reference in equivalence["tests"]:
        filename = Path(reference["path"]).name
        if filename == "historical-cleanup-proof.json":
            filename = "observer-counterexample.json"
        assert sha256((root / PROOFS / filename).read_bytes()) == reference["sha256"]
    assert reviewed["source_revision"] == {key: binding[key] for key in ("base_commit", "head_commit", "diff_sha256")}
    assert binding["base_commit"] == ORIGINAL_COMMIT and binding["head_commit"] == CORRECTED_COMMIT
    assert binding["receipt_sha256"] == packet["independent_review"]["sha256"]
    assert binding["runtime_attestation_sha256"] == packet["runtime_attestation"]["sha256"]
    assert runtime_binding["receipt_sha256"] == binding["receipt_sha256"]
    assert reviewed["compatibility_binding_sha256"] == packet["compatibility_binding"]["sha256"]
    assert reviewed["candidate_sha256"] == binding["candidate_sha256"]
    assert packet["family"] == compatibility["family"] == reviewed["family"] == "grok"
    assert packet["replacement_basis"] == compatibility["replacement_basis"] == "invalidated_cleanup_evidence"
    assert [item["retained_eligible"] for item in compatibility["original_records"]] == [True, True, True, False]
    assert [item["reported_passed"] for item in compatibility["original_records"]] == [True] * 4
    return packet, compatibility, binding, equivalence


def _manifests(equivalence, candidates):
    manifests = []
    for name, candidate in zip(("original", "corrected"), candidates, strict=True):
        manifest = equivalence[f"{name}_components"]
        _fields(manifest, {"schema_version", "files"})
        assert type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
        assert isinstance(manifest["files"], list) and len(manifest["files"]) == 62
        assert sha256(canonical_json(manifest)) == candidate["implementation_source_sha256"]
        assert equivalence[f"{name}_implementation_source_sha256"] == candidate["implementation_source_sha256"]
        components = {}
        for item in manifest["files"]:
            _fields(item, {"path", "sha256"})
            _digest(item["sha256"])
            assert isinstance(item["path"], str) and re.fullmatch(
                r"(?:clink|scripts)/[A-Za-z0-9_./-]+\.py", item["path"]
            )
            assert ".." not in PurePosixPath(item["path"]).parts and item["path"] not in components
            components[item["path"]] = item["sha256"]
        assert components["scripts/verify_family_generation.py"] == candidate["verifier_sha256"]
        manifests.append(components)
    original, corrected = manifests
    assert original.keys() == corrected.keys()
    delta = [
        {"path": path, "original_sha256": original[path], "corrected_sha256": corrected[path]}
        for path in sorted(original)
        if original[path] != corrected[path]
    ]
    _same(equivalence["delta"], delta)
    assert [item["path"] for item in delta] == ["scripts/verify_family_generation.py"]


def _retained(record, root, packet, compatibility, binding):
    history = []
    terminals = []
    for index, item in enumerate(record["submissions"]):
        evidence = {
            name: _pinned(root, item[name]["path"], item[name]["sha256"])
            for name in ("preflight", "reservation", "receipt")
        }
        preflight, reservation, receipt = (evidence[name] for name in ("preflight", "reservation", "receipt"))
        expected_pins = compatibility["original_records"][index] if index < 4 else FIFTH_PINS["grok"]
        for name in ("preflight", "reservation", "receipt"):
            pin = expected_pins["terminal" if name == "receipt" else name] if index < 4 else expected_pins[name]
            assert item[name]["sha256"] == (pin["sha256"] if isinstance(pin, dict) else pin)
        assert reservation["preflight_sha256"] == item["preflight"]["sha256"]
        assert reservation["state"] == "consumed_before_dispatch"
        _same(preflight["submission_ledger"], history)
        assert preflight["live_submitted"] is False and type(preflight["submissions_in_this_invocation"]) is int
        assert preflight["submissions_in_this_invocation"] == 0
        entry = {key: item[key] for key in ("ordinal", "check", "passed", "replacement")}
        entry["reservation_sha256"] = item["reservation"]["sha256"]
        if index == 4:
            entry["replacement_basis"] = item["replacement_basis"]
            assert reservation["replacement_basis"] == packet["replacement_basis"]
        history.append(entry)
        _same(receipt["submission_ledger"], history)
        assert receipt["submissions_consumed_total"] == index + 1
        assert receipt["live_submitted"] is receipt["gate_passed"] is True
        for key in ("ordinal", "check", "replacement"):
            _same(reservation[key], item[key])
        candidate = record["tested_candidates"][int(index == 4)]
        for key in ("candidate_sha256", "implementation_source_sha256"):
            assert preflight[key] == receipt[key] == candidate[key]
        for key in ("fixture_config_sha256", "fixture_project_sha256"):
            assert preflight[key] == receipt[key] == item[key]
        for key in ("observed_version", "executable_sha256", "recipe_sha256", "parser_sha256"):
            assert preflight[key] == receipt[key] == record[key]
        assert receipt["all_family_generation_gates_passed"] is (index == 3)
        assert receipt["production_admission_changed"] is receipt["native_task_admission_changed"] is False
        assert (
            receipt["raw_output_retained"]
            is receipt["prompts_recorded"]
            is receipt["native_sessions_recorded"]
            is False
        )
        terminals.append(receipt)
    summary = terminals[-1]["replacement_review"]
    _same(summary, evidence["preflight"]["replacement_review"])
    for summary_key, packet_key in (
        ("packet_sha256", None),
        ("review_sha256", "independent_review"),
        ("source_binding_sha256", "source_binding"),
        ("runtime_attestation_sha256", "runtime_attestation"),
        ("compatibility_binding_sha256", "compatibility_binding"),
    ):
        assert summary[summary_key] == (PACKET_SHA256 if packet_key is None else packet[packet_key]["sha256"])
    assert summary["candidate_sha256"] == binding["candidate_sha256"]
    assert summary["prior_ordinal"] == 4 and summary["replacement_basis"] == "invalidated_cleanup_evidence"
    assert terminals[-1]["prior_evidence_sha256"] == packet["prior_receipt_sha256"]
    for gate, ordinal in zip(record["gates"], ORDINALS, strict=True):
        receipt = terminals[ordinal - 1]
        _same(
            gate,
            {
                "ordinal": ordinal,
                "check": receipt["check"],
                **{key: receipt[key] for key in GATE_FIELDS - {"ordinal", "check"}},
            },
        )


def _validate(record, *, root):
    """Require the closed two-source projection and exact retained evidence, never a new aggregate."""
    _fields(record, TOP_FIELDS)
    assert type(record["schema_version"]) is int and record["schema_version"] == 3
    assert record["scope"] == "grok_native_generation_observations"
    assert record["profile"] == "generation_native_v1" and record["adapter"] == "grok"
    assert record["observed_version"] == "grok 1.0.70 (f50904eaee6c)"
    for name in ("executable_sha256", "recipe_sha256", "parser_sha256"):
        _digest(record[name])
    packet, compatibility, binding, equivalence = _proofs(root)
    candidates = record["tested_candidates"]
    assert type(candidates) is list and len(candidates) == 2
    for index, candidate in enumerate(candidates):
        _fields(
            candidate,
            {
                "candidate_sha256",
                "implementation_source_sha256",
                "source_commit",
                "verifier_sha256",
                "model_selection",
                "requested_model",
                "effective_model",
            },
        )
        for name in ("candidate_sha256", "implementation_source_sha256", "verifier_sha256"):
            _digest(candidate[name])
        assert candidate["source_commit"] == (ORIGINAL_COMMIT, CORRECTED_COMMIT)[index]
        _same(candidate["model_selection"], {"kind": "native_default"})
        assert candidate["requested_model"] is candidate["effective_model"] is None
        fingerprint = {
            "family": record["adapter"],
            **{key: record[key] for key in ("observed_version", "executable_sha256", "recipe_sha256", "parser_sha256")},
            "implementation_source_sha256": candidate["implementation_source_sha256"],
        }
        assert candidate["candidate_sha256"] == sha256(canonical_json(fingerprint))
    assert candidates[0]["candidate_sha256"] == compatibility["original_candidate_sha256"]
    assert candidates[1]["candidate_sha256"] == binding["candidate_sha256"]
    _manifests(equivalence, candidates)
    _same(
        record["budget"],
        {
            "max_total": 5,
            "max_per_gate": dict.fromkeys(CHECKS, 2),
            "max_replacements": 1,
            "consumed": 5,
            "replacements_used": 1,
            "automatic_retries": 0,
        },
    )
    submissions = record["submissions"]
    assert type(submissions) is list and len(submissions) == 5
    for index, item in enumerate(submissions):
        fields = {
            "ordinal",
            "check",
            "passed",
            "replacement",
            "candidate_sha256",
            "fixture_config_sha256",
            "fixture_project_sha256",
            "preflight",
            "reservation",
            "receipt",
        }
        _fields(item, fields | ({"replacement_basis"} if index == 4 else set()))
        _count(item["ordinal"], 5)
        assert item["ordinal"] == index + 1 and item["check"] == (*CHECKS, "cancel")[index]
        assert item["passed"] is True and item["replacement"] is (index == 4)
        if index == 4:
            assert item["replacement_basis"] == "invalidated_cleanup_evidence"
        assert item["candidate_sha256"] == candidates[int(index == 4)]["candidate_sha256"]
        for name in ("candidate_sha256", "fixture_config_sha256", "fixture_project_sha256"):
            _digest(item[name])
        for name, filename in (
            ("preflight", "preflight.json"),
            ("reservation", "submitted.json"),
            ("receipt", "receipt.json"),
        ):
            _reference(item[name])
            round_ = 2 if index == 4 else 1
            assert item[name]["path"] == f"{DIRECTORY}/20261103-{item['check']}-r{round_}/{filename}"
    gates = record["gates"]
    assert type(gates) is list and len(gates) == 4
    for index, gate in enumerate(gates):
        _fields(gate, GATE_FIELDS)
        _count(gate["ordinal"], 5)
        assert gate["ordinal"] == ORDINALS[index] and gate["check"] == CHECKS[index]
        for name in ("fixture_trust_requested", "host_configuration_changed", "host_configuration_restored"):
            assert gate[name] is True
        _same(gate["fixture_trust"], {"fixture_trust_prepared": True, "fixture_trust_removed": True})
        observed = gate["observation"]
        _fields(observed, OBSERVATION_FIELDS | (DIAGNOSTIC_FIELDS if index == 3 else set()))
        assert observed["check"] == CHECKS[index]
        for name in ("dispatch_count", "spawn_count", "pool_running_after_shutdown"):
            _count(observed[name])
        assert (
            observed["dispatch_count"] == observed["spawn_count"] == 1 and observed["pool_running_after_shutdown"] == 0
        )
        _count(observed["output_bytes"], OUTPUT_BYTES)
        for name in (
            "passed",
            "identity_bound",
            "private_input_removed",
            "process_groups_absent_after_cleanup",
            "production_service_used",
        ):
            assert observed[name] is True
        assert observed["raw_output_retained"] is False
        _fields(observed["observations"], DETAILS[index])
        assert all(value is True for value in observed["observations"].values())
        assert observed["terminal_status"] == ("completed", "completed", "failed", "cancelled")[index]
        assert observed["error_code"] == (None, None, "permission_needed", "cancelled")[index]
        if index < 2:
            assert (
                observed["actual_model"] == "grok-4.7-build"
                and observed["actual_model_origin"] == "native_terminal_metadata"
            )
            assert observed["output_bytes"] > 0
            _digest(observed["output_sha256"])
        else:
            assert observed["actual_model"] is observed["output_sha256"] is None
            assert observed["actual_model_origin"] == "unknown" and observed["output_bytes"] == 0
        if index == 3:
            assert observed["pool_exception_observation"] is None
            assert observed["pool_exception_diagnostic_capture_failed"] is False
    authorization = {
        "ordinal": 5,
        "prior_ordinal": 4,
        "replacement_basis": "invalidated_cleanup_evidence",
        "review_packet_sha256": PACKET_SHA256,
        **{
            name: packet[key]["sha256"]
            for name, key in (
                ("reviewed_input_sha256", "reviewed_input"),
                ("independent_review_sha256", "independent_review"),
                ("source_binding_sha256", "source_binding"),
                ("runtime_attestation_sha256", "runtime_attestation"),
            )
        },
    }
    _same(record["replacement_authorizations"], [authorization])
    reconciliation = {
        "kind": "reviewed_observer_compatibility",
        "original_candidate_sha256": candidates[0]["candidate_sha256"],
        "corrected_candidate_sha256": candidates[1]["candidate_sha256"],
        "compatibility_binding_sha256": packet["compatibility_binding"]["sha256"],
        "excluded_ordinal": 4,
        **{
            key + "_sha256": compatibility[key]["sha256"]
            for key in ("production_equivalence", "observer_counterexample", "non_cancellation_proof")
        },
    }
    _same(record["reconciliation"], reconciliation)
    _same(record["limitations"], LIMITATIONS)
    _retained(record, root, packet, compatibility, binding)


def _agy_proofs(root):
    """Bind portable technical copies to actual historical packets, without issuing a review verdict."""
    proofs = []
    for index, prefix in enumerate(("fifth", "sixth")):
        packet = _pinned(root, f"{AGY_PROOFS}/{prefix}-replacement-review.json", AGY_PACKET_PINS[index])
        linked = {
            key: _pinned(root, f"{AGY_PROOFS}/{prefix}-{filename}.json", packet[key]["sha256"])
            for key, filename in (
                ("compatibility_binding", "compatibility"),
                ("source_binding", "source-binding"),
                ("runtime_attestation", "runtime-attestation"),
                ("reviewed_input", "reviewed-input"),
            )
        }
        compatibility = linked["compatibility_binding"]
        binding, runtime_binding, reviewed = (
            linked[key] for key in ("source_binding", "runtime_attestation", "reviewed_input")
        )
        equivalence = _pinned(
            root,
            f"{AGY_PROOFS}/{prefix}-production-equivalence.json",
            compatibility["production_equivalence"]["sha256"],
        )
        for reference in equivalence["tests"]:
            filename = Path(reference["path"]).name
            if filename == "historical-cleanup-proof.json":
                filename = "observer-counterexample.json"
            assert sha256((root / AGY_PROOFS / f"{prefix}-{filename}").read_bytes()) == reference["sha256"]
        assert packet["family"] == compatibility["family"] == reviewed["family"] == "agy"
        assert packet["prior_ordinal"] == compatibility["prior_ordinal"] == reviewed["prior_ordinal"] == index + 4
        assert (
            packet["replacement_basis"]
            == compatibility["replacement_basis"]
            == ("failed_gate", "diagnostic_budget_extension")[index]
        )
        assert binding["base_commit"] == (ORIGINAL_COMMIT, CORRECTED_COMMIT)[index]
        assert binding["head_commit"] == (CORRECTED_COMMIT, DIAGNOSTIC_COMMIT)[index]
        _same(reviewed["source_revision"], {key: binding[key] for key in ("base_commit", "head_commit", "diff_sha256")})
        assert reviewed["candidate_sha256"] == binding["candidate_sha256"]
        assert reviewed["compatibility_binding_sha256"] == packet["compatibility_binding"]["sha256"]
        assert binding["receipt_sha256"] == runtime_binding["receipt_sha256"] == packet["independent_review"]["sha256"]
        assert binding["runtime_attestation_sha256"] == packet["runtime_attestation"]["sha256"]
        _same(
            {
                key: runtime_binding["effective_runtime"][key]
                for key in ("model", "effort", "model_provider", "approval_policy", "sandbox_type")
            },
            {
                "model": "gpt-6.1-sol",
                "effort": "high",
                "model_provider": "openai",
                "approval_policy": "never",
                "sandbox_type": "read-only",
            },
        )
        proofs.append((packet, compatibility, binding, equivalence))
    fifth, sixth = proofs
    assert [row["retained_eligible"] for row in fifth[1]["original_records"]] == [True, True, True, False]
    assert [row["reported_passed"] for row in fifth[1]["original_records"]] == [True, True, True, False]
    for key, filename in (
        ("observer_counterexample", "fifth-observer-counterexample.json"),
        ("non_cancellation_proof", "fifth-non-cancellation-proof.md"),
    ):
        assert sha256((root / AGY_PROOFS / filename).read_bytes()) == fifth[1][key]["sha256"]
    authorization = _pinned(
        root, f"{AGY_PROOFS}/sixth-user-authorization.json", sixth[0]["user_authorization"]["sha256"]
    )
    assert authorization["answer"] == "Approve the one additional diagnostic"
    assert authorization["family"] == "agy" and authorization["check"] == "cancel"
    assert type(authorization["authorized_next_ordinal"]) is int and authorization["authorized_next_ordinal"] == 6
    assert authorization["bounded_accounting_change_authorized"] is True
    _same(authorization["cumulative_limits"], {"total_calls": 6, "cancel_attempts": 3, "exceptional_replacements": 2})
    return proofs


def _agy_candidate(record, manifest, commit):
    fingerprint = {
        "family": "agy",
        **{
            key: record[key]
            for key in (
                "observed_version",
                "executable_sha256",
                "recipe_sha256",
                "parser_sha256",
                "implementation_source_sha256",
            )
        },
    }
    assert sha256(canonical_json(fingerprint)) == record["candidate_sha256"]
    components = {item["path"]: item["sha256"] for item in manifest["files"]}
    return {
        "candidate_sha256": record["candidate_sha256"],
        "implementation_source_sha256": record["implementation_source_sha256"],
        "source_commit": commit,
        "verifier_sha256": components["scripts/verify_family_generation.py"],
        "model_selection": {"kind": "native_default"},
        "requested_model": None,
        "effective_model": None,
    }


def _agy_history(root, proofs):
    fifth, sixth = proofs
    rows, terminals, history = [], [], []
    for index, check in enumerate((*CHECKS, "cancel", "cancel")):
        label = f"20261103-{check}-r{2 if index == 4 else 1}"
        if index == 5:
            label = "20261103-cancel-r3-diagnostic"
        pins = (
            {
                key: sixth[1]["original_records"][index]["terminal" if key == "receipt" else key]["sha256"]
                for key in ("preflight", "reservation", "receipt")
            }
            if index < 5
            else SIXTH_PINS
        )
        if index == 4:
            _same(pins, FIFTH_PINS["agy"])
        evidence = {
            key: _pinned(root, f"{AGY_DIRECTORY}/{label}/{filename}", pins[key])
            for key, filename in (
                ("preflight", "preflight.json"),
                ("reservation", "submitted.json"),
                ("receipt", "receipt.json"),
            )
        }
        preflight, reservation, terminal = (evidence[key] for key in ("preflight", "reservation", "receipt"))
        assert reservation["preflight_sha256"] == pins["preflight"]
        assert reservation["state"] == "consumed_before_dispatch"
        assert reservation["ordinal"] == index + 1 and reservation["check"] == check
        _same(preflight["submission_ledger"], history)
        _same(preflight["live_submitted"], False)
        _same(preflight["submissions_in_this_invocation"], 0)
        passed = index not in {3, 4}
        entry = {
            "ordinal": index + 1,
            "check": check,
            "passed": passed,
            "replacement": index >= 4,
            "reservation_sha256": pins["reservation"],
        }
        if index >= 4:
            entry["replacement_basis"] = ("failed_gate", "diagnostic_budget_extension")[index - 4]
            assert reservation["replacement_basis"] == entry["replacement_basis"]
        history.append(entry)
        _same(terminal["submission_ledger"], history)
        _same(terminal["submissions_consumed_total"], index + 1)
        _same(terminal["submissions_in_this_invocation"], 1)
        assert terminal["live_submitted"] is True and terminal["gate_passed"] is passed
        assert terminal["all_family_generation_gates_passed"] is False
        assert terminal["observation"]["passed"] is passed
        for key in (
            "candidate_sha256",
            "implementation_source_sha256",
            "fixture_config_sha256",
            "fixture_project_sha256",
        ):
            assert terminal[key] == preflight[key]
        for key in (
            "production_admission_changed",
            "native_task_admission_changed",
            "raw_output_retained",
            "prompts_recorded",
            "native_sessions_recorded",
        ):
            assert terminal[key] is False
        row = {key: entry[key] for key in ("ordinal", "check", "passed", "replacement")}
        row.update(
            {key: terminal[key] for key in ("candidate_sha256", "fixture_config_sha256", "fixture_project_sha256")}
        )
        row.update(
            {
                key: {"path": f"{AGY_DIRECTORY}/{label}/{filename}", "sha256": pins[key]}
                for key, filename in (
                    ("preflight", "preflight.json"),
                    ("reservation", "submitted.json"),
                    ("receipt", "receipt.json"),
                )
            }
        )
        if index >= 4:
            row["replacement_basis"] = entry["replacement_basis"]
            packet, _, binding, _ = proofs[index - 4]
            summary = terminal["replacement_review"]
            _same(summary, preflight["replacement_review"])
            assert summary["packet_sha256"] == AGY_PACKET_PINS[index - 4]
            assert summary["candidate_sha256"] == binding["candidate_sha256"]
            assert summary["prior_ordinal"] == index
            assert summary["replacement_basis"] == row["replacement_basis"]
            for summary_key, packet_key in (
                ("review_sha256", "independent_review"),
                ("source_binding_sha256", "source_binding"),
                ("runtime_attestation_sha256", "runtime_attestation"),
                ("compatibility_binding_sha256", "compatibility_binding"),
            ):
                assert summary[summary_key] == packet[packet_key]["sha256"]
            assert (
                terminal["prior_evidence_sha256"]
                == packet["prior_receipt_sha256"]
                == rows[index - 1]["receipt"]["sha256"]
            )
        if index == 5:
            for key in (
                "diagnostic_only",
                "diagnostic_purpose",
                "diagnostic_observation_complete",
                "diagnostic_publication",
                "user_authorization_sha256",
            ):
                row[key] = terminal[key]
            row["diagnostic_sidecar_sha256"] = terminal["diagnostic_sidecar_evidence"]["sha256"]
            assert terminal["diagnostic_only"] is terminal["diagnostic_observation_complete"] is True
            assert terminal["diagnostic_purpose"] == "cleanup_diagnostic_only"
            assert (
                terminal["prior_ledger_sha256"]
                == sha256(canonical_json(history[:5]))
                == sixth[0]["prior_ledger_sha256"]
            )
        rows.append(row)
        terminals.append(terminal)
    assert (
        terminals[3]["observation"]["error_code"] == terminals[4]["observation"]["error_code"] == "cleanup_unconfirmed"
    )
    assert "pool_exception_observation" not in terminals[3]["observation"]
    _same(
        terminals[4]["observation"]["pool_exception_observation"],
        {"exception_class": "PermissionError", "execution_phase": "production_cleanup"},
    )
    assert terminals[4]["observation"]["process_groups_absent_after_cleanup"] is False
    return rows, terminals


def _validate_agy(record, *, root):
    """Require the closed authentic projection; eligibility still needs separate promotion review."""
    _fields(record, TOP_FIELDS)
    proofs = _agy_proofs(root)
    fifth, sixth = proofs
    rows, terminals = _agy_history(root, proofs)
    manifests = (fifth[3]["original_components"], fifth[3]["corrected_components"], sixth[3]["corrected_components"])
    _same(manifests[1], sixth[3]["original_components"])
    candidates = [
        _agy_candidate(terminals[index], manifest, commit)
        for index, manifest, commit in zip(
            (0, 4, 5), manifests, (ORIGINAL_COMMIT, CORRECTED_COMMIT, DIAGNOSTIC_COMMIT), strict=True
        )
    ]
    _manifests(fifth[3], candidates[:2])
    _manifests(sixth[3], candidates[1:])
    equivalence = _pinned(root, f"{AGY_PROOFS}/production-equivalence.json", AGY_EQUIVALENCE_SHA256)
    _same(equivalence["component_manifests"], list(manifests))
    _same(equivalence["source_commits"], [ORIGINAL_COMMIT, CORRECTED_COMMIT, DIAGNOSTIC_COMMIT])
    _same(
        equivalence["implementation_source_sha256"],
        [candidate["implementation_source_sha256"] for candidate in candidates],
    )
    _same(equivalence["fixture_authority"], sixth[1]["fixture_authority"])
    for index, proof in enumerate(proofs):
        _same(
            equivalence["links"][index],
            {
                "original_source_commit": (ORIGINAL_COMMIT, CORRECTED_COMMIT)[index],
                "corrected_source_commit": (CORRECTED_COMMIT, DIAGNOSTIC_COMMIT)[index],
                "delta": proof[3]["delta"],
            },
        )
        _same(
            equivalence["compatibility_bindings"][index],
            {
                "path": f"{AGY_PROOFS}/{('fifth', 'sixth')[index]}-compatibility.json",
                "sha256": proof[0]["compatibility_binding"]["sha256"],
            },
        )
    for reference in (equivalence["source_invariants"], equivalence["non_cancellation_proof"]):
        _reference(reference)
        assert sha256((root / reference["path"]).read_bytes()) == reference["sha256"]
    interpretation = _pinned(root, f"{AGY_PROOFS}/admission-interpretation.json", AGY_INTERPRETATION_SHA256)
    assert (
        interpretation["admission_verdict_issued"] is False and interpretation["promotion_review_status"] == "pending"
    )
    _same(interpretation["tested_candidates"], candidates)
    _same(interpretation["selected_gate_ordinals"], [1, 2, 3, 6])
    _same(interpretation["excluded_failed_ordinals"], [4, 5])
    _same(interpretation["reported_exact_candidate_aggregates"], [False] * 6)
    _same(
        interpretation["failed_cancellations"],
        [
            {"ordinal": ordinal, "passed": False, "observation": terminals[ordinal - 1]["observation"]}
            for ordinal in (4, 5)
        ],
    )
    _same(
        interpretation["earlier_failure_cause"],
        {"cause": "unknown", "errno": None, "known": False, "resolved": False, "syscall": "unknown"},
    )
    context = interpretation["host_execution_context"]
    assert context["same_host_sandbox_conditions_proven"] is context["cause_inferred_from_context_absence"] is False
    assert context["failed_ordinals_4_5_sandbox_context"] == "unknown"
    for row in context["scoped_metadata"]:
        _reference(row["copy"])
        assert row["original"]["sha256"] == row["copy"]["sha256"]
        _pinned(root, row["copy"]["path"], row["copy"]["sha256"])
    _same(interpretation["limitations"], AGY_LIMITATIONS)
    for index, terminal in enumerate(terminals):
        assert terminal["candidate_sha256"] == candidates[0 if index < 4 else index - 3]["candidate_sha256"]
    assert candidates[0]["candidate_sha256"] == fifth[1]["original_candidate_sha256"]
    assert candidates[1]["candidate_sha256"] == sixth[1]["original_candidate_sha256"]
    sidecar = _pinned(root, f"{AGY_PROOFS}/sixth-cleanup-diagnostics.json", AGY_SIDECAR_SHA256)
    terminal = terminals[-1]
    publication = terminal["diagnostic_publication"]
    _fields(publication, {"published", "run_id", "result_sha256", "sidecar_sha256"})
    assert publication["published"] is True
    assert publication["sidecar_sha256"] == rows[-1]["diagnostic_sidecar_sha256"] == AGY_SIDECAR_SHA256
    for key in ("run_id", "result_sha256"):
        assert publication[key] == sidecar[key]
    _same(publication, terminal["observation"]["diagnostic_publication"])
    assert sidecar["capture_failed"] is False and sidecar["exception"] is None
    assert sidecar["executable_sha256"] == terminal["executable_sha256"]
    _same(
        {key: sidecar["source"][key] for key in terminal["replacement_review"]["diagnostic_source_sha256"]},
        terminal["replacement_review"]["diagnostic_source_sha256"],
    )
    _same(
        sidecar["processes"]["fixture_child"],
        [
            {"moment": moment, "state": "unknown", "identity": None, "fingerprint_sha256": None}
            for moment in ("initial", "cleanup_entry")
        ],
    )
    for snapshot in sidecar["processes"]["leader"]:
        assert snapshot["state"] == "observed"
        assert snapshot["fingerprint_sha256"] == sha256(canonical_json(snapshot["identity"]))
    gates = []
    for index, ordinal in enumerate((1, 2, 3, 6)):
        terminal = terminals[ordinal - 1]
        observed = terminal["observation"]
        _fields(
            observed, OBSERVATION_FIELDS | (DIAGNOSTIC_FIELDS | {"diagnostic_publication"} if ordinal == 6 else set())
        )
        details = DETAILS[index] | ({"permission_hook_observed"} if index == 2 else set())
        _fields(observed["observations"], details)
        assert all(value is True for value in observed["observations"].values())
        for key in (
            "passed",
            "identity_bound",
            "private_input_removed",
            "process_groups_absent_after_cleanup",
            "production_service_used",
        ):
            assert observed[key] is True
        _same(observed["dispatch_count"], 1)
        _same(observed["spawn_count"], 1)
        _same(observed["pool_running_after_shutdown"], 0)
        _count(observed["output_bytes"], OUTPUT_BYTES)
        assert observed["raw_output_retained"] is False
        assert observed["actual_model"] is None and observed["actual_model_origin"] == "unknown"
        assert observed["terminal_status"] == ("completed", "completed", "failed", "cancelled")[index]
        assert observed["error_code"] == (None, None, "permission_needed", "cancelled")[index]
        if index < 2:
            assert observed["output_bytes"] > 0
            _digest(observed["output_sha256"])
        else:
            assert observed["output_sha256"] is None and observed["output_bytes"] == 0
        if ordinal == 6:
            assert observed["pool_exception_observation"] is None
            assert observed["pool_exception_diagnostic_capture_failed"] is False
        assert terminal["fixture_trust_requested"] is terminal["host_configuration_changed"] is False
        assert terminal["host_configuration_restored"] is True
        _same(terminal["fixture_trust"], {"fixture_trust_prepared": False, "fixture_trust_removed": False})
        gates.append({"ordinal": ordinal, **{key: terminal[key] for key in GATE_FIELDS - {"ordinal"}}})
    authorizations = []
    for index, (packet, _, _, _) in enumerate(proofs):
        authorization = {
            "ordinal": index + 5,
            "prior_ordinal": index + 4,
            "replacement_basis": packet["replacement_basis"],
            "review_packet_sha256": AGY_PACKET_PINS[index],
            **{
                name: packet[key]["sha256"]
                for name, key in (
                    ("reviewed_input_sha256", "reviewed_input"),
                    ("independent_review_sha256", "independent_review"),
                    ("source_binding_sha256", "source_binding"),
                    ("runtime_attestation_sha256", "runtime_attestation"),
                )
            },
        }
        if index == 1:
            authorization.update(
                user_authorization_sha256=packet["user_authorization"]["sha256"],
                compatibility_binding_sha256=packet["compatibility_binding"]["sha256"],
                **{key: packet[key] for key in ("prior_ledger_sha256", "diagnostic_purpose", "diagnostic_limits")},
            )
        authorizations.append(authorization)
    reconciliation = {
        "kind": "diagnostic_observation_reconciliation",
        "original_candidate_sha256": candidates[0]["candidate_sha256"],
        "observer_corrected_candidate_sha256": candidates[1]["candidate_sha256"],
        "diagnostic_candidate_sha256": candidates[2]["candidate_sha256"],
        "observer_compatibility_binding_sha256": fifth[0]["compatibility_binding"]["sha256"],
        "diagnostic_compatibility_binding_sha256": sixth[0]["compatibility_binding"]["sha256"],
        "production_equivalence_sha256": sha256((root / AGY_PROOFS / "production-equivalence.json").read_bytes()),
        "non_cancellation_proof_sha256": fifth[1]["non_cancellation_proof"]["sha256"],
        "admission_interpretation_sha256": sha256((root / AGY_PROOFS / "admission-interpretation.json").read_bytes()),
        "excluded_failed_ordinals": [4, 5],
    }
    expected = {
        "schema_version": 4,
        "scope": "agy_native_generation_observations",
        "profile": "generation_native_v1",
        "adapter": "agy",
        "observed_version": "1.4.12",
        **{key: terminals[0][key] for key in ("executable_sha256", "recipe_sha256", "parser_sha256")},
        "tested_candidates": candidates,
        "budget": {
            "automatic_retries": 0,
            "consumed": 6,
            "max_per_gate": {**dict.fromkeys(CHECKS, 2), "cancel": 3},
            "max_replacements": 2,
            "max_total": 6,
            "replacements_used": 2,
        },
        "submissions": rows,
        "gates": gates,
        "replacement_authorizations": authorizations,
        "reconciliation": reconciliation,
        "limitations": AGY_LIMITATIONS,
    }
    _same(record, expected)
    return proofs, terminals


def _injected_agy_admission(evidence_path):
    """Supply explicit test authority without changing default family admission."""
    record = _load(evidence_path.read_bytes())
    return NativeGenerationAdmission(
        verified=True,
        version=record["observed_version"],
        **{key: record[key] for key in ("executable_sha256", "recipe_sha256", "parser_sha256")},
        evidence_path=str(evidence_path),
        evidence_sha256=sha256(evidence_path.read_bytes()),
    )


def _grok_current_source(record, *, root):
    """Bind both historical manifests and the prospective guard to frozen archive source bytes."""
    helper = _pinned(root, f"{PROOFS}/helper-current-source-equivalence.json", GROK_HELPER_EQUIVALENCE_SHA256)
    _, _, _, historical = _proofs(root)
    packet, _, binding, diagnostic = _agy_proofs(root)[1]
    _same(helper["original_components"], historical["corrected_components"])
    _same(helper["reviewed_components"], diagnostic["corrected_components"])
    assert helper["original_source_commit"] == CORRECTED_COMMIT
    assert helper["reviewed_source_commit"] == DIAGNOSTIC_COMMIT
    _same(helper["delta"], diagnostic["delta"])
    assert [row["path"] for row in helper["delta"]] == ["scripts/verify_family_generation.py"]
    assert helper["grok_gates_executed_on_newer_verifier"] is False
    assert helper["historical_grok_resource_or_manifest_rewritten"] is False
    assert helper["promotion_review_sufficiency_claimed"] is False
    for key, expected in (
        ("review_packet", AGY_PACKET_PINS[1]),
        ("source_binding", packet["source_binding"]["sha256"]),
        ("runtime_attestation", binding["runtime_attestation_sha256"]),
    ):
        _reference(helper[key])
        assert helper[key]["sha256"] == expected
        _pinned(root, helper[key]["path"], expected)
    assert record["tested_candidates"][1]["verifier_sha256"] == helper["delta"][0]["original_sha256"]
    assert sha256(canonical_json(helper["original_components"])) == helper["original_implementation_source_sha256"]
    assert sha256(canonical_json(helper["reviewed_components"])) == helper["reviewed_implementation_source_sha256"]
    guard = _pinned(
        root, f"{PROOFS}/nonlive-guard-current-source-equivalence.json", GROK_NONLIVE_GUARD_EQUIVALENCE_SHA256
    )
    assert guard["baseline_source_commit"] == DIAGNOSTIC_COMMIT
    assert guard["changed_component"] == "scripts/verify_family_generation.py"
    assert guard["baseline_verifier_sha256"] == helper["delta"][0]["corrected_sha256"]
    assert guard["genuine_repair_review_status"] == "pending"
    _same(
        guard["baseline_helper_equivalence"],
        {"path": f"{PROOFS}/helper-current-source-equivalence.json", "sha256": GROK_HELPER_EQUIVALENCE_SHA256},
    )
    for key in (
        "historical_gate_source_relabelled",
        "historical_manifests_rewritten",
        "native_call_budget_extended",
        "promotion_eligibility_claimed",
    ):
        assert guard[key] is False
    assert guard["other_verifier_asts_unchanged"] is True
    _reference(guard["guard_diff"])
    assert sha256((root / guard["guard_diff"]["path"]).read_bytes()) == guard["guard_diff"]["sha256"]
    for component in helper["reviewed_components"]["files"]:
        # Exact admission-policy activation is the sole declared production-byte exception.
        if component["path"] != "clink/native_generation_family_policy.py":
            expected = (
                guard["current_verifier_sha256"]
                if component["path"] == guard["changed_component"]
                else component["sha256"]
            )
            assert sha256((root / component["path"]).read_bytes()) == expected


def _synthetic_digest(label):
    return sha256(label.encode("ascii"))


def _synthetic_agy_projection():
    """Build protocol-shaped test data; this is never historical admission authority."""
    record = _load(RESOURCE.read_bytes())
    record.update(
        schema_version=4,
        scope="agy_native_generation_observations",
        adapter="agy",
        observed_version="synthetic-agy-version",
        executable_sha256=_synthetic_digest("synthetic executable"),
        recipe_sha256=family_generation_recipe_digest("agy"),
        parser_sha256=family_generation_parser_digest("agy"),
    )
    candidates = [copy.deepcopy(record["tested_candidates"][0]) for _ in range(3)]
    for index, candidate in enumerate(candidates):
        candidate.update(
            source_commit=str(index + 1) * 40,
            verifier_sha256=_synthetic_digest(f"verifier {index}"),
            implementation_source_sha256=_synthetic_digest(f"source {index}"),
        )
        fingerprint = {
            "family": "agy",
            **{key: record[key] for key in ("observed_version", "executable_sha256", "recipe_sha256", "parser_sha256")},
            "implementation_source_sha256": candidate["implementation_source_sha256"],
        }
        candidate["candidate_sha256"] = sha256(canonical_json(fingerprint))
    record["tested_candidates"] = candidates
    record["budget"].update(
        consumed=6,
        max_total=6,
        max_replacements=2,
        replacements_used=2,
        max_per_gate={**dict.fromkeys(CHECKS, 2), "cancel": 3},
    )
    publication = {
        "published": True,
        "run_id": "11111111-2222-3333-4444-555555555555",
        "result_sha256": _synthetic_digest("synthetic result"),
        "sidecar_sha256": _synthetic_digest("synthetic sidecar"),
    }
    record["submissions"].append(copy.deepcopy(record["submissions"][-1]))
    for index, item in enumerate(record["submissions"]):
        item.update(
            ordinal=index + 1,
            passed=index not in {3, 4},
            candidate_sha256=candidates[0 if index < 4 else index - 3]["candidate_sha256"],
            fixture_config_sha256=_synthetic_digest(f"configuration {index}"),
            fixture_project_sha256=_synthetic_digest(f"project {index}"),
        )
        for key in ("preflight", "reservation", "receipt"):
            item[key] = {
                "path": f"fixtures/synthetic/agy/{index + 1}/{key}.json",
                "sha256": _synthetic_digest(f"{index} {key}"),
            }
        if index >= 4:
            item["replacement_basis"] = ("failed_gate", "diagnostic_budget_extension")[index - 4]
        if index == 5:
            item.update(
                diagnostic_only=True,
                diagnostic_purpose="cleanup_diagnostic_only",
                diagnostic_observation_complete=True,
                diagnostic_publication=copy.deepcopy(publication),
                diagnostic_sidecar_sha256=publication["sidecar_sha256"],
                user_authorization_sha256=_synthetic_digest("synthetic authorization"),
            )
    for index, gate in enumerate(record["gates"]):
        gate.update(
            ordinal=(1, 2, 3, 6)[index],
            fixture_trust_requested=False,
            host_configuration_changed=False,
            fixture_trust={"fixture_trust_prepared": False, "fixture_trust_removed": False},
        )
        observed = gate["observation"]
        observed.update(actual_model=None, actual_model_origin="unknown")
        if index < 2:
            observed.update(output_bytes=index + 1, output_sha256=_synthetic_digest(f"output {index}"))
        if index == 2:
            observed["observations"]["permission_hook_observed"] = True
        if index == 3:
            observed["diagnostic_publication"] = copy.deepcopy(publication)
    authorization = record["replacement_authorizations"][0]
    authorization["replacement_basis"] = "failed_gate"
    for key in list(authorization):
        if key.endswith("_sha256"):
            authorization[key] = _synthetic_digest(f"synthetic fifth {key}")
    diagnostic = copy.deepcopy(authorization)
    diagnostic.update(
        ordinal=6,
        prior_ordinal=5,
        replacement_basis="diagnostic_budget_extension",
        user_authorization_sha256=_synthetic_digest("synthetic authorization"),
        compatibility_binding_sha256=_synthetic_digest("synthetic compatibility"),
        prior_ledger_sha256=_synthetic_digest("synthetic ledger"),
        diagnostic_purpose="cleanup_diagnostic_only",
        diagnostic_limits={"cancel_attempts": 3, "exceptional_replacements": 2, "total_calls": 6},
    )
    record["replacement_authorizations"].append(diagnostic)
    record["reconciliation"] = {
        "kind": "diagnostic_observation_reconciliation",
        "original_candidate_sha256": candidates[0]["candidate_sha256"],
        "observer_corrected_candidate_sha256": candidates[1]["candidate_sha256"],
        "diagnostic_candidate_sha256": candidates[2]["candidate_sha256"],
        "excluded_failed_ordinals": [4, 5],
        **{
            key + "_sha256": _synthetic_digest("synthetic " + key)
            for key in (
                "observer_compatibility_binding",
                "diagnostic_compatibility_binding",
                "production_equivalence",
                "non_cancellation_proof",
                "admission_interpretation",
            )
        },
    }
    record["limitations"] = dict(AGY_LIMITATIONS)
    return record


def _validate_grok_projection(record):
    """Check the closed pinned public projection without opening historical references."""
    expected = _load(RESOURCE.read_bytes())
    assert sha256(RESOURCE.read_bytes()) == DEFAULT_GROK_NATIVE_ADMISSION.evidence_sha256
    _same(record, expected)


def _validate_agy_projection(record):
    """Keep synthetic failed/diagnostic structure separate from historical authority."""
    _same(record, _synthetic_agy_projection())


def _reject_changed_projection(record, cli, tmp_path, monkeypatch):
    """Any changed projection must fail its independent byte pin before binary identity."""
    expected = _load(RESOURCE.read_bytes()) if cli == "grok" else _synthetic_agy_projection()
    original = canonical_json(expected)
    changed = canonical_json(record)
    assert changed != original
    evidence = tmp_path / "changed-projection.json"
    evidence.write_bytes(original)
    admission = NativeGenerationAdmission(
        verified=True,
        version=expected["observed_version"],
        **{key: expected[key] for key in ("executable_sha256", "recipe_sha256", "parser_sha256")},
        evidence_path=str(evidence),
        evidence_sha256=sha256(original),
    )
    service = NativeGenerationService(family_admissions={cli: admission})
    monkeypatch.setattr(runtime, "executable_digest", lambda _: admission.executable_sha256)
    assert service._verify_admission("identity-only", admission, cli=cli) == admission.executable_sha256
    evidence.write_bytes(changed)
    monkeypatch.setattr(
        runtime, "executable_digest", lambda _: pytest.fail("changed projection reached binary identity")
    )
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission, cli=cli)
    assert caught.value.code == "policy_unverified"


def _synthetic_agy_admission(tmp_path):
    evidence_path = tmp_path / "synthetic-agy-observations.json"
    evidence_path.write_bytes(canonical_json(_synthetic_agy_projection()))
    return _injected_agy_admission(evidence_path)


def _synthetic_manifests():
    """Exercise the one-component delta contract without frozen source history."""
    files = ["clink/runner.py", "scripts/verify_family_generation.py"]
    files.extend(f"clink/synthetic_component_{index}.py" for index in range(60))
    original = {"schema_version": 1, "files": [{"path": name, "sha256": _synthetic_digest(name)} for name in files]}
    corrected = copy.deepcopy(original)
    next(item for item in corrected["files"] if item["path"].startswith("scripts/"))["sha256"] = _synthetic_digest(
        "corrected verifier"
    )
    equivalence, candidates = {}, []
    for name, manifest in (("original", original), ("corrected", corrected)):
        digest = sha256(canonical_json(manifest))
        equivalence[name + "_components"] = manifest
        equivalence[name + "_implementation_source_sha256"] = digest
        candidates.append(
            {
                "implementation_source_sha256": digest,
                "verifier_sha256": next(
                    item["sha256"] for item in manifest["files"] if item["path"].startswith("scripts/")
                ),
            }
        )
    equivalence["delta"] = [
        {
            "path": "scripts/verify_family_generation.py",
            "original_sha256": candidates[0]["verifier_sha256"],
            "corrected_sha256": candidates[1]["verifier_sha256"],
        }
    ]
    _manifests(equivalence, candidates)
    return equivalence, candidates


def test_public_projection_baselines_are_closed_without_historical_files():
    _validate_grok_projection(_load(RESOURCE.read_bytes()))
    _validate_agy_projection(_synthetic_agy_projection())


def test_grok_observations_preserve_two_candidates_five_submissions_and_selected_gates():
    content = RESOURCE.read_bytes()
    record = _load(content)
    _validate_grok_projection(record)
    admission = DEFAULT_GROK_NATIVE_ADMISSION
    assert admission.verified is True and sha256(content) == admission.evidence_sha256
    assert admission.version == record["observed_version"]
    assert admission.executable_sha256 == record["executable_sha256"]
    assert admission.recipe_sha256 == record["recipe_sha256"] == family_generation_recipe_digest("grok")
    assert admission.parser_sha256 == record["parser_sha256"] == family_generation_parser_digest("grok")
    assert [gate["ordinal"] for gate in record["gates"]] == [1, 2, 3, 5]
    assert record["submissions"][3]["passed"] is True and record["reconciliation"]["excluded_ordinal"] == 4


@pytest.mark.private_evidence
def test_agy_preserves_failed_fifth_cancellation_without_borrowing_grok_admission(private_evidence_root):
    for name, filename in (
        ("preflight", "preflight.json"),
        ("reservation", "submitted.json"),
        ("receipt", "receipt.json"),
    ):
        record = _pinned(
            private_evidence_root,
            f"docs/verification/family-generation/agy/20261103-cancel-r2/{filename}",
            FIFTH_PINS["agy"][name],
        )
        assert record["family"] == "agy"
        if name == "receipt":
            assert len(record["submission_ledger"]) == record["submissions_consumed_total"] == 5
            assert [entry["passed"] for entry in record["submission_ledger"]] == [True, True, True, False, False]
            assert record["gate_passed"] is record["all_family_generation_gates_passed"] is False
            observed = record["observation"]
            assert (
                observed["error_code"] == "cleanup_unconfirmed"
                and observed["process_groups_absent_after_cleanup"] is False
            )
            assert observed["pool_exception_observation"] == {
                "exception_class": "PermissionError",
                "execution_phase": "production_cleanup",
            }
            ledger = record["submission_ledger"]
            previous = sha256(
                (
                    private_evidence_root / "docs/verification/family-generation/agy/20261103-preflight/receipt.json"
                ).read_bytes()
            )
            for index, entry in enumerate(ledger):
                round_ = 2 if index == 4 else 1
                directory = (
                    private_evidence_root
                    / f"docs/verification/family-generation/agy/20261103-{entry['check']}-r{round_}"
                )
                reservation_bytes = (directory / "submitted.json").read_bytes()
                reservation = _load(reservation_bytes)
                assert sha256(reservation_bytes) == entry["reservation_sha256"]
                assert sha256((directory / "preflight.json").read_bytes()) == reservation["preflight_sha256"]
                prior = _load((directory / "preflight.json").read_bytes())
                terminal = _load((directory / "receipt.json").read_bytes())
                _same(prior["submission_ledger"], ledger[:index])
                _same(terminal["submission_ledger"], ledger[: index + 1])
                assert terminal["prior_evidence_sha256"] == previous
                previous = sha256((directory / "receipt.json").read_bytes())
    permission = _load(
        (
            private_evidence_root / "docs/verification/family-generation/agy/20261103-permission-r1/receipt.json"
        ).read_bytes()
    )
    assert permission["observation"]["observations"]["permission_hook_observed"] is True
    assert permission["observation"]["actual_model"] is None
    assert _load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes())["adapter"] == "agy"


@pytest.mark.private_evidence
def test_rejected_agy_proposal_preserves_three_candidates_failures_and_diagnostic_only_sixth(private_evidence_root):
    content = (private_evidence_root / AGY_RESOURCE_PATH).read_bytes()
    record = _load(content)
    _, terminals = _validate_agy(record, root=private_evidence_root)
    admission = _injected_agy_admission(private_evidence_root / AGY_RESOURCE_PATH)
    assert sha256(content) == admission.evidence_sha256
    assert not (ROOT / "clink/resources" / Path(AGY_RESOURCE_PATH).name).exists()
    assert admission.version == record["observed_version"]
    assert admission.executable_sha256 == record["executable_sha256"]
    assert admission.recipe_sha256 == record["recipe_sha256"] == family_generation_recipe_digest("agy")
    assert admission.parser_sha256 == record["parser_sha256"] == family_generation_parser_digest("agy")
    assert [row["passed"] for row in record["submissions"]] == [True, True, True, False, False, True]
    assert [row["ordinal"] for row in record["gates"]] == [1, 2, 3, 6]
    assert all(terminal["all_family_generation_gates_passed"] is False for terminal in terminals)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("tested_candidates", 2),
        ("budget",),
        ("budget", "max_per_gate"),
        ("submissions", 3),
        ("submissions", 4),
        ("submissions", 5),
        ("submissions", 5, "diagnostic_publication"),
        ("submissions", 5, "receipt"),
        ("gates", 2, "observation", "observations"),
        ("gates", 3, "observation"),
        ("gates", 3, "observation", "diagnostic_publication"),
        ("replacement_authorizations", 0),
        ("replacement_authorizations", 1),
        ("replacement_authorizations", 1, "diagnostic_limits"),
        ("reconciliation",),
        ("limitations",),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_agy_observation_contract_rejects_nonclosed_objects(path, change, tmp_path, monkeypatch):
    record = _synthetic_agy_projection()
    selected = record
    for key in path:
        selected = selected[key]
    if change == "missing":
        selected.pop(next(iter(selected)))
    else:
        selected["admission_review"] = "not observation authority"
    _reject_changed_projection(record, "agy", tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("schema_version",), 3),
        (("adapter",), "grok"),
        (("budget", "consumed"), 7),
        (("budget", "automatic_retries"), 1),
        (("budget", "max_per_gate", "cancel"), 2),
        (("budget", "max_replacements"), True),
        (("submissions", 3, "passed"), True),
        (("submissions", 4, "passed"), True),
        (("submissions", 3, "check"), "text"),
        (("submissions", 5, "ordinal"), 7),
        (("submissions", 5, "passed"), 1),
        (("submissions", 5, "diagnostic_only"), False),
        (("submissions", 5, "diagnostic_purpose"), "ordinary_admission"),
        (("submissions", 5, "diagnostic_observation_complete"), False),
        (("submissions", 5, "diagnostic_sidecar_sha256"), "0" * 64),
        (("submissions", 5, "user_authorization_sha256"), "0" * 64),
        (("submissions", 5, "diagnostic_publication", "run_id"), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        (("submissions", 5, "diagnostic_publication", "result_sha256"), "0" * 64),
        (("submissions", 5, "diagnostic_publication", "sidecar_sha256"), "0" * 64),
        (("submissions", 5, "diagnostic_publication", "published"), False),
        (("submissions", 4, "replacement_basis"), "invalidated_cleanup_evidence"),
        (("submissions", 5, "replacement_basis"), "failed_gate"),
        (("submissions", 5, "receipt", "path"), "/private/copy.json"),
        (("submissions", 5, "receipt", "sha256"), "0" * 64),
        (("tested_candidates", 2, "source_commit"), CORRECTED_COMMIT),
        (("tested_candidates", 2, "candidate_sha256"), "0" * 64),
        (("tested_candidates", 0, "effective_model"), "unreported-model"),
        (("replacement_authorizations", 0, "review_packet_sha256"), PACKET_SHA256),
        (("replacement_authorizations", 1, "user_authorization_sha256"), "0" * 64),
        (("replacement_authorizations", 1, "runtime_attestation_sha256"), "0" * 64),
        (("replacement_authorizations", 1, "prior_ledger_sha256"), "0" * 64),
        (("replacement_authorizations", 1, "diagnostic_limits", "total_calls"), 6.0),
        (("replacement_authorizations", 1, "diagnostic_purpose"), "admission"),
        (("reconciliation", "excluded_failed_ordinals"), [4]),
        (("reconciliation", "observer_compatibility_binding_sha256"), "0" * 64),
        (("reconciliation", "diagnostic_compatibility_binding_sha256"), "0" * 64),
        (("reconciliation", "production_equivalence_sha256"), "0" * 64),
        (("reconciliation", "admission_interpretation_sha256"), "0" * 64),
        (("reconciliation", "non_cancellation_proof_sha256"), "0" * 64),
        (("gates", 3, "ordinal"), 5),
        (("gates", 3, "fixture_trust_requested"), True),
        (("gates", 2, "observation", "observations", "permission_hook_observed"), False),
        (("gates", 3, "observation", "process_groups_absent_after_cleanup"), False),
        (("gates", 3, "observation", "process_groups_absent_after_cleanup"), None),
        (("gates", 3, "observation", "pool_running_after_shutdown"), 1),
        (("gates", 3, "observation", "dispatch_count"), True),
        (("gates", 3, "observation", "private_input_removed"), False),
        (("gates", 3, "observation", "observations", "fixture_configuration_intact"), False),
        (("gates", 3, "observation", "observations", "synthetic_child_absent"), False),
        (("gates", 3, "observation", "observations", "synthetic_child_identity_observed"), None),
        (("gates", 3, "observation", "observations", "synthetic_child_ready"), False),
        (("limitations", "prior_cleanup_failure_cause_known"), True),
        (("limitations", "prior_cleanup_failure_resolved"), True),
        (("limitations", "diagnostic_child_identity_known"), True),
    ],
)
def test_agy_observation_contract_rejects_relabelled_failures_or_false_authority(path, value, tmp_path, monkeypatch):
    record = _synthetic_agy_projection()
    selected = record
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = value
    _reject_changed_projection(record, "agy", tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "field,index",
    [
        ("submissions", 3),
        ("submissions", 4),
        ("submissions", 5),
        ("tested_candidates", 0),
        ("tested_candidates", 1),
        ("tested_candidates", 2),
        ("replacement_authorizations", 0),
        ("replacement_authorizations", 1),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_agy_projection_forbids_omitted_failures_conflated_candidates_or_seventh_calls(
    field, index, change, tmp_path, monkeypatch
):
    record = _synthetic_agy_projection()
    if change == "missing":
        record[field].pop(index)
    else:
        record[field].append(copy.deepcopy(record[field][index]))
    _reject_changed_projection(record, "agy", tmp_path, monkeypatch)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "filename",
    [
        "fifth-replacement-review.json",
        "sixth-replacement-review.json",
        "fifth-compatibility.json",
        "sixth-compatibility.json",
        "sixth-source-binding.json",
        "sixth-runtime-attestation.json",
        "sixth-user-authorization.json",
        "sixth-cleanup-diagnostics.json",
        "production-equivalence.json",
        "admission-interpretation.json",
        "fifth-non-cancellation-proof.md",
    ],
)
def test_agy_changed_or_missing_retained_authority_fails_closed(tmp_path, filename, private_evidence_root):
    _copy_mutable_evidence(private_evidence_root / AGY_DIRECTORY, tmp_path / AGY_DIRECTORY)
    path = tmp_path / AGY_PROOFS / filename
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(AssertionError):
        _validate_agy(_load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes()), root=tmp_path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        _validate_agy(_load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes()), root=tmp_path)


@pytest.mark.private_evidence
@pytest.mark.parametrize("change", ["aggregate", "child_known", "run", "result"])
def test_agy_repinned_copies_cannot_manufacture_aggregate_or_diagnostic_identity(
    tmp_path, change, private_evidence_root
):
    _copy_mutable_evidence(private_evidence_root / AGY_DIRECTORY, tmp_path / AGY_DIRECTORY)
    record = _load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes())
    if change == "aggregate":
        path = tmp_path / record["submissions"][5]["receipt"]["path"]
        value = _load(path.read_bytes())
        value["all_family_generation_gates_passed"] = True
        path.write_bytes(canonical_json(value))
        record["submissions"][5]["receipt"]["sha256"] = sha256(path.read_bytes())
    else:
        path = tmp_path / AGY_PROOFS / "sixth-cleanup-diagnostics.json"
        value = _load(path.read_bytes())
        if change == "child_known":
            value["processes"]["fixture_child"][0]["state"] = "observed"
        else:
            value["run_id" if change == "run" else "result_sha256"] = (
                "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" if change == "run" else "0" * 64
            )
        path.write_bytes(canonical_json(value))
        record["submissions"][5]["diagnostic_sidecar_sha256"] = sha256(path.read_bytes())
    with pytest.raises(AssertionError):
        _validate_agy(record, root=tmp_path)


def test_agy_cannot_borrow_grok_observations_or_review_binding(tmp_path, monkeypatch):
    _reject_changed_projection(_load(RESOURCE.read_bytes()), "agy", tmp_path, monkeypatch)
    record = _synthetic_agy_projection()
    record["tested_candidates"][1] = _load(RESOURCE.read_bytes())["tested_candidates"][1]
    _reject_changed_projection(record, "agy", tmp_path, monkeypatch)


@pytest.mark.private_evidence
@pytest.mark.parametrize("filename", ["preflight.json", "submitted.json", "receipt.json"])
def test_agy_missing_actual_sixth_evidence_cannot_establish_admission(tmp_path, filename, private_evidence_root):
    _copy_mutable_evidence(private_evidence_root / AGY_DIRECTORY, tmp_path / AGY_DIRECTORY)
    (tmp_path / AGY_DIRECTORY / "20261103-cancel-r3-diagnostic" / filename).unlink()
    with pytest.raises(FileNotFoundError):
        _validate_agy(_load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes()), root=tmp_path)


@pytest.mark.private_evidence
def test_grok_helper_link_cannot_relabel_its_historical_verifier_or_exempt_another_component(
    tmp_path, private_evidence_root
):
    _copy_mutable_evidence(private_evidence_root / DIRECTORY, tmp_path / DIRECTORY)
    path = tmp_path / PROOFS / "helper-current-source-equivalence.json"
    helper = _load(path.read_bytes())
    helper["reviewed_components"]["files"][0]["sha256"] = "0" * 64
    helper["reviewed_implementation_source_sha256"] = sha256(canonical_json(helper["reviewed_components"]))
    path.write_bytes(canonical_json(helper))
    with pytest.raises(AssertionError):
        _grok_current_source(_load(RESOURCE.read_bytes()), root=tmp_path)


@pytest.mark.private_evidence
@pytest.mark.parametrize("change", ["component", "current_hash", "diff"])
def test_grok_nonlive_guard_binding_cannot_broaden_its_exact_prospective_delta(tmp_path, change, private_evidence_root):
    _copy_mutable_evidence(private_evidence_root / DIRECTORY, tmp_path / DIRECTORY)
    _copy_mutable_evidence(private_evidence_root / AGY_DIRECTORY, tmp_path / AGY_DIRECTORY)
    path = tmp_path / PROOFS / "nonlive-guard-current-source-equivalence.json"
    if change == "diff":
        diff = tmp_path / PROOFS / "nonlive-guard-verifier.diff"
        diff.write_bytes(diff.read_bytes() + b"\n")
    else:
        guard = _load(path.read_bytes())
        guard["changed_component" if change == "component" else "current_verifier_sha256"] = (
            "clink/runner.py" if change == "component" else "0" * 64
        )
        path.write_bytes(canonical_json(guard))
    with pytest.raises(AssertionError):
        _grok_current_source(_load(RESOURCE.read_bytes()), root=tmp_path)


def test_default_agy_remains_unverified_and_refuses_before_binary_identity(monkeypatch):
    assert DEFAULT_AGY_NATIVE_ADMISSION == NativeGenerationAdmission()
    assert DEFAULT_AGY_NATIVE_ADMISSION.verified is False
    assert DEFAULT_AGY_NATIVE_ADMISSION.evidence_path is DEFAULT_AGY_NATIVE_ADMISSION.evidence_sha256 is None
    monkeypatch.setattr(runtime, "executable_digest", lambda _: pytest.fail("unverified Agy reached binary identity"))
    service = NativeGenerationService()
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", service._admission_for("agy"), cli="agy")
    assert caught.value.code == "policy_unverified"


def test_explicitly_injected_agy_admission_verifies_proposal_bytes_without_spawning(tmp_path, monkeypatch):
    admission = _synthetic_agy_admission(tmp_path)
    monkeypatch.setattr(runtime, "executable_digest", lambda _: admission.executable_sha256)
    service = NativeGenerationService(family_admissions={"agy": admission})
    assert (
        service._verify_admission("identity-only", service._admission_for("agy"), cli="agy")
        == admission.executable_sha256
    )


@pytest.mark.parametrize("field", ["executable_sha256", "recipe_sha256", "parser_sha256"])
def test_agy_binary_recipe_or_parser_mismatch_fails_closed(tmp_path, monkeypatch, field):
    original = _synthetic_agy_admission(tmp_path)
    admission = replace(original, **{field: "0" * 64})
    service = NativeGenerationService(family_admissions={"agy": admission})
    monkeypatch.setattr(runtime, "executable_digest", lambda _: original.executable_sha256)
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission, cli="agy")
    assert caught.value.code == "policy_unverified"


@pytest.mark.parametrize("change", ["missing", "tampered", "oversized", "grok_generation", "native_task"])
def test_agy_evidence_rejected_before_binary_identity(tmp_path, monkeypatch, change):
    evidence = tmp_path / "observations.json"
    admission = _synthetic_agy_admission(tmp_path)
    evidence.write_bytes(Path(admission.evidence_path).read_bytes())
    admission = replace(admission, evidence_path=str(evidence))
    if change == "missing":
        evidence.unlink()
    elif change == "tampered":
        evidence.write_bytes(evidence.read_bytes() + b"\n")
    elif change == "oversized":
        content = b" " * (1024 * 1024 + 1)
        evidence.write_bytes(content)
        admission = replace(admission, evidence_sha256=sha256(content))
    else:
        filename = (
            "native-generation-grok-1.0.70-admission.json"
            if change == "grok_generation"
            else "native-agy-1.4.12-admission.json"
        )
        evidence.write_bytes((ROOT / "clink/resources" / filename).read_bytes())
    service = NativeGenerationService(family_admissions={"agy": admission})
    monkeypatch.setattr(
        runtime, "executable_digest", lambda _: pytest.fail("unmatched evidence reached binary identity")
    )
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission, cli="agy")
    assert caught.value.code == "policy_unverified"


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("tested_candidates", 0),
        ("tested_candidates", 1),
        ("tested_candidates", 0, "model_selection"),
        ("budget",),
        ("budget", "max_per_gate"),
        ("submissions", 0),
        ("submissions", 4),
        ("submissions", 4, "preflight"),
        ("gates", 0),
        ("gates", 3, "fixture_trust"),
        ("gates", 0, "observation"),
        ("gates", 3, "observation"),
        ("gates", 0, "observation", "observations"),
        ("replacement_authorizations", 0),
        ("reconciliation",),
        ("limitations",),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_observation_contract_rejects_nonclosed_objects(path, change, tmp_path, monkeypatch):
    record = _load(RESOURCE.read_bytes())
    selected = record
    for key in path:
        selected = selected[key]
    if change == "missing":
        selected.pop(next(iter(selected)))
    else:
        selected["activation_review"] = "not observation authority"
    _reject_changed_projection(record, "grok", tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("adapter",), "agy"),
        (("budget", "consumed"), True),
        (("budget", "automatic_retries"), 1),
        (("budget", "max_per_gate", "cancel"), True),
        (("budget", "replacements_used"), 0),
        (("submissions", 0, "ordinal"), True),
        (("submissions", 3, "passed"), False),
        (("submissions", 4, "passed"), False),
        (("submissions", 0, "passed"), 1),
        (("submissions", 0, "replacement"), True),
        (("submissions", 4, "replacement_basis"), "failed_gate"),
        (("submissions", 4, "candidate_sha256"), "0" * 64),
        (("submissions", 4, "reservation", "sha256"), "0" * 64),
        (("submissions", 4, "receipt", "sha256"), "0" * 64),
        (("submissions", 0, "preflight", "path"), "/private/copy.json"),
        (("submissions", 0, "preflight", "path"), "docs/../copy.json"),
        (("submissions", 0, "preflight", "sha256"), "F" * 64),
        (("tested_candidates", 0, "requested_model"), "grok-4.7-build"),
        (("tested_candidates", 1, "effective_model"), "grok-4.7-build"),
        (("tested_candidates", 1, "implementation_source_sha256"), "0" * 64),
        (("tested_candidates", 0, "model_selection", "kind"), "explicit"),
        (("replacement_authorizations", 0, "replacement_basis"), "failed_gate"),
        (("replacement_authorizations", 0, "independent_review_sha256"), "0" * 64),
        (("replacement_authorizations", 0, "runtime_attestation_sha256"), "0" * 64),
        (("reconciliation", "excluded_ordinal"), 5),
        (("reconciliation", "compatibility_binding_sha256"), "0" * 64),
        (("reconciliation", "corrected_candidate_sha256"), "0" * 64),
        (("reconciliation", "non_cancellation_proof_sha256"), "0" * 64),
        (("gates", 3, "ordinal"), 4),
        (("gates", 0, "host_configuration_restored"), False),
        (("gates", 3, "fixture_trust", "fixture_trust_removed"), False),
        (("gates", 0, "observation", "dispatch_count"), True),
        (("gates", 0, "observation", "spawn_count"), MAX_METADATA_NUMBER + 1),
        (("gates", 0, "observation", "output_bytes"), OUTPUT_BYTES + 1),
        (("gates", 0, "observation", "pool_running_after_shutdown"), -1),
        (("gates", 1, "observation", "actual_model"), "unproven"),
        (("gates", 2, "observation", "error_code"), None),
        (("gates", 2, "observation", "observations", "protected_file_untouched"), 1),
        (("gates", 3, "observation", "process_groups_absent_after_cleanup"), None),
        (
            ("gates", 3, "observation", "pool_exception_observation"),
            {"exception_class": "PermissionError", "phase": "production_cleanup"},
        ),
        (("gates", 3, "observation", "pool_exception_diagnostic_capture_failed"), True),
        (("limitations", "candidate_only_live_coverage"), False),
    ],
)
def test_observation_contract_rejects_changed_history_authority_or_gate_claims(path, value, tmp_path, monkeypatch):
    record = _load(RESOURCE.read_bytes())
    selected = record
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = value
    _reject_changed_projection(record, "grok", tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "field,index",
    [
        ("submissions", 3),
        ("submissions", 4),
        ("tested_candidates", 0),
        ("tested_candidates", 1),
        ("replacement_authorizations", 0),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_observation_rejects_omitted_or_extra_candidates_submissions_and_replacements(
    field, index, change, tmp_path, monkeypatch
):
    record = _load(RESOURCE.read_bytes())
    if change == "missing":
        record[field].pop(index)
    else:
        record[field].append(copy.deepcopy(record[field][index]))
    _reject_changed_projection(record, "grok", tmp_path, monkeypatch)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "filename",
    [
        "replacement-review.json",
        "compatibility.json",
        "source-binding.json",
        "runtime-attestation.json",
        "reviewed-input.json",
        "production-equivalence.json",
        "observer-counterexample.json",
        "non-cancellation-proof.md",
    ],
)
def test_changed_retained_proof_bytes_fail_closed(tmp_path, filename, private_evidence_root):
    target = tmp_path / PROOFS
    target.mkdir(parents=True)
    for path in (private_evidence_root / PROOFS).iterdir():
        (target / path.name).write_bytes(path.read_bytes() + (b"\n" if path.name == filename else b""))
    with pytest.raises(AssertionError):
        _validate(_load(RESOURCE.read_bytes()), root=tmp_path)


@pytest.mark.private_evidence
@pytest.mark.parametrize("filename", ["preflight.json", "submitted.json", "receipt.json"])
def test_missing_actual_fifth_evidence_cannot_establish_admission(tmp_path, filename, private_evidence_root):
    directory = tmp_path / DIRECTORY
    _copy_mutable_evidence(private_evidence_root / DIRECTORY, directory)
    (directory / "20261103-cancel-r2" / filename).unlink()
    with pytest.raises(FileNotFoundError):
        _validate(_load(RESOURCE.read_bytes()), root=tmp_path)


def test_recomputed_manifest_hash_cannot_hide_a_production_component_change():
    equivalence, candidates = _synthetic_manifests()
    changed = next(item for item in equivalence["corrected_components"]["files"] if item["path"] == "clink/runner.py")
    original = changed["sha256"]
    changed["sha256"] = "0" * 64
    equivalence["delta"].append(
        {"path": changed["path"], "original_sha256": original, "corrected_sha256": changed["sha256"]}
    )
    equivalence["delta"].sort(key=lambda item: item["path"])
    digest = sha256(canonical_json(equivalence["corrected_components"]))
    candidates[1]["implementation_source_sha256"] = equivalence["corrected_implementation_source_sha256"] = digest
    with pytest.raises(AssertionError):
        _manifests(equivalence, candidates)


@pytest.mark.private_evidence
def test_synthetic_receipt_cannot_replace_actual_fifth_even_with_repinned_resource(tmp_path, private_evidence_root):
    record = _load(RESOURCE.read_bytes())
    item = record["submissions"][-1]
    fake = _load((private_evidence_root / item["receipt"]["path"]).read_bytes())
    fake["all_family_generation_gates_passed"] = True
    content = canonical_json(fake)
    item["receipt"]["sha256"] = sha256(content)
    directory = tmp_path / DIRECTORY
    directory.mkdir(parents=True)
    # Preserve all other retained inputs; only the claimed terminal is replaced.
    _copy_mutable_evidence(private_evidence_root / DIRECTORY, directory, dirs_exist_ok=True)
    (tmp_path / item["receipt"]["path"]).write_bytes(content)
    with pytest.raises(AssertionError):
        _validate(record, root=tmp_path)


@pytest.mark.parametrize(
    "content",
    [
        b'{"scope":"one","scope":"two"}',
        b'{"nested":{"same":0,"same":1}}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b" " * 65537,
        b"\xff",
    ],
)
def test_observation_json_refuses_duplicate_nonfinite_oversized_or_nonutf8(content):
    with pytest.raises((AssertionError, ValueError, UnicodeError)):
        _load(content)


def test_default_grok_pin_verifies_resource_and_binary_without_spawning(monkeypatch):
    monkeypatch.setattr(runtime, "executable_digest", lambda _: DEFAULT_GROK_NATIVE_ADMISSION.executable_sha256)
    service = NativeGenerationService()
    assert (
        service._verify_admission("identity-only", service._admission_for("grok"), cli="grok")
        == DEFAULT_GROK_NATIVE_ADMISSION.executable_sha256
    )


@pytest.mark.parametrize("field", ["executable_sha256", "recipe_sha256", "parser_sha256"])
def test_grok_binary_recipe_or_parser_mismatch_fails_closed(monkeypatch, field):
    admission = replace(DEFAULT_GROK_NATIVE_ADMISSION, **{field: "0" * 64})
    service = NativeGenerationService(family_admissions={"grok": admission})
    monkeypatch.setattr(runtime, "executable_digest", lambda _: DEFAULT_GROK_NATIVE_ADMISSION.executable_sha256)
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission, cli="grok")
    assert caught.value.code == "policy_unverified"


@pytest.mark.parametrize(
    "change", ["missing", "tampered", "oversized", "native_task", "claude_generation", "codex_generation"]
)
def test_grok_evidence_rejected_before_binary_identity(tmp_path, monkeypatch, change):
    evidence = tmp_path / "observations.json"
    evidence.write_bytes(RESOURCE.read_bytes())
    admission = replace(DEFAULT_GROK_NATIVE_ADMISSION, evidence_path=str(evidence))
    if change == "missing":
        evidence.unlink()
    elif change == "tampered":
        evidence.write_bytes(evidence.read_bytes() + b"\n")
    elif change == "oversized":
        content = b" " * (1024 * 1024 + 1)
        evidence.write_bytes(content)
        admission = replace(admission, evidence_sha256=sha256(content))
    else:
        filename = {
            "native_task": "native-grok-1.0.70-admission.json",
            "claude_generation": "native-generation-claude-2.1.304-admission.json",
            "codex_generation": "native-generation-codex-0.166.0-admission.json",
        }[change]
        evidence.write_bytes((ROOT / "clink/resources" / filename).read_bytes())
    service = NativeGenerationService(family_admissions={"grok": admission})
    monkeypatch.setattr(
        runtime, "executable_digest", lambda _: pytest.fail("unmatched evidence reached binary identity")
    )
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission, cli="grok")
    assert caught.value.code == "policy_unverified"


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "field,index",
    [
        ("submissions", 3),
        ("submissions", 4),
        ("tested_candidates", 0),
        ("tested_candidates", 1),
        ("replacement_authorizations", 0),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_private_observation_rejects_omitted_or_extra_candidates_submissions_and_replacements(
    field, index, change, private_evidence_root
):
    record = _load(RESOURCE.read_bytes())
    if change == "missing":
        record[field].pop(index)
    else:
        record[field].append(copy.deepcopy(record[field][index]))
    with pytest.raises(AssertionError):
        _validate(record, root=private_evidence_root)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("adapter",), "agy"),
        (("budget", "consumed"), True),
        (("budget", "automatic_retries"), 1),
        (("budget", "max_per_gate", "cancel"), True),
        (("budget", "replacements_used"), 0),
        (("submissions", 0, "ordinal"), True),
        (("submissions", 3, "passed"), False),
        (("submissions", 4, "passed"), False),
        (("submissions", 0, "passed"), 1),
        (("submissions", 0, "replacement"), True),
        (("submissions", 4, "replacement_basis"), "failed_gate"),
        (("submissions", 4, "candidate_sha256"), "0" * 64),
        (("submissions", 4, "reservation", "sha256"), "0" * 64),
        (("submissions", 4, "receipt", "sha256"), "0" * 64),
        (("submissions", 0, "preflight", "path"), "/private/copy.json"),
        (("submissions", 0, "preflight", "path"), "docs/../copy.json"),
        (("submissions", 0, "preflight", "sha256"), "F" * 64),
        (("tested_candidates", 0, "requested_model"), "grok-4.7-build"),
        (("tested_candidates", 1, "effective_model"), "grok-4.7-build"),
        (("tested_candidates", 1, "implementation_source_sha256"), "0" * 64),
        (("tested_candidates", 0, "model_selection", "kind"), "explicit"),
        (("replacement_authorizations", 0, "replacement_basis"), "failed_gate"),
        (("replacement_authorizations", 0, "independent_review_sha256"), "0" * 64),
        (("replacement_authorizations", 0, "runtime_attestation_sha256"), "0" * 64),
        (("reconciliation", "excluded_ordinal"), 5),
        (("reconciliation", "compatibility_binding_sha256"), "0" * 64),
        (("reconciliation", "corrected_candidate_sha256"), "0" * 64),
        (("reconciliation", "non_cancellation_proof_sha256"), "0" * 64),
        (("gates", 3, "ordinal"), 4),
        (("gates", 0, "host_configuration_restored"), False),
        (("gates", 3, "fixture_trust", "fixture_trust_removed"), False),
        (("gates", 0, "observation", "dispatch_count"), True),
        (("gates", 0, "observation", "spawn_count"), MAX_METADATA_NUMBER + 1),
        (("gates", 0, "observation", "output_bytes"), OUTPUT_BYTES + 1),
        (("gates", 0, "observation", "pool_running_after_shutdown"), -1),
        (("gates", 1, "observation", "actual_model"), "unproven"),
        (("gates", 2, "observation", "error_code"), None),
        (("gates", 2, "observation", "observations", "protected_file_untouched"), 1),
        (("gates", 3, "observation", "process_groups_absent_after_cleanup"), None),
        (
            ("gates", 3, "observation", "pool_exception_observation"),
            {"exception_class": "PermissionError", "phase": "production_cleanup"},
        ),
        (("gates", 3, "observation", "pool_exception_diagnostic_capture_failed"), True),
        (("limitations", "candidate_only_live_coverage"), False),
    ],
)
def test_private_observation_contract_rejects_changed_history_authority_or_gate_claims(
    path, value, private_evidence_root
):
    record = _load(RESOURCE.read_bytes())
    selected = record
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = value
    with pytest.raises(AssertionError):
        _validate(record, root=private_evidence_root)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "path",
    [
        (),
        ("tested_candidates", 0),
        ("tested_candidates", 1),
        ("tested_candidates", 0, "model_selection"),
        ("budget",),
        ("budget", "max_per_gate"),
        ("submissions", 0),
        ("submissions", 4),
        ("submissions", 4, "preflight"),
        ("gates", 0),
        ("gates", 3, "fixture_trust"),
        ("gates", 0, "observation"),
        ("gates", 3, "observation"),
        ("gates", 0, "observation", "observations"),
        ("replacement_authorizations", 0),
        ("reconciliation",),
        ("limitations",),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_private_observation_contract_rejects_nonclosed_objects(path, change, private_evidence_root):
    record = _load(RESOURCE.read_bytes())
    selected = record
    for key in path:
        selected = selected[key]
    if change == "missing":
        selected.pop(next(iter(selected)))
    else:
        selected["activation_review"] = "not observation authority"
    with pytest.raises(AssertionError):
        _validate(record, root=private_evidence_root)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "path",
    [
        (),
        ("tested_candidates", 2),
        ("budget",),
        ("budget", "max_per_gate"),
        ("submissions", 3),
        ("submissions", 4),
        ("submissions", 5),
        ("submissions", 5, "diagnostic_publication"),
        ("submissions", 5, "receipt"),
        ("gates", 2, "observation", "observations"),
        ("gates", 3, "observation"),
        ("gates", 3, "observation", "diagnostic_publication"),
        ("replacement_authorizations", 0),
        ("replacement_authorizations", 1),
        ("replacement_authorizations", 1, "diagnostic_limits"),
        ("reconciliation",),
        ("limitations",),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_private_agy_observation_contract_rejects_nonclosed_objects(path, change, private_evidence_root):
    record = _load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes())
    selected = record
    for key in path:
        selected = selected[key]
    if change == "missing":
        selected.pop(next(iter(selected)))
    else:
        selected["admission_review"] = "not observation authority"
    with pytest.raises(AssertionError):
        _validate_agy(record, root=private_evidence_root)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("schema_version",), 3),
        (("adapter",), "grok"),
        (("budget", "consumed"), 7),
        (("budget", "automatic_retries"), 1),
        (("budget", "max_per_gate", "cancel"), 2),
        (("budget", "max_replacements"), True),
        (("submissions", 3, "passed"), True),
        (("submissions", 4, "passed"), True),
        (("submissions", 3, "check"), "text"),
        (("submissions", 5, "ordinal"), 7),
        (("submissions", 5, "passed"), 1),
        (("submissions", 5, "diagnostic_only"), False),
        (("submissions", 5, "diagnostic_purpose"), "ordinary_admission"),
        (("submissions", 5, "diagnostic_observation_complete"), False),
        (("submissions", 5, "diagnostic_sidecar_sha256"), "0" * 64),
        (("submissions", 5, "user_authorization_sha256"), "0" * 64),
        (("submissions", 5, "diagnostic_publication", "run_id"), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        (("submissions", 5, "diagnostic_publication", "result_sha256"), "0" * 64),
        (("submissions", 5, "diagnostic_publication", "sidecar_sha256"), "0" * 64),
        (("submissions", 5, "diagnostic_publication", "published"), False),
        (("submissions", 4, "replacement_basis"), "invalidated_cleanup_evidence"),
        (("submissions", 5, "replacement_basis"), "failed_gate"),
        (("submissions", 5, "receipt", "path"), "/private/copy.json"),
        (("submissions", 5, "receipt", "sha256"), "0" * 64),
        (("tested_candidates", 2, "source_commit"), CORRECTED_COMMIT),
        (("tested_candidates", 2, "candidate_sha256"), "0" * 64),
        (("tested_candidates", 0, "effective_model"), "unreported-model"),
        (("replacement_authorizations", 0, "review_packet_sha256"), PACKET_SHA256),
        (("replacement_authorizations", 1, "user_authorization_sha256"), "0" * 64),
        (("replacement_authorizations", 1, "runtime_attestation_sha256"), "0" * 64),
        (("replacement_authorizations", 1, "prior_ledger_sha256"), "0" * 64),
        (("replacement_authorizations", 1, "diagnostic_limits", "total_calls"), 6.0),
        (("replacement_authorizations", 1, "diagnostic_purpose"), "admission"),
        (("reconciliation", "excluded_failed_ordinals"), [4]),
        (("reconciliation", "observer_compatibility_binding_sha256"), "0" * 64),
        (("reconciliation", "diagnostic_compatibility_binding_sha256"), "0" * 64),
        (("reconciliation", "production_equivalence_sha256"), "0" * 64),
        (("reconciliation", "admission_interpretation_sha256"), "0" * 64),
        (("reconciliation", "non_cancellation_proof_sha256"), "0" * 64),
        (("gates", 3, "ordinal"), 5),
        (("gates", 3, "fixture_trust_requested"), True),
        (("gates", 2, "observation", "observations", "permission_hook_observed"), False),
        (("gates", 3, "observation", "process_groups_absent_after_cleanup"), False),
        (("gates", 3, "observation", "process_groups_absent_after_cleanup"), None),
        (("gates", 3, "observation", "pool_running_after_shutdown"), 1),
        (("gates", 3, "observation", "dispatch_count"), True),
        (("gates", 3, "observation", "private_input_removed"), False),
        (("gates", 3, "observation", "observations", "fixture_configuration_intact"), False),
        (("gates", 3, "observation", "observations", "synthetic_child_absent"), False),
        (("gates", 3, "observation", "observations", "synthetic_child_identity_observed"), None),
        (("gates", 3, "observation", "observations", "synthetic_child_ready"), False),
        (("limitations", "prior_cleanup_failure_cause_known"), True),
        (("limitations", "prior_cleanup_failure_resolved"), True),
        (("limitations", "diagnostic_child_identity_known"), True),
    ],
)
def test_private_agy_observation_contract_rejects_relabelled_failures_or_false_authority(
    path, value, private_evidence_root
):
    record = _load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes())
    selected = record
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = value
    with pytest.raises(AssertionError):
        _validate_agy(record, root=private_evidence_root)


@pytest.mark.private_evidence
def test_private_agy_cannot_borrow_grok_observations_or_review_binding(private_evidence_root):
    with pytest.raises(AssertionError):
        _validate_agy(_load(RESOURCE.read_bytes()), root=private_evidence_root)
    record = _load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes())
    record["tested_candidates"][1] = _load(RESOURCE.read_bytes())["tested_candidates"][1]
    with pytest.raises(AssertionError):
        _validate_agy(record, root=private_evidence_root)


@pytest.mark.private_evidence
@pytest.mark.parametrize(
    "field,index",
    [
        ("submissions", 3),
        ("submissions", 4),
        ("submissions", 5),
        ("tested_candidates", 0),
        ("tested_candidates", 1),
        ("tested_candidates", 2),
        ("replacement_authorizations", 0),
        ("replacement_authorizations", 1),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_private_agy_projection_forbids_omitted_failures_conflated_candidates_or_seventh_calls(
    field, index, change, private_evidence_root
):
    record = _load((private_evidence_root / AGY_RESOURCE_PATH).read_bytes())
    if change == "missing":
        record[field].pop(index)
    else:
        record[field].append(copy.deepcopy(record[field][index]))
    with pytest.raises(AssertionError):
        _validate_agy(record, root=private_evidence_root)


@pytest.mark.private_evidence
def test_private_grok_observations_preserve_two_candidates_five_submissions_and_selected_gates(private_evidence_root):
    content = RESOURCE.read_bytes()
    record = _load(content)
    _validate(record, root=private_evidence_root)
    admission = DEFAULT_GROK_NATIVE_ADMISSION
    assert admission.verified is True and sha256(content) == admission.evidence_sha256
    assert content == (private_evidence_root / DIRECTORY / RESOURCE.name).read_bytes()
    assert admission.version == record["observed_version"]
    assert admission.executable_sha256 == record["executable_sha256"]
    assert admission.recipe_sha256 == record["recipe_sha256"] == family_generation_recipe_digest("grok")
    assert admission.parser_sha256 == record["parser_sha256"] == family_generation_parser_digest("grok")
    _grok_current_source(record, root=private_evidence_root)
    assert [gate["ordinal"] for gate in record["gates"]] == [1, 2, 3, 5]
    assert record["submissions"][3]["passed"] is True and record["reconciliation"]["excluded_ordinal"] == 4


@pytest.mark.private_evidence
def test_private_recomputed_manifest_hash_cannot_hide_a_production_component_change(private_evidence_root):
    record = _load(RESOURCE.read_bytes())
    _, _, _, equivalence = _proofs(private_evidence_root)
    candidates = record["tested_candidates"]
    changed = next(item for item in equivalence["corrected_components"]["files"] if item["path"] == "clink/runner.py")
    original = changed["sha256"]
    changed["sha256"] = "0" * 64
    equivalence["delta"].append(
        {"path": changed["path"], "original_sha256": original, "corrected_sha256": changed["sha256"]}
    )
    equivalence["delta"].sort(key=lambda item: item["path"])
    digest = sha256(canonical_json(equivalence["corrected_components"]))
    candidates[1]["implementation_source_sha256"] = equivalence["corrected_implementation_source_sha256"] = digest
    with pytest.raises(AssertionError):
        _manifests(equivalence, candidates)


@pytest.mark.private_evidence
def test_private_explicitly_injected_agy_admission_verifies_proposal_bytes_without_spawning(
    monkeypatch, private_evidence_root
):
    admission = _injected_agy_admission(private_evidence_root / AGY_RESOURCE_PATH)
    monkeypatch.setattr(runtime, "executable_digest", lambda _: admission.executable_sha256)
    service = NativeGenerationService(family_admissions={"agy": admission})
    assert (
        service._verify_admission("identity-only", service._admission_for("agy"), cli="agy")
        == admission.executable_sha256
    )


@pytest.mark.private_evidence
def test_released_family_runtime_components_match_frozen_originals(private_evidence_root):
    """Check active runtime bytes separately from historical verifier-script equivalence."""
    helper = _pinned(
        private_evidence_root, f"{PROOFS}/helper-current-source-equivalence.json", GROK_HELPER_EQUIVALENCE_SHA256
    )
    expected = {item["path"] for item in helper["reviewed_components"]["files"] if item["path"].startswith("clink/")}
    released = {path.relative_to(ROOT).as_posix() for path in (ROOT / "clink").rglob("*.py")}
    assert released == expected
    for filename in sorted(released):
        assert (ROOT / filename).read_bytes() == (private_evidence_root / filename).read_bytes(), filename
