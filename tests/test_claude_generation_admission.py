"""Validate portable Claude observation pins without opening private review evidence."""

import copy
import json
import re
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

import clink.native_generation as module
from clink.contracts import BackendError
from clink.generation_contracts import OUTPUT_BYTES, canonical_json, sha256
from clink.generation_policy import DEFAULT_ADMISSION
from clink.native import NativeRuntime
from clink.native_generation import NativeGenerationService
from clink.native_generation_claude_policy import DEFAULT_CLAUDE_NATIVE_ADMISSION, claude_generation_recipe_digest
from clink.native_generation_policy import DEFAULT_NATIVE_ADMISSION, NativeGenerationAdmission
from clink.parsers.base import MAX_METADATA_NUMBER, model_identifier
from clink.parsers.claude_native_generation import parser_contract_digest

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / "clink/resources/native-generation-claude-2.1.304-admission.json"
TOP_FIELDS = {
    "schema_version",
    "scope",
    "profile",
    "adapter",
    "observed_version",
    "executable_sha256",
    "recipe_sha256",
    "parser_sha256",
    "tested_candidate",
    "budget",
    "submissions",
    "gates",
    "replacement_authorizations",
    "limitations",
}
OBSERVATION_FIELDS = {
    "passed",
    "terminal_status",
    "error_code",
    "actual_model",
    "dispatch_count",
    "spawn_count",
    "production_service_used",
    "native_terminal_observed",
    "assistant_activity_before_cancellation",
    "useful_transformation",
    "unprovided_file_token_matches",
    "read_tool_uses",
    "output_bytes",
    "output_sha256",
    "fixture_hook_observations",
    "process_groups_absent_after_cleanup",
    "pool_running_after_shutdown",
    "pool_children_after_shutdown",
    "raw_output_retained",
}
LIMITATIONS = {
    "historical_failure_cause": "unproven",
    "candidate_only_live_coverage": True,
    "permission_observability": "limited",
    "fulfillment_verified": False,
    "side_effects_possible": True,
    "cancellation_rolls_back_effects": False,
    "detached_or_remote_cleanup_verified": False,
    "native_internal_work_bounded_by_top_level_budget": False,
    "native_authentication_independently_verified": False,
    "native_task_admission_reused": False,
}


def _fields(value, expected):
    assert type(value) is dict and set(value) == set(expected)


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


