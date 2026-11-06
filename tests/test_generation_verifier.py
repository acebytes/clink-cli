"""Exercise separate generation evidence without installed CLIs or model calls."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clink import generation, generation_policy
from scripts import verify_cli_policy as advisory
from scripts import verify_generation_policy as verifier


@pytest.fixture
def durable_path(monkeypatch):
    parent = verifier.PROJECT / "test_output/generation-verifier-tests"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        root = Path(temporary)
        monkeypatch.setattr(verifier, "PROJECT", root)
        yield root


@pytest.fixture
def simulated_runtime_ceiling(monkeypatch):
    # Only synthetic fake-executable/ledger tests use this trusted-code patch.
    # It is not admission or evidence that the installed CLI has a tool ceiling.
    monkeypatch.setattr(generation_policy, "RUNTIME_BLOCKERS", ())
    monkeypatch.setattr(generation.GenerationService, "_require_runtime_ceiling", staticmethod(lambda: None))


def args(tmp_path: Path, **changes: object) -> argparse.Namespace:
    values = {
        "label": "test-first",
        "output_dir": tmp_path / "evidence",
        "source_proof": None,
        "source_root": None,
        "source_proof_sha256": None,
        "live": False,
        "model": "gpt-6.1-sol",
        "timeout": 120,
        "prior_evidence": None,
        "replace_failed_attempt": False,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def passing_preflight() -> dict:
    return {
        "version_matches": True,
        "catalog_proof": {"verified": True},
        "source_proof": {"verified": True},
        "synthetic_cancellation": {"passed": True},
        "runtime_preflight": {
            "status": "observed",
            "workspace_unchanged": True,
            "canary_marker_absent": True,
            "process_group_stopped": True,
            "controls": {
                **dict.fromkeys(verifier.CONTROL_NAMES, True),
                "disabled_features": dict.fromkeys(generation_policy.FEATURES_OFF, True),
            },
        },
        "native_sandbox_probe": {
            "exit_code": 0,
            "read_succeeded": True,
            "write_denied": True,
            "forbidden_file_absent": True,
            "workspace_unchanged": True,
        },
    }


@pytest.fixture
def patched_preflight(monkeypatch, tmp_path):
    binary = tmp_path / "codex"
    binary.write_text("synthetic installed identity")
    monkeypatch.setattr(verifier.shutil, "which", lambda _: str(binary))
    monkeypatch.setattr(advisory, "bounded_version", lambda *_: (0, verifier.EXPECTED_VERSION.encode()))
    monkeypatch.setattr(verifier, "catalog_proof", lambda: {"verified": True})
    monkeypatch.setattr(verifier, "load_source_proof", lambda *_: {"verified": True})
    preflight = passing_preflight()
    monkeypatch.setattr(verifier, "control_probe", lambda *_: copy.deepcopy(preflight["runtime_preflight"]))
    monkeypatch.setattr(verifier, "sandbox_probe", lambda *_: copy.deepcopy(preflight["native_sandbox_probe"]))
    monkeypatch.setattr(verifier, "synthetic_cancellation_probe", AsyncMock(return_value={"passed": True}))
    return binary


@pytest.fixture(params=["synthetic", pytest.param("historical", marks=pytest.mark.private_evidence)])
def source_catalog(request, monkeypatch, tmp_path):
    if request.param == "historical":
        archive = request.getfixturevalue("private_evidence_root")
        path = archive / "docs/verification/cli-policy/codex-0.166.0-model-catalog-original.json"
    else:
        # A synthetic native baseline exercises projection semantics without
        # presenting reconstructed bytes as original native evidence.
        baseline = json.loads(generation_policy.generation_catalog_path().read_bytes())
        for model in baseline["models"]:
            model.update(
                tool_mode="synthetic-native",
                shell_type="synthetic-shell",
                apply_patch_tool_type="synthetic-patch",
                experimental_supported_tools=["synthetic-tool"],
                supports_search_tool=True,
            )
        path = tmp_path / "synthetic-native-catalog.json"
        path.write_text(json.dumps(baseline))
        monkeypatch.setattr(verifier, "ORIGINAL_CATALOG_SHA256", verifier.digest(path.read_bytes()))
    monkeypatch.setattr(verifier, "ORIGINAL_CATALOG", path)
    return path


def test_catalog_projection_proves_exact_generation_changes_and_preserves_advisory(source_catalog):
    original_recipe = advisory.policy_recipe("codex")
    original_digest = advisory.CODEX_CATALOG_SHA256
    result = verifier.catalog_proof()
    assert result["verified"] is True
    assert result["model_count"] == 12
    assert result["other_fields_unchanged"] is True
    assert result["model_requests_submitted"] == 0
    assert result["only_projected_fields"] == sorted(verifier.CATALOG_PROJECTION)
    assert advisory.policy_recipe("codex") == original_recipe
    assert advisory.CODEX_CATALOG_SHA256 == original_digest
    assert 'web_search="live"' in original_recipe["argv"]
    assert 'web_search="disabled"' in verifier.control_arguments()


def test_catalog_projection_rejects_an_unrelated_model_field_change(source_catalog, monkeypatch, tmp_path):
    catalog = json.loads(generation_policy.generation_catalog_path().read_bytes())
    catalog["models"][0]["base_instructions"] = "unexpected change"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    monkeypatch.setattr(generation_policy, "generation_catalog_path", lambda: path)
    monkeypatch.setattr(generation_policy, "CATALOG_SHA256", verifier.digest(path.read_bytes()))
    assert verifier.catalog_proof()["verified"] is False


@pytest.mark.parametrize("field", verifier.CONTROL_NAMES)
def test_missing_runtime_control_fails_configuration_observation(field):
    record = passing_preflight()
    record["runtime_preflight"]["controls"].pop(field)
    assert verifier.configuration_observations_passed(record) is False


@pytest.mark.parametrize("feature", generation_policy.FEATURES_OFF)
def test_missing_core_utility_or_extension_feature_fails_configuration_observation(feature):
    record = passing_preflight()
    record["runtime_preflight"]["controls"]["disabled_features"].pop(feature)
    assert verifier.configuration_observations_passed(record) is False


@pytest.mark.parametrize(
    "category,field",
    [
        ("catalog_proof", "verified"),
        ("source_proof", "verified"),
        ("synthetic_cancellation", "passed"),
        ("runtime_preflight", "workspace_unchanged"),
        ("runtime_preflight", "canary_marker_absent"),
        ("runtime_preflight", "process_group_stopped"),
        ("native_sandbox_probe", "write_denied"),
        ("native_sandbox_probe", "forbidden_file_absent"),
    ],
)
def test_independent_proofs_cannot_be_assumed(category, field):
    record = passing_preflight()
    record[category][field] = False
    assert verifier.configuration_observations_passed(record) is False


def test_runtime_projection_excludes_host_values_and_is_closed_when_controls_missing():
    summary = verifier.runtime_summary({"api_key": "SECRET", "account": "PRIVATE", "features": {}})
    assert not any(summary["disabled_features"].values())
    assert all(summary[key] is False for key in verifier.CONTROL_NAMES)
    assert "PRIVATE" not in json.dumps(summary)
    assert "SECRET" not in json.dumps(summary)


def test_immutable_receipt_never_overwrites_and_writes_valid_hash(tmp_path):
    target = tmp_path / "receipt.json"
    checksum = verifier.persist(target, {"attempt": 1})
    assert checksum == verifier.digest(target.read_bytes())
    assert target.stat().st_mode & 0o222 == 0
    with pytest.raises(FileExistsError):
        verifier.persist(target, {"attempt": 2})
    assert json.loads(target.read_text()) == {"attempt": 1}


def test_unique_label_and_durable_storage_are_enforced(durable_path):
    first = verifier.evidence_directory(durable_path, "new-label")
    assert first.is_dir()
    with pytest.raises(FileExistsError):
        verifier.evidence_directory(durable_path, "new-label")
    with pytest.raises(ValueError):
        verifier.evidence_directory(durable_path, "../escape")
    with pytest.raises(ValueError, match="durable"):
        verifier.evidence_directory(Path("/private/tmp"), "discardable")


def test_source_proof_requires_explicit_packet_pin_and_exact_source_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    static = source / "controls.rs"
    static.write_text("// official pinned source excerpt fixture\n// controls\n")
    controls = verifier.required_source_controls()
    proof = tmp_path / "proof.json"
    proof.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": verifier.PROFILE,
                "codex_source_commit": verifier.SOURCE_COMMIT,
                "source_supported_controls": list(controls),
                "source_files": [{"path": "controls.rs", "sha256": verifier.digest(static.read_bytes())}],
                "control_sources": {
                    name: [{"path": "controls.rs", "start_line": 1, "end_line": 2}] for name in controls
                },
            }
        )
    )
    checksum = verifier.digest(proof.read_bytes())
    assert verifier.load_source_proof(proof, source, checksum)["verified"] is True
    assert verifier.load_source_proof(proof, source, None)["verified"] is False
    assert verifier.load_source_proof(proof, source, "0" * 64)["verified"] is False
    static.write_text("changed source\n")
    assert verifier.load_source_proof(proof, source, checksum)["verified"] is False


def test_root_reviewed_assertion_without_source_controls_is_insufficient(tmp_path):
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps({"review_status": "root_reviewed", "verified": True}))
    assert verifier.load_source_proof(proof, tmp_path, verifier.digest(proof.read_bytes()))["verified"] is False


@pytest.mark.asyncio
async def test_controls_only_never_dispatches_generation(patched_preflight, monkeypatch, durable_path):
    live = AsyncMock()
    monkeypatch.setattr(verifier, "live_canary", live)
    record = await verifier.verify(args(durable_path))
    live.assert_not_awaited()
    assert record["configuration_observations_passed"] is True
    assert record["preconditions_passed"] is False
    assert record["live_requested"] is record["live_submitted"] is False
    assert record["admission"] == "policy_unverified"
    assert record["generation_canary_passed"] is False
    assert record["submissions_consumed_total"] == 0
    assert record["live_cancellation_verified"] is False


def test_supplied_proof_and_empty_inventory_cannot_clear_trusted_runtime_blocker():
    record = passing_preflight()
    record["runtime_blockers"] = []
    record["runtime_ceiling_verified"] = True
    assert verifier.configuration_observations_passed(record) is True
    assert verifier.preconditions(record) is False
    assert generation_policy.RUNTIME_BLOCKERS == ("ordinary_mcp_runtime_ceiling_unavailable",)


@pytest.mark.asyncio
async def test_live_request_with_all_observations_passing_records_refusal_before_reservation(
    patched_preflight, monkeypatch, durable_path
):
    live = AsyncMock()
    monkeypatch.setattr(verifier, "live_canary", live)
    record = await verifier.verify(args(durable_path, live=True))
    live.assert_not_awaited()
    assert record["configuration_observations_passed"] is True
    assert record["preconditions_passed"] is False
    assert record["live_requested"] is True and record["live_submitted"] is False
    assert record["live_blocker"] == "ordinary_mcp_runtime_ceiling_unavailable"
    assert record["runtime_blockers"] == list(generation_policy.RUNTIME_BLOCKERS)
    assert record["submissions_in_this_invocation"] == record["submissions_consumed_total"] == 0
    assert record["maximum_submissions_per_invocation"] == 0
    assert record["submission_ledger"] == []
    assert not (durable_path / "evidence/test-first/submitted.json").exists()
    assert not (durable_path / "evidence/test-first/live").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_passed", [False, True])
async def test_blocked_live_request_retains_prior_submissions_without_replacement(
    patched_preflight, monkeypatch, durable_path, prior_passed
):
    ledger = [
        {
            "ordinal": 1,
            "check": "suppression_and_useful_transformation",
            "reservation_sha256": "0" * 64,
            "passed": prior_passed,
        }
    ]
    prior = durable_path / "prior.json"
    verifier.persist(
        prior,
        {
            "scope": verifier.SCOPE,
            "profile": verifier.PROFILE,
            "submission_ledger": ledger,
            "submissions_consumed_total": 1,
        },
    )
    live = AsyncMock()
    monkeypatch.setattr(verifier, "live_canary", live)
    record = await verifier.verify(args(durable_path, live=True, prior_evidence=prior))
    live.assert_not_awaited()
    assert record["submission_ledger"] == ledger
    assert record["submissions_consumed_total"] == 1
    assert record["submissions_in_this_invocation"] == 0
    assert record["live_requested"] is True and record["live_submitted"] is False
    assert not (durable_path / "evidence/test-first/submitted.json").exists()


@pytest.mark.asyncio
async def test_direct_constructor_candidate_cannot_bypass_production_ceiling(tmp_path):
    from clink.parsers.codex_generation import parser_contract_digest

    executable = tmp_path / "must-not-launch"
    executable.write_text("#!" + sys.executable + "\nraise RuntimeError('must not execute')\n")
    executable.chmod(0o700)
    binding = {
        "canary_fixture_sha256": verifier.digest(verifier.CANARY.read_bytes()),
        "executable_sha256": verifier.executable_digest(str(executable)),
        "recipe_sha256": generation_policy.generation_recipe_digest(),
        "catalog_sha256": generation_policy.generation_catalog_digest(),
        "parser_sha256": parser_contract_digest(),
    }
    preflight = tmp_path / "preflight.json"
    verifier.persist(preflight, {"scope": "synthetic_constructor_bypass_rejection", **binding})
    root = tmp_path / "candidate"
    root.mkdir()
    result = await verifier.live_canary(str(executable), root, binding, preflight, "gpt-6.1-sol", 30)
    assert result["passed"] is False
    assert result["error_code"] == "policy_unverified"
    assert result["terminal_status"] == "failed"
    assert result["dispatch_count"] == result["spawn_count"] == 0
    assert result["workspace_unchanged"] is True


@pytest.mark.asyncio
async def test_unverified_extension_blocks_even_explicit_live(patched_preflight, monkeypatch, durable_path):
    preflight = passing_preflight()["runtime_preflight"]
    preflight["controls"]["disabled_features"]["executor_capability_discovery_v2"] = False
    monkeypatch.setattr(verifier, "control_probe", lambda *_: preflight)
    live = AsyncMock()
    monkeypatch.setattr(verifier, "live_canary", live)
    record = await verifier.verify(args(durable_path, live=True))
    live.assert_not_awaited()
    assert record["live_request"] == "blocked_before_inference"
    assert record["submissions_consumed_total"] == 0


@pytest.mark.asyncio
async def test_submission_is_immutable_and_consumed_before_dispatch_even_on_failure(
    patched_preflight, monkeypatch, durable_path, simulated_runtime_ceiling
):
    async def failing_canary(_executable, _root, binding, preflight, _model, _timeout):
        submitted = verifier.read_json(preflight.parent / "submitted.json")
        assert submitted["state"] == "consumed_before_dispatch"
        assert submitted["preflight_sha256"] == verifier.digest(preflight.read_bytes())
        assert binding["submissions_consumed_total"] == 1
        raise RuntimeError("PRIVATE details must not be recorded")

    mock = AsyncMock(side_effect=failing_canary)
    monkeypatch.setattr(verifier, "live_canary", mock)
    record = await verifier.verify(args(durable_path, live=True))
    assert mock.await_count == 1
    assert record["submissions_consumed_total"] == record["submissions_in_this_invocation"] == 1
    assert record["submission_ledger"][0]["passed"] is False
    assert record["automatic_retries"] == 0
    assert record["generation_canary_passed"] is False
    assert "PRIVATE" not in json.dumps(record)
    assert (durable_path / "evidence/test-first/receipt.json").is_file()


@pytest.mark.asyncio
async def test_previous_failed_attempt_requires_explicit_replacement_and_preserves_count(
    patched_preflight, monkeypatch, durable_path, simulated_runtime_ceiling
):
    monkeypatch.setattr(verifier, "live_canary", AsyncMock(side_effect=RuntimeError()))
    first = await verifier.verify(args(durable_path, live=True))
    prior = durable_path / "evidence/test-first/receipt.json"
    second_args = args(durable_path, live=True, label="test-second", prior_evidence=prior)
    with pytest.raises(ValueError, match="consumed attempt"):
        await verifier.verify(second_args)
    second_args.replace_failed_attempt = True
    second = await verifier.verify(second_args)
    assert second["submissions_consumed_total"] == 2
    assert second["submission_ledger"][0] == first["submission_ledger"][0]
    assert second["prior_evidence_sha256"] == verifier.digest(prior.read_bytes())


def test_interrupted_submission_reservation_cannot_reset_count(tmp_path):
    preflight = tmp_path / "preflight.json"
    verifier.persist(
        preflight,
        {
            "scope": verifier.SCOPE,
            "profile": verifier.PROFILE,
            "submission_ledger": [],
            "submissions_consumed_total": 0,
        },
    )
    reservation = tmp_path / "submitted.json"
    verifier.persist(
        reservation,
        {
            "scope": verifier.SCOPE,
            "profile": verifier.PROFILE,
            "state": "consumed_before_dispatch",
            "ordinal": 1,
            "preflight_sha256": verifier.digest(preflight.read_bytes()),
        },
    )
    ledger, _ = verifier.load_history(preflight)
    assert len(ledger) == 1
    assert ledger[0]["passed"] is False
    assert ledger[0]["reservation_sha256"] == verifier.digest(reservation.read_bytes())


@pytest.mark.asyncio
async def test_successful_terminal_cannot_be_recovered_as_failed_preflight(
    patched_preflight, monkeypatch, durable_path, simulated_runtime_ceiling
):
    monkeypatch.setattr(verifier, "live_canary", AsyncMock(return_value={"passed": True, "actual_model": None}))
    first = await verifier.verify(args(durable_path, live=True))
    assert first["submission_ledger"][0]["passed"] is True
    directory = durable_path / "evidence/test-first"
    with pytest.raises(ValueError, match="terminal receipt"):
        verifier.load_history(directory / "preflight.json")
    replacement = args(
        durable_path,
        live=True,
        label="second",
        prior_evidence=directory / "preflight.json",
        replace_failed_attempt=True,
    )
    with pytest.raises(ValueError, match="terminal receipt"):
        await verifier.verify(replacement)
    replacement.prior_evidence = directory / "receipt.json"
    with pytest.raises(ValueError, match="prior failed attempt"):
        await verifier.verify(replacement)
    ledger, _ = verifier.load_history(directory / "receipt.json")
    assert len(ledger) == 1 and ledger[0]["passed"] is True
    assert not (durable_path / "evidence/second").exists()


def test_consultation_evidence_cannot_supply_generation_history(tmp_path):
    prior = tmp_path / "consultation.json"
    prior.write_text(json.dumps({"adapter": "codex", "admission": {"status": "verified"}}))
    with pytest.raises(ValueError, match="separate generation"):
        verifier.load_history(prior)


def test_useful_transformation_and_private_read_canary_checks():
    text = (
        "Create an engineering migration plan for the Atlas cache, owned by Mira, for Friday. "
        "Include two concrete risks, a rollback procedure, and measurable acceptance checks for each stage."
    )
    assert all(verifier.useful_transformation(text).values())
    assert verifier.useful_transformation("READY")["nonempty_bounded_text"] is False
    assert (
        verifier.useful_transformation(text + " CLINK_GENERATION_FILE_SECRET_732819")["file_canary_not_disclosed"]
        is False
    )
    assert verifier.useful_transformation(text.replace("two", "three"))["two_risks_preserved"] is False
    assert verifier.useful_transformation(text + " use apply_patch")["embedded_tool_requests_omitted"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "tool", "unknown", "model_mismatch"])
async def test_real_service_parser_canary_preserves_identity_and_rejects_tools_without_models(
    tmp_path, mode, simulated_runtime_ceiling
):
    from clink.parsers.codex_generation import parser_contract_digest

    text = (
        "Create an engineering migration plan for the Atlas cache, owned by Mira, for Friday. "
        "Include two concrete risks, a rollback procedure, and measurable acceptance checks for each stage."
    )
    events = [
        {"type": "thread.started", "thread_id": "synthetic"},
        {"type": "turn.started"},
    ]
    if mode == "tool":
        events.append({"type": "item.completed", "item": {"type": "command_execution", "command": "synthetic"}})
    if mode == "unknown":
        events.append({"type": "unsupported.synthetic"})
    events.extend(
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},
        ]
    )
    if mode == "model_mismatch":
        events[-1]["model"] = "gpt-6-luna"
    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!"
        + sys.executable
        + "\nimport sys\nsys.stdin.buffer.read()\n"
        + "sys.stdout.write("
        + repr("\n".join(json.dumps(event) for event in events) + "\n")
        + ")\n"
    )
    executable.chmod(0o700)
    binding = {
        "canary_fixture_sha256": verifier.digest(verifier.CANARY.read_bytes()),
        "executable_sha256": verifier.executable_digest(str(executable)),
        "recipe_sha256": generation_policy.generation_recipe_digest(),
        "catalog_sha256": generation_policy.generation_catalog_digest(),
        "parser_sha256": parser_contract_digest(),
    }
    preflight = tmp_path / "preflight.json"
    verifier.persist(preflight, {"scope": "synthetic_no_model", **binding})
    root = tmp_path / "live"
    root.mkdir()
    result = await verifier.live_canary(str(executable), root, binding, preflight, "gpt-6.1-sol", 30)
    assert result["passed"] is (mode == "success")
    assert result["dispatch_count"] == result["spawn_count"] == 1
    assert result["workspace_unchanged"] is True
    assert result["pool_running_after_shutdown"] == result["pool_children_after_shutdown"] == 0
    assert result["raw_output_retained"] is False
    assert text not in json.dumps(result)
    if mode == "success":
        assert result["actual_model"] is None
        assert result["output_sha256"] == verifier.digest(text.encode())
    elif mode == "tool":
        assert result["tool_event_count"] == 1
        assert result["error_code"] == "unexpected_tool_event"
    elif mode == "unknown":
        assert result["unknown_event_count"] == 1
        assert result["error_code"] == "protocol_error"
    else:
        assert result["actual_model"] == "gpt-6-luna"
        assert result["error_code"] == "unsupported_model"


@pytest.mark.asyncio
async def test_synthetic_cancellation_uses_only_shared_pool_and_reaps_descendant(tmp_path):
    result = await verifier.synthetic_cancellation_probe(tmp_path)
    assert result["passed"] is True
    assert result["production_service_used"] is False
    assert result["production_pool_used"] is True
    assert result["model_requests_submitted"] == 0
    assert result["descendant_absent_after_cleanup"] is True
    assert result["pool_running_after_shutdown"] == result["pool_children_after_shutdown"] == 0
    assert result["live_remote_cancellation_verified"] is False


@pytest.mark.asyncio
async def test_observed_pool_prevents_a_second_dispatch_without_spawning():
    pool = verifier.ObservedPool()
    pool.dispatches = 1
    with pytest.raises(RuntimeError, match="more than once"):
        await pool.execute(("never-executed",))
    await pool.shutdown()


def test_help_is_read_only_and_default_has_no_live_opt_in():
    parsed = verifier.argument_parser().parse_args(["--label", "controls-only"])
    assert parsed.live is False
    assert parsed.replace_failed_attempt is False
    assert parsed.source_proof_sha256 is None
    assert "Currently unavailable" in verifier.argument_parser().format_help()
