"""Claude generation verifier accounting and lifecycle without real model calls."""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clink.generation_contracts import canonical_json, sha256
from clink.native_generation_claude_policy import claude_generation_recipe_digest
from clink.native_generation_policy import NativeGenerationAdmission
from clink.parsers.claude_native_generation import parser_contract_digest
from clink.policy import executable_digest
from scripts import verify_claude_generation as verifier


@pytest.fixture
def disabled_production(monkeypatch):
    admission = NativeGenerationAdmission()
    monkeypatch.setattr(verifier, "DEFAULT_CLAUDE_NATIVE_ADMISSION", admission)
    return admission


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(verifier, "DEFAULT_CLAUDE_NATIVE_ADMISSION", NativeGenerationAdmission())
    parent = verifier.PROJECT / "test_output/claude-generation-verifier-tests"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        root = Path(directory)
        monkeypatch.setattr(verifier, "PROJECT", root)
        monkeypatch.setattr(verifier, "LINEAGE_ROOT", root / "docs/verification/claude-generation")
        monkeypatch.setattr(verifier, "_assert_execution_provenance", lambda: None)
        yield root


def arguments(**changes):
    values = {
        "label": "first",
        "check": "text",
        "live": True,
        "timeout": 30,
        "model": verifier.DEFAULT_MODEL,
        "output_dir": verifier.LINEAGE_ROOT,
        "replacement_review": None,
        "replacement_review_sha256": None,
        "exception_review": None,
        "exception_review_sha256": None,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def binding():
    result = {
        "executable_sha256": "a" * 64,
        "recipe_sha256": "b" * 64,
        "parser_sha256": "c" * 64,
        "model": verifier.DEFAULT_MODEL,
        "implementation_source_sha256": "d" * 64,
        "verifier_sha256": "e" * 64,
        "observed_version": verifier.VERSION,
        "package_source_sha256": "f" * 64,
        "python_executable_sha256": "1" * 64,
        "python_executable": str(Path(sys.executable).resolve(strict=True)),
        "python_version": sys.version.split()[0],
        "clink_package_directory": str(verifier.PACKAGE_ROOT / "clink"),
        "harness_project_directory": str(verifier.SOURCE_PROJECT),
    }
    result["candidate_sha256"] = sha256(canonical_json({key: result[key] for key in verifier.CANDIDATE_FIELDS}))
    return result


@pytest.fixture
def static(isolated, monkeypatch):
    monkeypatch.setattr(verifier, "preflight", lambda model: ("synthetic-claude", binding()))
    monkeypatch.setattr(verifier, "hook_policy", lambda _: {"known_local_hook_policy_available": True})
    monkeypatch.setattr(verifier, "_runtime_identity", binding)
    return isolated


def entry(ordinal, check="text", *, passed=False, replacement=False):
    return {
        "ordinal": ordinal,
        "check": check,
        "passed": passed,
        "replacement": replacement,
        "reservation_sha256": "a" * 64,
    }


@pytest.mark.parametrize(
    "entries",
    [
        [entry(1), entry(2)],
        [entry(1, passed=True), entry(2, replacement=True)],
        [entry(1), entry(2, replacement=True), entry(3, replacement=True)],
        [entry(1), entry(2, replacement=True), entry(3, "read"), entry(4, "read", replacement=True)],
        [entry(1, "unknown")],
        [entry(True)],
        [entry(1), entry(2, "read"), entry(3, "cancel"), entry(4, "cancel", replacement=True), entry(5, "read")],
    ],
)
def test_budget_refuses_duplicate_success_unreviewed_or_excess_attempts(entries):
    with pytest.raises(ValueError):
        verifier.validate_ledger(entries)


@pytest.mark.asyncio
async def test_failure_is_reserved_before_dispatch_and_cannot_reset_under_a_new_label(static, monkeypatch):
    async def fail(check, _executable, _workspace, _binding, preflight, _timeout):
        submitted = json.loads((preflight.parent / "submitted.json").read_bytes())
        assert submitted["state"] == "consumed_before_dispatch"
        assert submitted["preflight_sha256"] == sha256(preflight.read_bytes())
        prepared = json.loads(preflight.read_bytes())
        assert prepared["fixture_controls"]["selftest_passed"] is True
        assert prepared["fixture_controls"]["permission_grants"] is False
        assert len(prepared["fixture_controls"]["files"]) == 3
        return {"check": check, "passed": False}

    run = AsyncMock(side_effect=fail)
    monkeypatch.setattr(verifier, "run_check", run)
    first = await verifier.verify(arguments())
    assert first["live_submitted"] is True and first["submissions_consumed_total"] == 1
    original = (verifier.LINEAGE_ROOT / "first/receipt.json").read_bytes()
    second = await verifier.verify(arguments(label="reset-attempt"))
    assert second["live_submitted"] is False and second["submissions_consumed_total"] == 1
    assert second["error_class"] == "ValueError" and run.await_count == 1
    assert (verifier.LINEAGE_ROOT / "first/receipt.json").read_bytes() == original
    assert verifier.authoritative_history()[0]["entry"]["passed"] is False
    assert verifier.DEFAULT_CLAUDE_NATIVE_ADMISSION.verified is False


@pytest.mark.asyncio
async def test_interrupted_submission_is_consumed_and_preserved(static, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await verifier.verify(arguments())
    receipt = json.loads((verifier.LINEAGE_ROOT / "first/receipt.json").read_bytes())
    assert receipt["error_class"] == "CancelledError" and receipt["live_submitted"] is True
    assert len(verifier.authoritative_history()) == 1
    (verifier.LINEAGE_ROOT / "first/receipt.json").unlink()
    assert verifier.authoritative_history()[0]["entry"]["passed"] is False
    refused = await verifier.verify(arguments(label="after-crash"))
    assert refused["live_submitted"] is False and refused["submissions_consumed_total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["relabel", "candidate", "forged_pass", "count", "copied_reservation", "symlink"])
async def test_history_binds_original_preflight_reservation_and_outcome(static, monkeypatch, case):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": False}))
    await verifier.verify(arguments())
    original = verifier.LINEAGE_ROOT / "first"
    path = original / "receipt.json"
    record = json.loads(path.read_bytes())
    if case == "relabel":
        record["check"] = "read"
    elif case == "candidate":
        record["candidate_sha256"] = "f" * 64
    elif case == "forged_pass":
        record["submission_ledger"][0]["passed"] = True
    elif case == "count":
        record["submissions_consumed_total"] = 0
    elif case == "copied_reservation":
        copy = verifier.LINEAGE_ROOT / "copy"
        copy.mkdir()
        for name in ("preflight.json", "submitted.json", "receipt.json"):
            (copy / name).write_bytes((original / name).read_bytes())
    elif case == "symlink":
        target = static / "copied.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    if case not in {"copied_reservation", "symlink"}:
        path.chmod(0o600)
        path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        verifier.authoritative_history()


def replacement_packet(root, history, *, review_changes=None, candidate_sha=None):
    failed = history[0]
    candidate = candidate_sha or binding()["candidate_sha256"]
    failure_sha = sha256(
        canonical_json(
            {
                "reservation_sha256": failed["entry"]["reservation_sha256"],
                "preflight_sha256": failed["preflight_sha256"],
                "terminal_sha256": failed["terminal_sha256"],
            }
        )
    )
    diagnosis = root / "diagnosis.json"
    diagnosis.write_text('{"technical_diagnosis":"synthetic fixture failure"}')
    reviewed = {
        "schema_version": 1,
        "scope": "claude_generation_replacement_input",
        "profile": verifier.PROFILE,
        "failed_ordinal": 1,
        "check": "text",
        "failed_evidence_sha256": failure_sha,
        "diagnosis_sha256": sha256(diagnosis.read_bytes()),
        "candidate_sha256": candidate,
    }
    reviewed_path = root / "reviewed.json"
    reviewed_path.write_bytes(canonical_json(reviewed))
    review = {
        **verifier.REVIEW_CORE,
        "session_id": "11111111-2222-3333-4444-555555555555",
        "timestamp": "2026-11-02T00:00:00Z",
        "review_reservation_id": "claude-replacement_1",
        "review": "Synthetic review fixture. SHIP.\n\n" + verifier.APPROVAL_MARKER + sha256(reviewed_path.read_bytes()),
    }
    review.update(review_changes or {})
    review_path = root / "test_output/bundled-generation/replacement-review/receipt.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(json.dumps(review))
    attempt = {
        "reservation_id": review["review_reservation_id"],
        "task": verifier.REVIEW_TASK,
        "scope": "impl:" + verifier.REVIEW_TASK,
        "backend": "codex",
        "kind": "impl",
        "counter_kind": "impl",
        "outcome": "verdict",
        "verdict": "SHIP",
        "model": "gpt-6.1-sol",
        "effort": "high",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "head_sha_observed": True,
        "round_consumed": True,
        "forced": False,
        "failure_class": None,
        "superseded_by": None,
        "timestamp": "2026-11-02T00:00:00.001Z",
        "finalized": {"receipt": "complete", "digest": "complete", "status": "not_applicable"},
    }
    spec = root / ".flow/specs/fn-3-bundle-claude-and-codex-clink.json"
    spec.parent.mkdir(parents=True)
    spec.write_text(json.dumps({"review_attempts": [attempt]}))
    transport = {
        "schemaVersion": 1,
        "reservationId": review["review_reservation_id"],
        "taskId": verifier.REVIEW_TASK,
        "baseSha": attempt["base_sha"],
        "headSha": attempt["head_sha"],
        "packetSha256": "c" * 64,
        "allowlistSha256": "d" * 64,
        "sessionId": review["session_id"],
        "requestedSpec": "codex:gpt-6.1-sol:high",
        "sandbox": "read-only",
        "resumeOnly": True,
        "runtimeVerification": "unverified",
    }
    transport_path = root / ".flow/tmp/scoped-review-transports/claude-replacement_1.json"
    transport_path.parent.mkdir(parents=True)
    transport_path.write_text(json.dumps(transport))
    attestation = {
        "schema_version": 1,
        "scope": "claude_generation_replacement_review_completion",
        "authority": "conductor_owned_original_receipt",
        "task_id": verifier.REVIEW_TASK,
        "reservation_id": review["review_reservation_id"],
        "session_id": review["session_id"],
        "original_receipt": {"path": str(review_path), "sha256": sha256(review_path.read_bytes())},
        "review_attempt_sha256": sha256(canonical_json(attempt)),
        "review_transport_sha256": sha256(transport_path.read_bytes()),
        "reviewed_input_sha256": sha256(reviewed_path.read_bytes()),
        "native_output_to_review_verified": False,
    }
    attestation_path = root / ".flow/claude-generation-review-attestations/claude-replacement_1.json"
    attestation_path.parent.mkdir(parents=True)
    attestation_path.write_text(json.dumps(attestation))
    packet = {
        "schema_version": 2,
        "scope": "claude_generation_replacement_review",
        "profile": verifier.PROFILE,
        "completion_attestation_sha256": sha256(attestation_path.read_bytes()),
    }
    for key, path in (("diagnosis", diagnosis), ("reviewed_input", reviewed_path), ("independent_review", review_path)):
        packet[key] = {"path": str(path), "sha256": sha256(path.read_bytes())}
    path = root / "packet.json"
    path.write_text(json.dumps(packet))
    return path, sha256(path.read_bytes())


@pytest.mark.asyncio
async def test_only_one_exact_independently_reviewed_replacement_can_complete_three_gates(static, monkeypatch):
    async def observe(check, *_args):
        return {"check": check, "passed": check != "text" or observe.replaced}

    observe.replaced = False
    monkeypatch.setattr(verifier, "run_check", observe)
    await verifier.verify(arguments())
    packet, checksum = replacement_packet(static, verifier.authoritative_history())
    observe.replaced = True
    second = await verifier.verify(
        arguments(label="replacement", replacement_review=packet, replacement_review_sha256=checksum)
    )
    assert second["gate_passed"] is True and second["submission_ledger"][-1]["replacement"] is True
    await verifier.verify(arguments(label="read", check="read"))
    fourth = await verifier.verify(arguments(label="cancel", check="cancel"))
    assert fourth["all_claude_generation_gates_passed"] is True and fourth["submissions_consumed_total"] == 4
    refused = await verifier.verify(arguments(label="fifth", check="read"))
    assert refused["live_submitted"] is False and refused["submissions_consumed_total"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [{"model": "gpt-6-astra"}, {"effort": "ultra"}, {"verdict": "NEEDS_WORK"}, {"id": "another-task"}]
)
async def test_replacement_rejects_wrong_reviewer_identity_or_verdict(static, monkeypatch, change):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": False}))
    await verifier.verify(arguments())
    history = verifier.authoritative_history()
    packet, checksum = replacement_packet(static, history, review_changes=change)
    with pytest.raises(ValueError):
        verifier.submission_authorization(history, "text", binding()["candidate_sha256"], packet, checksum)


@pytest.mark.asyncio
async def test_replacement_is_bound_to_current_candidate_and_completion_attestation(static, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", AsyncMock(return_value={"check": "text", "passed": False}))
    await verifier.verify(arguments())
    history = verifier.authoritative_history()
    packet, checksum = replacement_packet(static, history)
    with pytest.raises(ValueError, match="current candidate"):
        verifier.submission_authorization(history, "text", "f" * 64, packet, checksum)
    attestation = static / ".flow/claude-generation-review-attestations/claude-replacement_1.json"
    attestation.write_text("{}")
    with pytest.raises(ValueError, match="attestation"):
        verifier.submission_authorization(history, "text", binding()["candidate_sha256"], packet, checksum)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "legacy_packet",
        "copied_receipt",
        "original_overwrite",
        "receipt_byte_race",
        "diagnosis",
        "failed_input",
        "marker",
        "attestation_path",
    ],
)
async def test_rejected_completion_proof_never_reserves_or_dispatches_a_replacement(static, monkeypatch, case):
    run = AsyncMock(return_value={"check": "text", "passed": False})
    monkeypatch.setattr(verifier, "run_check", run)
    await verifier.verify(arguments())
    packet_path, _ = replacement_packet(static, verifier.authoritative_history())
    packet = json.loads(packet_path.read_bytes())
    if case == "legacy_packet":
        packet["schema_version"] = 1
        packet["review_run_sha256"] = packet.pop("completion_attestation_sha256")
    elif case == "copied_receipt":
        copied = static / "test_output/bundled-generation/copied.json"
        copied.write_bytes(Path(packet["independent_review"]["path"]).read_bytes())
        packet["independent_review"]["path"] = str(copied)
    elif case == "original_overwrite":
        receipt = Path(packet["independent_review"]["path"])
        receipt.write_bytes(receipt.read_bytes() + b"\n")
    elif case == "receipt_byte_race":
        receipt = Path(packet["independent_review"]["path"])
        original = receipt.read_bytes()
        receipt.write_bytes(original + b"\n")
        packet["independent_review"]["sha256"] = sha256(receipt.read_bytes())
        complete = verifier.completion_proof

        def restored_original(*args):
            receipt.write_bytes(original)
            return complete(*args)

        monkeypatch.setattr(verifier, "completion_proof", restored_original)
    elif case in {"diagnosis", "failed_input"}:
        key = "diagnosis" if case == "diagnosis" else "reviewed_input"
        path = Path(packet[key]["path"])
        value = json.loads(path.read_bytes())
        value["technical_diagnosis" if case == "diagnosis" else "failed_evidence_sha256"] = "0" * 64
        path.write_bytes(canonical_json(value))
        packet[key]["sha256"] = sha256(path.read_bytes())
    elif case == "marker":
        receipt = Path(packet["independent_review"]["path"])
        value = json.loads(receipt.read_bytes())
        value["review"] = "Synthetic review fixture without an approval marker."
        receipt.write_bytes(canonical_json(value))
        packet["independent_review"]["sha256"] = sha256(receipt.read_bytes())
    else:
        path = static / ".flow/claude-generation-review-attestations/claude-replacement_1.json"
        path.rename(path.with_name("caller-selected.json"))
    packet_path.write_bytes(canonical_json(packet))
    result = await verifier.verify(
        arguments(
            label="refused-replacement",
            replacement_review=packet_path,
            replacement_review_sha256=sha256(packet_path.read_bytes()),
        )
    )
    assert result["live_submitted"] is False and result["submissions_consumed_total"] == 1
    assert not (verifier.LINEAGE_ROOT / "refused-replacement/submitted.json").exists()
    assert run.await_count == len(verifier.authoritative_history()) == 1


@pytest.mark.asyncio
async def test_lock_serializes_reservations_and_nonlive_never_submits(static, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def observe(check, *_args):
        entered.set()
        await release.wait()
        return {"check": check, "passed": True}

    run = AsyncMock(side_effect=observe)
    monkeypatch.setattr(verifier, "run_check", run)
    dry = await verifier.verify(arguments(label="dry", live=False))
    assert dry["live_submitted"] is False and not (verifier.LINEAGE_ROOT / "dry/submitted.json").exists()
    first = asyncio.create_task(verifier.verify(arguments()))
    await entered.wait()
    try:
        with pytest.raises(ValueError, match="Another"):
            await verifier.verify(arguments(label="concurrent", check="read"))
    finally:
        release.set()
        await first
    assert run.await_count == 1 and len(verifier.authoritative_history()) == 1


def _synthetic_cli(tmp_path, check, mode="normal", notice=None):
    useful = "Write an engineering migration plan for Atlas on Friday, owned by Mira. Include exactly two risks, a rollback plan, and acceptance checks with measurable outcomes."
    script = tmp_path / "synthetic-claude"
    script.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import json, os, sys, time, shlex, subprocess
from pathlib import Path
settings=json.loads(Path('.claude/settings.json').read_text())
def hook(name, **data):
    command=settings['hooks'][name][0]['hooks'][0]['command']
    result=subprocess.run(shlex.split(command),input=json.dumps(dict(hook_event_name=name,**data)),text=True,capture_output=True,check=True)
    assert json.loads(result.stdout).get('hookSpecificOutput',{}).get('permissionDecision') is None
hook('SessionStart')
request=json.loads(sys.stdin.read().split("\\n\\n",1)[1])
model=sys.argv[sys.argv.index('--model')+1]
def emit(event):
    print(json.dumps(dict(session_id='synthetic-session',**event)),flush=True)
emit(dict(type='system',subtype='init',model=model))
"""
        + (f"emit({notice!r})\n" if notice is not None else "")
        + """emit(dict(type='assistant',message={'content':[{'type':'tool_use','name':'Read','input':{'file_path':'tool-context.txt'}}]}))
"""
        + (
            "time.sleep(30)\n"
            if check == "cancel"
            else f"hook('PreToolUse',tool_name='Read',tool_input={{'file_path':str(Path('tool-context.txt').resolve())}})\nresult=Path('tool-context.txt').read_text() if {check!r}=='read' else {useful!r}\nemit(dict(type='result',subtype='success',is_error=False,result=result,permission_denials=[]))\n"
        )
    )
    if mode == "no_start":
        script.write_text(script.read_text().replace("hook('SessionStart')", "pass"))
    elif mode == "no_read_hook":
        script.write_text(
            script.read_text().replace(
                "hook('PreToolUse',tool_name='Read',tool_input={'file_path':str(Path('tool-context.txt').resolve())})",
                "pass",
            )
        )
    elif mode == "changed_control":
        script.write_text(
            script.read_text().replace(
                "hook('SessionStart')", "Path('.claude/settings.json').write_text('{}'); hook('SessionStart')"
            )
        )
    elif mode == "terminal_same_chunk":
        assistant = "emit(dict(type='assistant',message={'content':[{'type':'tool_use','name':'Read','input':{'file_path':'tool-context.txt'}}]}))"
        terminal = "dict(session_id='synthetic-session',type='result',subtype='success',is_error=False,result='already complete',permission_denials=[])"
        activity = "dict(session_id='synthetic-session',type='assistant',message={'content':[{'type':'text','text':'working'}]})"
        replacement = f"sys.stdout.write(json.dumps({activity})+'\\n'+json.dumps({terminal})+'\\n'); sys.stdout.flush()"
        script.write_text(
            script.read_text().replace(assistant, replacement).replace("time.sleep(30)", "time.sleep(0.3)")
        )
    script.chmod(0o700)
    return script


@pytest.mark.asyncio
@pytest.mark.parametrize("check", verifier.CHECKS)
async def test_real_production_service_and_process_cleanup_with_synthetic_cli(tmp_path, monkeypatch, check):
    monkeypatch.setattr(verifier, "_assert_execution_provenance", lambda: None)
    model = verifier.DEFAULT_MODEL
    script = _synthetic_cli(tmp_path, check)
    preflight = tmp_path / "preflight.json"
    preflight.write_text('{"synthetic":true}')
    selected = {
        **verifier._runtime_identity(),
        "model": model,
        "executable_sha256": executable_digest(str(script)),
        "recipe_sha256": claude_generation_recipe_digest(),
        "parser_sha256": parser_contract_digest(),
    }
    result = await verifier.run_check(check, str(script), tmp_path / "workspace", selected, preflight, 10)
    assert result["passed"] is True and result["dispatch_count"] == result["spawn_count"] == 1
    assert result["pool_running_after_shutdown"] == result["pool_children_after_shutdown"] == 0
    assert result["raw_output_retained"] is False and "synthetic-session" not in json.dumps(result)


@pytest.mark.asyncio
async def test_cancel_gate_cannot_pass_after_native_terminal_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "_assert_execution_provenance", lambda: None)
    script = _synthetic_cli(tmp_path, "cancel", "terminal_same_chunk")
    preflight = tmp_path / "preflight.json"
    preflight.write_text('{"synthetic":true}')
    selected = {
        **verifier._runtime_identity(),
        "model": verifier.DEFAULT_MODEL,
        "executable_sha256": executable_digest(str(script)),
        "recipe_sha256": claude_generation_recipe_digest(),
        "parser_sha256": parser_contract_digest(),
    }
    result = await verifier.run_check("cancel", str(script), tmp_path / "workspace", selected, preflight, 10)
    assert result["passed"] is False
    assert result["native_terminal_observed"] is True and result["event_counts"]["result"] == 1
    assert result["spawn_count"] == 1 and result["process_groups_absent_after_cleanup"] is True
    assert result["pool_running_after_shutdown"] == result["pool_children_after_shutdown"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change_at", ["after_reservation", "prelaunch"])
async def test_changed_preflight_interpreter_consumes_reservation_without_spawning(static, monkeypatch, change_at):
    script = _synthetic_cli(static, "text")
    frozen = verifier._runtime_identity()
    selected = {
        **binding(),
        **frozen,
        "executable_sha256": executable_digest(str(script)),
        "recipe_sha256": claude_generation_recipe_digest(),
        "parser_sha256": parser_contract_digest(),
    }
    selected["candidate_sha256"] = verifier._candidate_digest(selected)
    monkeypatch.setattr(verifier, "preflight", lambda _: (str(script), selected))
    checked = []

    def observed():
        checked.append(True)
        changed = change_at == "after_reservation" or len(checked) > 1
        return {**frozen, "python_executable_sha256": "0" * 64 if changed else frozen["python_executable_sha256"]}

    monkeypatch.setattr(verifier, "_runtime_identity", observed)
    run = AsyncMock(wraps=verifier.run_check)
    monkeypatch.setattr(verifier, "run_check", run)
    result = await verifier.verify(arguments())
    assert result["live_submitted"] is True and result["submissions_consumed_total"] == 1
    assert result["gate_passed"] is False and verifier.authoritative_history()[0]["entry"]["passed"] is False
    if change_at == "after_reservation":
        assert result["error_class"] == "ValueError" and run.await_count == 0
    else:
        assert run.await_count == 1 and result["observation"]["spawn_count"] == 0
        assert result["observation"]["pool_running_after_shutdown"] == 0


def test_fixture_hook_interpreter_is_frozen_through_selftest_and_validation(tmp_path, monkeypatch):
    controls = verifier.create_fixture(tmp_path / "workspace")
    original = verifier.executable_digest
    python = str(Path(sys.executable).resolve(strict=True))
    monkeypatch.setattr(verifier, "executable_digest", lambda path: "0" * 64 if str(path) == python else original(path))
    with pytest.raises(ValueError, match="interpreter"):
        controls.validate(require_fresh=True)


def test_help_and_binary_pin_offer_no_reset_or_executable_override():
    parser = verifier.argument_parser()
    args = parser.parse_args(["--label", "synthetic"])
    assert args.live is False and args.model == "claude-opus-5-5"
    text = parser.format_help()
    assert "--live" in text and "--replacement-review" in text
    assert "--executable" not in text and "--reset" not in text
    assert verifier.RESEARCHED_EXECUTABLE_SHA256 == "b9ac5374839f14abf3f1f45610637c332b5daee5ce0d1634726cef87aab78e6a"


@pytest.mark.parametrize("case", ["valid", "binary", "version", "arguments", "production_enabled"])
def test_static_preflight_requires_exact_binary_recipe_and_disabled_production(
    isolated, monkeypatch, disabled_production, case
):
    from dataclasses import replace

    executable = isolated / "synthetic-identity"
    executable.write_text("synthetic executable bytes; never launched")
    calls = []
    monkeypatch.setattr(verifier.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(
        verifier, "RESEARCHED_EXECUTABLE_SHA256", "0" * 64 if case == "binary" else executable_digest(str(executable))
    )
    monkeypatch.setattr(verifier, "implementation_manifest", lambda: {"schema_version": 1, "files": []})

    def version(command, environment):
        calls.append(command)
        assert "HOME" in environment and "ANTHROPIC_API_KEY" not in environment
        return 0, b"changed version" if case == "version" else verifier.VERSION.encode()

    monkeypatch.setattr(verifier, "bounded_version", version)
    if case == "arguments":
        monkeypatch.setattr(verifier, "claude_generation_arguments", lambda: ("--permission-mode", "bypassPermissions"))
    if case == "production_enabled":
        monkeypatch.setattr(verifier, "DEFAULT_CLAUDE_NATIVE_ADMISSION", replace(disabled_production, verified=True))
    if case == "valid":
        selected, proof = verifier.preflight(verifier.DEFAULT_MODEL)
        assert selected == str(executable) and proof["candidate_sha256"] == verifier._candidate_digest(proof)
    else:
        with pytest.raises(ValueError):
            verifier.preflight(verifier.DEFAULT_MODEL)
    assert len(calls) == (0 if case in {"binary", "production_enabled"} else 1)


def test_staged_package_pin_and_actual_runtime_identity_are_observed_without_a_native_call(tmp_path):
    import shutil
    import subprocess

    staged = tmp_path / "staged"
    staged.mkdir()
    for package in ("clink", "utils"):
        shutil.copytree(
            verifier.PACKAGE_ROOT / package, staged / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    shutil.copy2(verifier.PACKAGE_ROOT / "config.py", staged / "config.py")
    manifest, checksum = verifier._package_sources(staged / "clink")
    assert manifest["files"] and verifier._select_package(staged, checksum) == (staged, checksum)
    harness = Path(verifier.__file__).resolve()
    code = "import json,runpy,sys; sys.argv=sys.argv[1:]; result=runpy.run_path(sys.argv[0],run_name='verifier_identity'); print(json.dumps(result['_runtime_identity']()))"
    command = [
        sys.executable,
        "-c",
        code,
        str(harness),
        "--package-root",
        str(staged),
        "--package-source-sha256",
        checksum,
    ]
    observed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    identity = json.loads(observed.stdout)
    assert identity["clink_package_directory"] == str(staged / "clink")
    assert identity["package_source_sha256"] == checksum
    assert identity["python_executable"] == str(Path(sys.executable).resolve(strict=True))
    assert identity["python_executable_sha256"] == executable_digest(identity["python_executable"])
    with pytest.raises(ValueError, match="explicit pin"):
        verifier._select_package(staged, "0" * 64)
    alias = tmp_path / "alias"
    alias.symlink_to(staged, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical"):
        verifier._select_package(alias, checksum)


@pytest.mark.parametrize("case", ["missing_utils", "changed_utils", "symlinked_utils"])
def test_staged_bootstrap_cannot_import_checkout_or_changed_utils_before_pin_validation(tmp_path, case):
    import shutil
    import subprocess

    staged = tmp_path / "staged"
    staged.mkdir()
    for package in ("clink", "utils"):
        shutil.copytree(
            verifier.PACKAGE_ROOT / package, staged / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    shutil.copy2(verifier.PACKAGE_ROOT / "config.py", staged / "config.py")
    _, checksum = verifier._package_sources(staged / "clink")
    if case == "missing_utils":
        shutil.rmtree(staged / "utils")
    elif case == "changed_utils":
        with (staged / "utils/__init__.py").open("a") as output:
            output.write("\nraise RuntimeError('UNPINNED_UTILS_IMPORTED')\n")
    else:
        outside = tmp_path / "outside-utils"
        (staged / "utils").rename(outside)
        (staged / "utils").symlink_to(outside, target_is_directory=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(verifier.__file__).resolve()),
            "--package-root",
            str(staged),
            "--package-source-sha256",
            checksum,
            "--help",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode != 0
    assert "UNPINNED_UTILS_IMPORTED" not in completed.stdout + completed.stderr


def test_fixture_hook_denies_everything_except_canonical_token_read_without_granting(tmp_path):
    import subprocess

    controls = verifier.create_fixture(tmp_path / "workspace")
    settings = json.loads((controls.root / ".claude/settings.json").read_bytes())
    assert "permissions" not in settings
    assert set(settings["hooks"]) == {"SessionStart", "PreToolUse"}
    token = controls.root / "tool-context.txt"
    for tool, path, allowed in (
        ("Read", str(token), True),
        ("Read", "tool-context.txt", False),
        ("Read", str(tmp_path / "outside"), False),
        ("Write", str(token), False),
        ("Bash", str(token), False),
        ("Agent", str(token), False),
        ("mcp__unrelated__write", str(token), False),
    ):
        event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"file_path": path}}
        completed = subprocess.run(
            [sys.executable, str(controls.root / "scope_hook.py"), "--self-test"],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        result = json.loads(completed.stdout)
        decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert decision is None if allowed else decision == "deny"
    assert controls.observation()["session_start_observed"] is False
    assert controls.summary()["selftest_passed"] is True
    token.write_text("changed token")
    with pytest.raises(ValueError):
        controls.validate()


@pytest.mark.parametrize("case", ["preloaded", "poison_path", "missing_submodule", "helper_reinsertion"])
def test_staged_import_guard_closes_other_python_resolution_paths(tmp_path, case):
    import os
    import shutil
    import subprocess

    staged = tmp_path / "staged"
    staged.mkdir()
    for package in ("clink", "utils"):
        shutil.copytree(
            verifier.PACKAGE_ROOT / package, staged / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    shutil.copy2(verifier.PACKAGE_ROOT / "config.py", staged / "config.py")
    if case == "missing_submodule":
        (staged / "utils/env.py").unlink()
    _, checksum = verifier._package_sources(staged / "clink")
    harness = str(Path(verifier.__file__).resolve())
    setup = ""
    if case == "preloaded":
        setup = f"sys.path.insert(0,{str(verifier.PACKAGE_ROOT)!r}); import utils; "
    observe = "result['_runtime_identity']()"
    if case == "helper_reinsertion":
        observe = f"sys.path.insert(0,{str(verifier.PACKAGE_ROOT)!r}); sys.modules.pop('config',None); import config; assert str(result['Path'](config.__file__).resolve()).startswith({str(staged)!r}); result['_runtime_identity']()"
    code = (
        "import json,runpy,sys; "
        + setup
        + "sys.argv=sys.argv[1:]; result=runpy.run_path(sys.argv[0],run_name='identity'); "
        + observe
    )
    environment = dict(os.environ, PYTHONPATH=str(verifier.PACKAGE_ROOT))
    completed = subprocess.run(
        [sys.executable, "-c", code, harness, "--package-root", str(staged), "--package-source-sha256", checksum],
        cwd=verifier.PACKAGE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (completed.returncode == 0) is (case in {"poison_path", "helper_reinsertion"}), completed.stderr


@pytest.mark.parametrize("key", ["disableAllHooks", "allowManagedHooksOnly"])
def test_known_native_hook_disabling_stops_without_reading_auth_stores(tmp_path, monkeypatch, key):
    home = tmp_path / "native-home"
    config = home / ".claude"
    config.mkdir(parents=True)
    (config / "settings.json").write_text(json.dumps({key: True, "env": {"PRIVATE_VALUE": "never-export"}}))
    (home / ".claude.json").write_text("not-json-auth-store-must-not-be-read")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="disables"):
        verifier.hook_policy(workspace)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_start", "no_read_hook", "changed_control"])
async def test_missing_or_changed_native_controls_consume_a_failed_attempt(static, monkeypatch, case):
    script = _synthetic_cli(static, "read", case)
    selected = binding()
    selected.update(
        executable_sha256=executable_digest(str(script)),
        recipe_sha256=claude_generation_recipe_digest(),
        parser_sha256=parser_contract_digest(),
    )
    selected["candidate_sha256"] = verifier._candidate_digest(selected)
    monkeypatch.setattr(verifier, "preflight", lambda _: (str(script), selected))
    result = await verifier.verify(arguments(check="read"))
    assert result["live_submitted"] is True and result["submissions_consumed_total"] == 1
    assert result["gate_passed"] is False
    if case == "changed_control":
        assert result["error_class"] == "ValueError"
    else:
        assert result["observation"]["fixture_hook_observations"]["session_start_observed"] is (case != "no_start")
    assert verifier.authoritative_history()[0]["entry"]["passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "notice,reason",
    [
        (
            {"type": "system", "subtype": "informational", "content": "PRIVATE_NOTICE", "level": "PRIVATE_LEVEL"},
            "notice_shape",
        ),
        ({"type": "system", "subtype": "PRIVATE_SUBTYPE", "content": "PRIVATE_NOTICE"}, "unknown_system_subtype"),
        (
            {
                "type": "system",
                "subtype": "informational",
                "content": "PRIVATE_NOTICE",
                "level": "notice",
                "prompt_submit_outcome": {"kind": "stopped_by_hook", "prompt_kept": True},
            },
            "notice_stop",
        ),
    ],
)
async def test_synthetic_parser_failure_retains_only_closed_diagnostic(tmp_path, monkeypatch, notice, reason):
    monkeypatch.setattr(verifier, "_assert_execution_provenance", lambda: None)
    script = _synthetic_cli(tmp_path, "text", notice=notice)
    preflight = tmp_path / "preflight.json"
    preflight.write_text('{"synthetic":true}')
    selected = {
        **verifier._runtime_identity(),
        "model": verifier.DEFAULT_MODEL,
        "executable_sha256": executable_digest(str(script)),
        "recipe_sha256": claude_generation_recipe_digest(),
        "parser_sha256": parser_contract_digest(),
    }
    result = await verifier.run_check("text", str(script), tmp_path / "workspace", selected, preflight, 10)
    assert result["passed"] is False and result["error_code"] == "protocol_error"
    assert result["parser_diagnostic"] == {"reason": reason, "event_index": 2}
    assert result["dispatch_count"] == result["spawn_count"] == 1
    assert result["pool_running_after_shutdown"] == result["pool_children_after_shutdown"] == 0
    assert result["raw_output_retained"] is False
    assert "PRIVATE" not in json.dumps(result) and "synthetic-session" not in json.dumps(result)
