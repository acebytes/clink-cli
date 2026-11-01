"""Verify scoped Codex trust orchestration with in-memory control sessions only."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from clink.contracts import AdapterPolicy
from scripts import verify_native_adapters as verifier


@pytest.fixture
def controls(tmp_path, monkeypatch):
    events = []
    native_records = {}
    options = {
        "model": "gpt-6-astra",
        "effort": "ultra",
        "enroll_error": None,
        "cleanup_error": None,
        "cleanup_complete": True,
        "preparation_reaped": True,
        "managed_before": True,
        "managed_after": True,
        "write_confirmed": True,
        "unconfirmed_write_count": 0,
        "preparation_cleanup_failure": None,
        "cleanup_control_failure": None,
    }
    instances = []

    class Rpc:
        def __init__(self, executable, root, *, allow_writes):
            assert allow_writes is True
            self.number = len(instances) + 1
            self.cleanup_complete = False
            self.cleanup_failure = None
            instances.append(self)

        def __enter__(self):
            events.append(("open", self.number))
            return self

        def __exit__(self, *args):
            events.append(("close", self.number))
            self.cleanup_complete = options["preparation_reaped"] if self.number == 1 else True
            self.cleanup_failure = (
                options["preparation_cleanup_failure"] if self.number == 1 else options["cleanup_control_failure"]
            )

    class Scope:
        def __init__(self, rpc, binding):
            self.rpc = rpc
            self.owned = {}
            self.receipt = {
                "native_write_attempted": False,
                "native_write_confirmed": False,
                "runtime_permissions_resolved": False,
                "managed_requirements_observed": options["managed_before"],
                "managed_requirements_present": False,
                "managed_constraints": None,
            }

        def settings(self):
            return {
                "model": options["model"],
                "model_reasoning_effort": options["effort"],
                "approval_policy": None,
                "approvals_reviewer": None,
                "sandbox_mode": None,
            }

        def inventory(self):
            events.append(("inventory", self.rpc.number))
            self.receipt["configured_before"] = self.settings()
            return deepcopy(self.receipt)

        def enroll(self):
            events.append(("enroll", self.rpc.number))
            native_records[("synthetic",)] = {"trust_level": "trusted"}
            self.owned = {("synthetic",): {"trust_level": "trusted"}} if options["write_confirmed"] else {}
            self.receipt.update(native_write_attempted=True, native_write_confirmed=options["write_confirmed"])
            if options["unconfirmed_write_count"]:
                self.receipt["unconfirmed_write_record_count"] = options["unconfirmed_write_count"]
            if options["enroll_error"]:
                raise verifier.TrustError(options["enroll_error"])
            self.receipt["configured_after"] = self.settings()
            self.receipt["managed_requirements_observed"] = options["managed_after"]
            return deepcopy(self.receipt)

        def cleanup(self):
            events.append(("cleanup", self.rpc.number))
            if options["cleanup_error"]:
                raise verifier.TrustError(options["cleanup_error"])
            self.receipt["cleanup_complete"] = options["cleanup_complete"] and not options["unconfirmed_write_count"]
            if options["cleanup_complete"]:
                for key in self.owned:
                    native_records.pop(key, None)
                self.owned.clear()
            return deepcopy(self.receipt)

    monkeypatch.setattr(verifier, "NativeRpc", Rpc)
    monkeypatch.setattr(verifier, "TrustScope", Scope)
    evidence = {"gates": [], "submission_ledger": [], "configuration_preparation_gates": sorted(verifier.CONFIG_GATES)}
    path = tmp_path / "receipt.json"
    return SimpleNamespace(
        events=events, options=options, instances=instances, evidence=evidence, path=path, native_records=native_records
    )


async def run(controls, tmp_path, *, model="gpt-6.1-sol"):
    await verifier.run_codex_trusted_checks(
        "/synthetic/codex",
        tmp_path,
        AdapterPolicy("codex"),
        verifier.CHECKS,
        controls.evidence,
        controls.path,
        5,
        SimpleNamespace(root=tmp_path),
        model,
    )


@pytest.mark.asyncio
async def test_request_selection_supersedes_configured_model_and_cleanup_uses_fresh_session(
    controls, tmp_path, monkeypatch
):
    async def lifecycle(*args, **kwargs):
        assert controls.events[-1] == ("close", 1)
        assert kwargs == {"model": "gpt-6.1-sol"}
        assert controls.evidence["configuration_preparation_gates"] == []
        controls.events.append(("lifecycle", "gpt-6.1-sol"))

    monkeypatch.setattr(verifier, "run_checks", lifecycle)
    await run(controls, tmp_path)
    assert controls.events == [
        ("open", 1),
        ("inventory", 1),
        ("enroll", 1),
        ("close", 1),
        ("lifecycle", "gpt-6.1-sol"),
        ("open", 2),
        ("cleanup", 2),
        ("close", 2),
    ]
    evidence = controls.evidence
    assert evidence["gates"] == []
    assert evidence["codex_owned_trust_entries_removed"] is True
    assert evidence["codex_trust_control_groups_reaped"] is True
    assert evidence["host_configuration_changed"] is True
    assert controls.native_records == {}
    selected = evidence["codex_configured_selection"]["before"]
    assert selected["configured_model"] == "gpt-6-astra"
    assert selected["requested_model"] == selected["selected_model"] == "gpt-6.1-sol"
    assert selected["configured_reasoning_effort"] == "ultra"
    assert evidence["codex_fixture_trust"]["configured_after"]["sandbox_mode"] is None
    assert evidence["codex_fixture_trust"]["runtime_permissions_resolved"] is False
    assert "actual_model" not in selected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,effort,requested_model,gate",
    [
        ("gpt-6-astra", "ultra", None, "codex_configured_model_mismatch"),
        (None, "ultra", None, "codex_configured_model_unverified"),
        ("gpt-6-astra", None, "gpt-6.1-sol", "codex_configured_reasoning_effort_unverified"),
        ("gpt-6-astra", "high", "gpt-6.1-sol", "codex_configured_reasoning_effort_mismatch"),
    ],
)
async def test_unresolved_or_mismatched_selection_blocks_writes_and_model_work(
    controls, tmp_path, monkeypatch, model, effort, requested_model, gate
):
    controls.options.update(model=model, effort=effort)
    monkeypatch.setattr(verifier, "run_checks", lambda *args, **kwargs: pytest.fail("Selection gate submitted work"))
    await run(controls, tmp_path, model=requested_model)
    assert ("enroll", 1) not in controls.events
    assert len(controls.instances) == 1
    assert controls.native_records == {}
    assert gate in controls.evidence["gates"]
    assert controls.evidence["stopped_after"] == "codex_configured_selection_gate"
    assert controls.evidence["host_configuration_changed"] is False
    assert controls.evidence["configuration_preparation_gates"] == sorted(verifier.CONFIG_GATES)


@pytest.mark.asyncio
async def test_cleanup_runs_after_lifecycle_failure_without_persisting_exception_text(controls, tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_MODEL_OR_CONFIG_TEXT")

    monkeypatch.setattr(verifier, "run_checks", fail)
    with pytest.raises(RuntimeError):
        await run(controls, tmp_path)
    assert ("cleanup", 2) in controls.events
    assert controls.evidence["codex_owned_trust_entries_removed"] is True
    assert "PRIVATE_MODEL_OR_CONFIG_TEXT" not in controls.path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before", "after"])
async def test_missing_managed_requirements_observation_blocks_model_work_and_cleans_if_needed(
    controls, tmp_path, monkeypatch, phase
):
    controls.options["managed_" + phase] = False
    monkeypatch.setattr(
        verifier, "run_checks", lambda *args, **kwargs: pytest.fail("Missing managed inventory submitted work")
    )
    await run(controls, tmp_path)
    assert "codex_managed_requirements_unverified" in controls.evidence["gates"]
    assert controls.evidence["stopped_after"] == "codex_managed_requirements_gate"
    assert controls.evidence["codex_owned_trust_entries_removed"] is True
    assert ("enroll", 1) in controls.events if phase == "after" else ("enroll", 1) not in controls.events


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["trust_entry_preexisting", "PRIVATE_NATIVE_CONFIG_TEXT"])
async def test_partial_enrollment_failure_cleans_owned_records_and_filters_error_code(
    controls, tmp_path, monkeypatch, code
):
    controls.options["enroll_error"] = code
    monkeypatch.setattr(verifier, "run_checks", lambda *args, **kwargs: pytest.fail("Failed enrollment submitted work"))
    await run(controls, tmp_path)
    assert ("cleanup", 2) in controls.events
    assert controls.evidence["codex_trust_error_code"] == (
        code if code in verifier.CODEX_TRUST_ERROR_CODES else "unknown_trust_error"
    )
    assert "PRIVATE_NATIVE_CONFIG_TEXT" not in controls.path.read_text()


@pytest.mark.asyncio
async def test_applied_first_write_with_lost_ack_reports_host_change_as_unknown(controls, tmp_path, monkeypatch):
    controls.options.update(
        write_confirmed=False,
        unconfirmed_write_count=1,
        enroll_error="rpc_native_error",
    )
    monkeypatch.setattr(
        verifier, "run_checks", lambda *args, **kwargs: pytest.fail("Lost acknowledgment submitted model work")
    )
    await run(controls, tmp_path)
    evidence = controls.evidence
    assert evidence["host_configuration_changed"] is None
    assert evidence["host_configuration_change_unconfirmed"] is True
    assert evidence["codex_fixture_trust"]["unconfirmed_write_record_count"] == 1
    assert evidence["codex_owned_trust_entries_removed"] is False
    assert "codex_fixture_trust_cleanup_failed" in evidence["gates"]
    assert len(controls.instances) == 1
    assert controls.native_records == {("synthetic",): {"trust_level": "trusted"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["conflict", "exception", "preparation_group"])
async def test_unverified_cleanup_is_never_reported_as_complete(controls, tmp_path, monkeypatch, mode):
    async def lifecycle(*args, **kwargs):
        controls.events.append(("lifecycle", "called"))

    monkeypatch.setattr(verifier, "run_checks", lifecycle)
    if mode == "conflict":
        controls.options["cleanup_complete"] = False
    elif mode == "exception":
        controls.options["cleanup_error"] = "PRIVATE_CLEANUP_TEXT"
    else:
        controls.options["preparation_reaped"] = False
    await run(controls, tmp_path)
    assert controls.evidence["gates"]
    if mode == "preparation_group":
        assert ("lifecycle", "called") not in controls.events
        assert controls.evidence["codex_trust_control_groups_reaped"] is False
    else:
        assert controls.evidence.get("codex_owned_trust_entries_removed") is False
    assert "PRIVATE_CLEANUP_TEXT" not in controls.path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,code",
    [
        ("preparation", "rpc_stream_close_failed"),
        ("cleanup", "rpc_stream_close_failed"),
        ("preparation", "PRIVATE_CONTROL_TEXT"),
    ],
)
async def test_reaped_control_with_cleanup_failure_is_recorded_and_gated(controls, tmp_path, monkeypatch, phase, code):
    called = []

    async def lifecycle(*args, **kwargs):
        called.append(True)

    monkeypatch.setattr(verifier, "run_checks", lifecycle)
    controls.options["preparation_cleanup_failure" if phase == "preparation" else "cleanup_control_failure"] = code
    await run(controls, tmp_path)
    evidence = controls.evidence
    assert evidence["codex_trust_control_groups_reaped"] is True
    assert "codex_trust_control_cleanup_failed" in evidence["gates"]
    assert evidence["codex_" + phase + "_control_cleanup_failure"] == (
        code if code in verifier.CODEX_CONTROL_CLEANUP_ERRORS else "unknown_control_cleanup_error"
    )
    assert bool(called) is (phase == "cleanup")
    assert evidence["configuration_preparation_gates"] == ([] if phase == "cleanup" else sorted(verifier.CONFIG_GATES))
    assert evidence["codex_owned_trust_entries_removed"] is True
    assert "PRIVATE_CONTROL_TEXT" not in controls.path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["wrong_adapter", "missing_root", "missing_prior", "prepare_only", "wrong_root", "alias_root"]
)
async def test_trust_optin_requires_the_exact_existing_packet_before_discovery(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    approved = tmp_path / "approved"
    approved.mkdir()
    selected = []

    def binding(requested_root):
        selected.append(requested_root)
        if requested_root != approved:
            raise verifier.TrustError("fixture_root_not_registered")
        return SimpleNamespace(root=approved)

    monkeypatch.setattr(verifier, "approved_binding", binding)
    monkeypatch.setattr(verifier.shutil, "which", lambda *args: pytest.fail("Invalid trust options reached discovery"))
    args = verifier.argument_parser().parse_args(
        [
            "--live",
            "--adapter",
            "codex",
            "--trust-codex-fixture",
            "--evidence",
            str(tmp_path / "docs/verification/next.json"),
            "--fixture-root",
            str(approved),
            "--prior-evidence",
            str(tmp_path / "prior.json"),
        ]
    )
    if mode == "wrong_adapter":
        args.adapter = "agy"
    elif mode == "missing_root":
        args.fixture_root = None
    elif mode == "missing_prior":
        args.prior_evidence = None
    elif mode == "prepare_only":
        args.prepare_only = True
    elif mode == "wrong_root":
        args.fixture_root = tmp_path / "other"
        args.fixture_root.mkdir()
    else:
        args.fixture_root = tmp_path / "alias"
        args.fixture_root.symlink_to(approved, target_is_directory=True)
    with pytest.raises(ValueError, match="Codex trust"):
        await verifier.verify(args)
    assert selected == ([args.fixture_root] if mode in {"wrong_root", "alias_root"} else [])


@pytest.mark.asyncio
async def test_changed_approved_binding_keeps_its_technical_failure_before_discovery(tmp_path, monkeypatch):
    def changed_binding(requested_root):
        raise verifier.TrustError("fixture_file_changed")

    monkeypatch.setattr(verifier, "approved_binding", changed_binding)
    monkeypatch.setattr(verifier.shutil, "which", lambda *args: pytest.fail("Changed binding reached discovery"))
    args = verifier.argument_parser().parse_args(
        [
            "--live",
            "--adapter",
            "codex",
            "--trust-codex-fixture",
            "--fixture-root",
            str(tmp_path / "approved"),
            "--prior-evidence",
            str(tmp_path / "prior.json"),
            "--evidence",
            str(tmp_path / "docs/verification/next.json"),
        ]
    )
    with pytest.raises(verifier.TrustError, match="^fixture_file_changed$"):
        await verifier.verify(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter,model", [("grok", "gpt-6.1-sol"), ("codex", "other-model")])
async def test_model_selector_is_narrow_and_never_substitutes(tmp_path, monkeypatch, adapter, model):
    monkeypatch.setattr(verifier.shutil, "which", lambda *args: pytest.fail("Invalid selection reached discovery"))
    args = verifier.argument_parser().parse_args(
        [
            "--live",
            "--adapter",
            adapter,
            "--model",
            model,
            "--evidence",
            str(tmp_path / "docs/verification/next.json"),
        ]
    )
    with pytest.raises(ValueError, match="explicitly selected Codex execution model"):
        await verifier.verify(args)


@pytest.mark.asyncio
async def test_trust_entrypoint_reuses_pinned_fixture_and_preserves_preflight_without_regeneration(
    controls, tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    parent = tmp_path / "test_output/native-adapters"
    parent.mkdir(parents=True)
    root = parent / "approved.fixture"
    files = verifier.create_fixture(root, "codex")
    directory = tmp_path / "docs/verification"
    directory.mkdir(parents=True)
    prior = directory / "prepared.json"
    prior.write_text(
        json.dumps(
            {
                "scope": verifier.SCOPE,
                "adapter": "codex",
                "submission_ledger": [],
                "submission_allowance_total": 4,
                "submissions_consumed_total": 0,
                "fixture_config_files": files,
                "fixture_config_sha256": verifier.fixture_digest(root, files),
                "project_root_sha256": verifier.digest(str(root)),
            }
        )
    )
    before = prior.read_bytes()
    fixture_before = {name: (root / name).read_bytes() for name in files}
    executable = tmp_path / "synthetic-codex"
    executable.write_text("Not executed; native control and model work are replaced in this test.")
    monkeypatch.setattr(verifier.shutil, "which", lambda name: str(executable))
    selected = []

    def binding(requested_root):
        selected.append(requested_root)
        return SimpleNamespace(root=root)

    monkeypatch.setattr(verifier, "approved_binding", binding)
    monkeypatch.setattr(verifier, "create_fixture", lambda *args: pytest.fail("Existing fixture was regenerated"))
    monkeypatch.setattr(
        verifier,
        "preflight",
        lambda *args: {
            "adapter": "codex",
            "observed_version": "codex-cli 0.166.0",
            "executable_sha256": "b" * 64,
            "native_recipe_sha256": verifier.native_recipe_digest("codex"),
        },
    )

    async def lifecycle(executable, adapter, project, candidate, checks, evidence, path, timeout, *, model):
        assert project == root and adapter == "codex" and model == "gpt-6.1-sol"
        evidence["submission_ledger"] = [
            {"ordinal": number, "check": check, "passed": True, "process_group_absent_after_cleanup": True}
            for number, check in enumerate(checks, 1)
        ]
        evidence["submissions_consumed_total"] = evidence["submissions_in_this_invocation"] = 4

    monkeypatch.setattr(verifier, "run_checks", lifecycle)
    args = verifier.argument_parser().parse_args(
        [
            "--live",
            "--adapter",
            "codex",
            "--model",
            "gpt-6.1-sol",
            "--trust-codex-fixture",
            "--fixture-root",
            str(root),
            "--prior-evidence",
            str(prior),
            "--evidence",
            str(directory / "next.json"),
        ]
    )
    evidence = await verifier.verify(args)
    assert selected == [root]
    assert evidence["native_verified"] is True
    assert evidence["submission_allowance_total"] == evidence["submissions_consumed_total"] == 4
    assert evidence["prior_evidence_sha256"] == verifier.digest(before)
    assert prior.read_bytes() == before
    assert fixture_before == {name: (root / name).read_bytes() for name in files}
    assert controls.events[-3:] == [("open", 2), ("cleanup", 2), ("close", 2)]