def _validate(record):
    """Test the frozen record contract; references are metadata, never opened here."""
    _fields(record, TOP_FIELDS)
    assert type(record["schema_version"]) is int and record["schema_version"] == 2
    assert record["scope"] == "claude_native_generation_observations"
    assert record["profile"] == "generation_native_v1" and record["adapter"] == "claude"
    version = record["observed_version"]
    assert isinstance(version, str) and re.fullmatch(r"[0-9][A-Za-z0-9. ()_-]{0,127}", version)
    for name in ("executable_sha256", "recipe_sha256", "parser_sha256"):
        _digest(record[name])
    candidate = record["tested_candidate"]
    _fields(
        candidate,
        {
            "candidate_sha256",
            "package_source_sha256",
            "implementation_source_sha256",
            "verifier_sha256",
            "python_version",
            "python_executable_sha256",
            "requested_model",
        },
    )
    for name in set(candidate) - {"python_version", "requested_model"}:
        _digest(candidate[name])
    assert (
        isinstance(candidate["python_version"], str)
        and len(candidate["python_version"]) <= 32
        and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", candidate["python_version"])
    )
    model = candidate["requested_model"]
    assert (
        isinstance(model, str)
        and model_identifier(model) == model
        and model.startswith("claude-")
        and any(char.isdigit() for char in model)
    )
    fingerprint = {
        **{key: record[key] for key in ("executable_sha256", "recipe_sha256", "parser_sha256")},
        "model": model,
        **{
            key: candidate[key]
            for key in (
                "package_source_sha256",
                "python_executable_sha256",
                "implementation_source_sha256",
                "verifier_sha256",
            )
        },
    }
    assert candidate["candidate_sha256"] == sha256(canonical_json(fingerprint))
    budget = record["budget"]
    _fields(
        budget,
        {
            "policy",
            "max_total",
            "max_per_gate",
            "max_replacements",
            "consumed",
            "replacements_used",
            "automatic_retries",
        },
    )
    for name in set(budget) - {"policy", "max_per_gate"}:
        _count(budget[name], 5)
    _fields(budget["max_per_gate"], {"text", "read", "cancel"})
    for value in budget["max_per_gate"].values():
        _count(value, 3)
    assert budget == {
        "policy": "claude_text_third_attempt_v1",
        "max_total": 5,
        "max_per_gate": {"text": 3, "read": 1, "cancel": 1},
        "max_replacements": 2,
        "consumed": 5,
        "replacements_used": 2,
        "automatic_retries": 0,
    }
    submissions = record["submissions"]
    assert type(submissions) is list and len(submissions) == 5
    for index, item in enumerate(submissions):
        _fields(
            item,
            {"ordinal", "check", "passed", "replacement", "candidate_sha256", "preflight", "reservation", "receipt"},
        )
        _count(item["ordinal"], 5)
        assert item["ordinal"] == index + 1 and item["check"] == ("text", "text", "text", "read", "cancel")[index]
        assert item["passed"] is (index >= 2)
        assert item["replacement"] is (index in (1, 2))
        _digest(item["candidate_sha256"])
        if index >= 2:
            assert item["candidate_sha256"] == candidate["candidate_sha256"]
        label = ("text-1", "text-2", "text-3", "read-4", "cancel-5")[index]
        for name, filename in (
            ("preflight", "preflight.json"),
            ("reservation", "submitted.json"),
            ("receipt", "receipt.json"),
        ):
            _reference(item[name])
            assert item[name]["path"] == f"docs/verification/claude-generation/release-020-{label}/{filename}"
    assert len({item["candidate_sha256"] for item in submissions[:3]}) == 3
    authorities = record["replacement_authorizations"]
    assert type(authorities) is list and len(authorities) == 2
    for index, authority in enumerate(authorities):
        _fields(
            authority,
            {
                "kind",
                "failed_ordinal",
                "replacement_ordinal",
                "packet",
                "reviewed_input",
                "independent_review",
                "completion_attestation",
            },
        )
        assert authority["kind"] == ("legacy_replacement", "bounded_exception")[index]
        for name in ("failed_ordinal", "replacement_ordinal"):
            _count(authority[name], 3)
        assert authority["failed_ordinal"] == index + 1 and authority["replacement_ordinal"] == index + 2
        for name in ("packet", "reviewed_input", "independent_review", "completion_attestation"):
            _reference(authority[name])
    gates = record["gates"]
    assert type(gates) is list and len(gates) == 3
    for index, gate in enumerate(gates):
        _fields(gate, {"check", "ordinal", "observation"})
        _count(gate["ordinal"], 5)
        assert gate["check"] == ("text", "read", "cancel")[index] and gate["ordinal"] == index + 3
        observed = gate["observation"]
        _fields(observed, OBSERVATION_FIELDS)
        for name in (
            "dispatch_count",
            "spawn_count",
            "read_tool_uses",
            "pool_running_after_shutdown",
            "pool_children_after_shutdown",
        ):
            _count(observed[name])
        _count(observed["output_bytes"], OUTPUT_BYTES)
        for name in ("passed", "production_service_used", "process_groups_absent_after_cleanup"):
            assert observed[name] is True
        assert observed["raw_output_retained"] is False
        assert observed["dispatch_count"] == observed["spawn_count"] == 1
        assert observed["pool_running_after_shutdown"] == observed["pool_children_after_shutdown"] == 0
        hooks = observed["fixture_hook_observations"]
        _fields(hooks, {"controls_unchanged", "session_start_observed", "token_read_observations", "tool_denials"})
        assert hooks["controls_unchanged"] is hooks["session_start_observed"] is True
        _count(hooks["token_read_observations"])
        _count(hooks["tool_denials"])
        assert hooks["tool_denials"] == 0
        assert observed["useful_transformation"] is (True if index == 0 else None)
        assert observed["unprovided_file_token_matches"] is (True if index == 1 else None)
        if index == 2:
            assert observed["terminal_status"] == observed["error_code"] == "cancelled"
            assert (
                observed["native_terminal_observed"] is False
                and observed["assistant_activity_before_cancellation"] is True
            )
            assert observed["actual_model"] is observed["output_sha256"] is None and observed["output_bytes"] == 0
        else:
            assert observed["terminal_status"] == "completed" and observed["error_code"] is None
            assert (
                observed["native_terminal_observed"] is True
                and observed["assistant_activity_before_cancellation"] is None
            )
            assert observed["actual_model"] == model and observed["output_bytes"] > 0
            _digest(observed["output_sha256"])
        if index == 1:
            assert observed["read_tool_uses"] > 0 and hooks["token_read_observations"] > 0
    _fields(record["limitations"], LIMITATIONS)
    for key, value in LIMITATIONS.items():
        if type(value) is bool:
            assert record["limitations"][key] is value
        else:
            assert record["limitations"][key] == value


def test_production_observations_are_closed_portable_and_preserve_original_history():
    content = RESOURCE.read_bytes()
    record = _load(content)
    _validate(record)
    admission = DEFAULT_CLAUDE_NATIVE_ADMISSION
    assert admission.verified is True and sha256(content) == admission.evidence_sha256
    assert record["observed_version"] == admission.version
    assert record["executable_sha256"] == admission.executable_sha256
    assert record["recipe_sha256"] == admission.recipe_sha256 == claude_generation_recipe_digest()
    assert record["parser_sha256"] == admission.parser_sha256 == parser_contract_digest()
    assert (
        record["tested_candidate"]["candidate_sha256"]
        == "0a73c562814ffb3787986c64eb33b406aef6cbca0b02af8689eec2a513301812"
    )
    assert [gate["observation"]["output_bytes"] for gate in record["gates"]] == [969, 55, 0]
    assert [gate["observation"]["output_sha256"] for gate in record["gates"]] == [
        "5ef075412b4fb6c29d5b68a5ff3d7ac8b8b09c2996ea0bc6e499170cb7ac243b",
        "e0968184d06da07a3d0cffef64c6975a9ccda2079b0a9e61246ab19d62ce6e09",
        None,
    ]
    assert [authority["packet"]["sha256"] for authority in record["replacement_authorizations"]] == [
        "d059e0a7faccb72307d3116d4e8ed49d1b92c8e0f44314be504c6427f589e690",
        "dbf451cf606e30408b4ab2b978f4a61d373657d235af163876fcb9c0f152431c",
    ]
    assert DEFAULT_NATIVE_ADMISSION.verified is True and DEFAULT_ADMISSION.verified is False
    assert NativeRuntime._policy("codex").verified is False


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("tested_candidate",),
        ("budget",),
        ("budget", "max_per_gate"),
        ("submissions", 0),
        ("submissions", 0, "preflight"),
        ("gates", 0),
        ("gates", 0, "observation"),
        ("gates", 0, "observation", "fixture_hook_observations"),
        ("replacement_authorizations", 0),
        ("limitations",),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_observation_contract_rejects_nonclosed_objects(path, change):
    record = _load(RESOURCE.read_bytes())
    selected = record
    for key in path:
        selected = selected[key]
    if change == "missing":
        selected.pop(next(iter(selected)))
    else:
        selected["future_review"] = "not admitted"
    with pytest.raises(AssertionError):
        _validate(record)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("budget", "consumed"), True),
        (("budget", "automatic_retries"), 1),
        (("submissions", 0, "ordinal"), True),
        (("submissions", 0, "passed"), 0),
        (("submissions", 1, "passed"), True),
        (("submissions", 2, "candidate_sha256"), "0" * 64),
        (("submissions", 0, "preflight", "path"), "/private/copy.json"),
        (("submissions", 0, "preflight", "path"), "docs/../copy.json"),
        (("submissions", 0, "preflight", "sha256"), "F" * 64),
        (("replacement_authorizations", 1, "failed_ordinal"), 1),
        (("tested_candidate", "requested_model"), "claude-another-1"),
        (("gates", 2, "observation", "actual_model"), "claude-opus-5-5"),
        (("gates", 2, "observation", "native_terminal_observed"), True),
        (("gates", 0, "observation", "output_bytes"), OUTPUT_BYTES + 1),
        (("gates", 0, "observation", "read_tool_uses"), -1),
        (("gates", 1, "observation", "read_tool_uses"), MAX_METADATA_NUMBER + 1),
        (("gates", 1, "observation", "fixture_hook_observations", "token_read_observations"), True),
        (("limitations", "candidate_only_live_coverage"), False),
    ],
)
def test_observation_contract_rejects_weakened_or_invented_values(path, value):
    record = copy.deepcopy(_load(RESOURCE.read_bytes()))
    selected = record
    for key in path[:-1]:
        selected = selected[key]
    selected[path[-1]] = value
    with pytest.raises(AssertionError):
        _validate(record)


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


@pytest.fixture
def synthetic_admission(tmp_path):
    evidence = tmp_path / "synthetic-admission.json"
    evidence.write_bytes(b'{"synthetic":true,"production_admission":false}')
    admission = NativeGenerationAdmission(
        version="synthetic-no-native-call",
        verified=True,
        executable_sha256="f" * 64,
        recipe_sha256=claude_generation_recipe_digest(),
        parser_sha256=parser_contract_digest(),
        evidence_path=str(evidence),
        evidence_sha256=sha256(evidence.read_bytes()),
    )
    return admission, evidence


def test_exact_claude_evidence_binding_uses_claude_recipe_without_spawning(synthetic_admission, monkeypatch):
    admission, _ = synthetic_admission
    observed = []

    def binary_identity(path):
        observed.append(path)
        return admission.executable_sha256

    monkeypatch.setattr(module, "executable_digest", binary_identity)
    service = NativeGenerationService(claude_admission=admission)
    assert service._verify_admission("not-launched", admission, cli="claude") == admission.executable_sha256
    assert observed == ["not-launched"]


def test_default_claude_pin_checks_installed_resource_without_spawning(monkeypatch):
    monkeypatch.setattr(module, "executable_digest", lambda _: DEFAULT_CLAUDE_NATIVE_ADMISSION.executable_sha256)
    service = NativeGenerationService()
    assert (
        service._verify_admission("identity-only", service.claude_admission, cli="claude")
        == service.claude_admission.executable_sha256
    )


@pytest.mark.parametrize("field", ["executable_sha256", "recipe_sha256", "parser_sha256"])
def test_default_claude_binary_recipe_or_parser_mismatch_fails_closed(monkeypatch, field):
    admission = replace(DEFAULT_CLAUDE_NATIVE_ADMISSION, **{field: "0" * 64})
    service = NativeGenerationService(claude_admission=admission)
    monkeypatch.setattr(module, "executable_digest", lambda _: DEFAULT_CLAUDE_NATIVE_ADMISSION.executable_sha256)
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission, cli="claude")
    assert caught.value.code == "policy_unverified"


@pytest.mark.parametrize("change", ["missing", "tampered", "oversized", "codex_generation", "native_task"])
def test_claude_evidence_rejected_before_binary_identity(synthetic_admission, monkeypatch, change):
    admission, evidence = synthetic_admission
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
            "codex_generation": "native-generation-codex-0.166.0-admission.json",
            "native_task": "native-claude-2.1.304-admission.json",
        }[change]
        evidence.write_bytes((ROOT / "clink/resources" / filename).read_bytes())
    service = NativeGenerationService(claude_admission=admission)
    monkeypatch.setattr(
        module, "executable_digest", lambda _: pytest.fail("unmatched evidence reached binary identity")
    )
    with pytest.raises(BackendError) as caught:
        service._verify_admission("not-launched", admission, cli="claude")
    assert caught.value.code == "policy_unverified"
