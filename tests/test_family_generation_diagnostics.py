"""Check bounded verifier diagnostics using only synthetic, in-memory process data."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import struct
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import verify_family_generation as verifier

PRIVATE = "PRIVATE_EXCEPTION_MESSAGE_FRAME_LOCAL_PROMPT_CREDENTIAL"
RUNNER = verifier.PROJECT / "clink/runner.py"
RUN_ID = "5ebfe91a-e4ad-4a09-81db-a984605d1fc0"
EXECUTABLE_SHA256 = "a" * 64


def cleanup_failure(
    line: int,
    *,
    error: BaseException | None = None,
    helper_line: int | None = None,
    filename: str | None = None,
    function: str = "_cleanup",
) -> BaseException:
    """Compile trusted-looking locations without running cleanup or any signal call."""
    failure = error if error is not None else PermissionError(errno.EPERM, PRIVATE)
    source = [""] * 180
    if helper_line is not None:
        source[118] = "def _signal_group(private=PRIVATE):"
        for position in range(120, helper_line):
            source[position - 1] = "    pass"
        source[helper_line - 1] = "    raise failure"
    source[125] = f"def {function}(private=PRIVATE):"
    for position in range(127, line):
        source[position - 1] = "    pass"
    source[line - 1] = "    _signal_group()" if helper_line is not None else "    raise failure"
    namespace = {"failure": failure, "PRIVATE": PRIVATE}
    exec(compile("\n".join(source), filename or str(RUNNER), "exec"), namespace)
    try:
        namespace[function]()
    except BaseException as captured:
        return captured
    raise AssertionError("Synthetic cleanup did not raise")


def diagnostic(error: BaseException, phase: str = "production_cleanup", runner_sha256: str | None = None) -> dict:
    return verifier.cleanup_exception_diagnostic(
        error,
        phase,
        verifier.PINNED_DIAGNOSTIC_RUNNER_SHA256 if runner_sha256 is None else runner_sha256,
    )


def location(function: str, line: int) -> dict:
    return {"file": "clink/runner.py", "function": function, "line": line}


def test_runner_pin_matches_the_unchanged_source():
    assert verifier.PINNED_DIAGNOSTIC_RUNNER_SHA256 == hashlib.sha256(RUNNER.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("line", "helper_line", "operation"),
    [(127, 121, "term_group"), (132, None, "probe_group"), (136, 121, "kill_group"), (137, None, "wait_leader")],
)
def test_exact_pinned_cleanup_callsites_are_distinguished_and_redacted(line, helper_line, operation):
    failure = cleanup_failure(line, helper_line=helper_line)
    result = diagnostic(failure)
    assert result == {
        "exception_diagnostic": {
            "exception_class": "PermissionError",
            "exception_location": location(
                "_signal_group" if helper_line is not None else "_cleanup", helper_line or line
            ),
            "os_error_number": errno.EPERM,
        },
        "execution_phase": "production_cleanup",
        "cleanup_operation": operation,
        "cleanup_caller": location("_cleanup", line),
    }
    retained = json.dumps(result)
    assert PRIVATE not in retained
    assert str(RUNNER.parent) not in retained
    assert "traceback" not in retained and "private" not in retained and "failure" not in retained
    assert failure.errno == errno.EPERM and str(failure).endswith(PRIVATE)


@pytest.mark.parametrize("source_hash", ["0" * 64, "", PRIVATE, None])
def test_changed_or_missing_runner_source_never_classifies_a_cleanup_operation(source_hash):
    result = verifier.cleanup_exception_diagnostic(
        cleanup_failure(127, helper_line=121), "production_cleanup", source_hash
    )
    assert result["cleanup_operation"] == "unknown"
    assert result["cleanup_caller"] is None
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize(
    ("line", "helper_line", "filename", "function"),
    [
        (128, 121, None, "_cleanup"),
        (127, 122, None, "_cleanup"),
        (137, None, "/untrusted/clink/runner.py", "_cleanup"),
        (127, 121, None, "other_cleanup"),
        (133, None, None, "_cleanup"),
    ],
)
def test_changed_or_untrusted_callsites_remain_unknown(line, helper_line, filename, function):
    result = diagnostic(cleanup_failure(line, helper_line=helper_line, filename=filename, function=function))
    assert result["cleanup_operation"] == "unknown"
    assert result["cleanup_caller"] is None
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("phase", ["production_cleanup", "process_group_probe", "pool_execute", PRIVATE, "", None])
def test_execution_phase_is_a_fixed_identifier(phase):
    result = diagnostic(PermissionError(errno.EPERM, PRIVATE), phase)
    assert result["execution_phase"] == (
        phase if phase in {"production_cleanup", "process_group_probe", "pool_execute"} else "unknown"
    )
    assert result["cleanup_operation"] == "unknown" and result["cleanup_caller"] is None
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("number", [0, errno.EPERM, errno.EACCES, 4095, -1, 4096, True, None])
def test_errno_is_bounded_without_retaining_exception_text(number):
    result = diagnostic(PermissionError(number, PRIVATE))["exception_diagnostic"]
    if type(number) is int and 0 <= number <= 4095:
        assert result["os_error_number"] == number
    else:
        assert "os_error_number" not in result
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"exception_class": PRIVATE},
        {"exception_class": "PermissionError", "message": PRIVATE},
        {"exception_class": "PermissionError", "os_error_number": True},
        {"exception_class": "PermissionError", "os_error_number": 4096},
        {
            "exception_class": "PermissionError",
            "exception_location": {"file": PRIVATE, "function": "_cleanup", "line": 127},
        },
    ],
)
def test_malformed_native_diagnostic_is_rejected_without_retention(monkeypatch, value):
    helper = Mock(return_value=value)
    monkeypatch.setattr(verifier, "exception_diagnostic", helper)
    failure = cleanup_failure(127, helper_line=121)
    with pytest.raises(ValueError) as caught:
        diagnostic(failure)
    helper.assert_called_once_with(failure)
    assert PRIVATE not in str(caught.value)


def test_unknown_class_and_unavailable_locations_remain_explicitly_unknown():
    failure = type(PRIVATE, (Exception,), {})(PRIVATE)
    assert diagnostic(failure) == {
        "exception_diagnostic": {"exception_class": "unknown"},
        "execution_phase": "production_cleanup",
        "cleanup_operation": "unknown",
        "cleanup_caller": None,
    }


def test_diagnostic_capture_failure_is_propagated_without_rendering_the_error(monkeypatch):
    capture_failure = RuntimeError(PRIVATE)
    helper = Mock(side_effect=capture_failure)
    monkeypatch.setattr(verifier, "exception_diagnostic", helper)
    original = cleanup_failure(127, helper_line=121)
    with pytest.raises(RuntimeError) as caught:
        diagnostic(original)
    assert caught.value is capture_failure
    helper.assert_called_once_with(original)
    assert original.errno == errno.EPERM


def identity(pid: int = 123, **changes) -> dict[str, int]:
    return {
        "pid": pid,
        "pgid": 123,
        "sid": 123,
        "uid": 501,
        "start_seconds": 1780000000,
        "start_microseconds": 2345,
        **changes,
    }


def collector(reader=None):
    return verifier.CleanupDiagnostics(
        RUN_ID,
        EXECUTABLE_SHA256,
        identity_reader=Mock(return_value=None) if reader is None else reader,
    )


def test_owned_leader_and_designated_child_have_only_two_bounded_snapshots():
    reader = Mock(side_effect=lambda pid: identity(pid))
    diagnostics = collector(reader)
    leader = Mock(pid=123)
    diagnostics.observe_leader(leader, {leader})
    diagnostics.observe_leader(leader, {leader})
    diagnostics.observe_fixture_child(456)
    diagnostics.observe_fixture_child(789)
    diagnostics.cleanup_entry(Mock(pid=123))
    diagnostics.cleanup_entry(leader)
    diagnostics.cleanup_entry(leader)

    assert [call.args[0] for call in reader.call_args_list] == [123, 123, 456, 456, 123, 123, 456, 456]
    sidecar = diagnostics.sidecar({"status": "failed", "output": PRIVATE})
    assert set(sidecar["processes"]) == {"leader", "fixture_child"}
    for role, pid in (("leader", 123), ("fixture_child", 456)):
        snapshots = sidecar["processes"][role]
        assert [entry["moment"] for entry in snapshots] == ["initial", "cleanup_entry"]
        assert [entry["state"] for entry in snapshots] == ["observed", "observed"]
        for snapshot in snapshots:
            assert snapshot["identity"] == identity(pid)
            assert snapshot["fingerprint_sha256"] == hashlib.sha256(verifier.canonical_json(identity(pid))).hexdigest()
    assert PRIVATE not in json.dumps(sidecar)
    assert verifier.valid_cleanup_diagnostic_sidecar(sidecar)


@pytest.mark.parametrize("ownership", ["none", "different_handle", "multiple"])
def test_pid_without_exact_single_owned_handle_never_triggers_a_metadata_read(ownership):
    reader = Mock(side_effect=AssertionError("Unowned identity read"))
    diagnostics = collector(reader)
    leader = Mock(pid=123)
    other = Mock(pid=123)
    children = {"none": set(), "different_handle": {other}, "multiple": {leader, other}}[ownership]
    diagnostics.observe_leader(leader, children)
    diagnostics.observe_fixture_child(456)
    diagnostics.cleanup_entry(leader)
    reader.assert_not_called()
    assert diagnostics.processes["leader"][0]["state"] == "unknown"
    assert diagnostics.processes["fixture_child"][0]["identity"] is None


def test_bare_fixture_pid_before_any_owned_leader_never_triggers_a_metadata_read():
    reader = Mock(side_effect=AssertionError("Bare PID has no ownership"))
    diagnostics = collector(reader)
    diagnostics.observe_fixture_child(456)
    diagnostics.observe_fixture_child(456)
    reader.assert_not_called()
    assert diagnostics.processes == {
        "leader": [],
        "fixture_child": [{"moment": "initial", "state": "unknown", "identity": None, "fingerprint_sha256": None}],
    }


@pytest.mark.parametrize("field", ["pgid", "sid", "uid"])
def test_designated_child_outside_the_owned_group_session_or_user_is_unknown(field):
    reader = Mock(side_effect=lambda pid: identity(pid, **({field: 999} if pid == 456 else {})))
    diagnostics = collector(reader)
    leader = Mock(pid=123)
    diagnostics.observe_leader(leader, {leader})
    diagnostics.observe_fixture_child(456)
    record = diagnostics.processes["fixture_child"][0]
    assert record["state"] == "unknown"
    assert record["identity"] is None and record["fingerprint_sha256"] is None
    assert reader.call_count == 4


@pytest.mark.parametrize(
    ("first", "second", "state"),
    [
        (None, None, "unknown"),
        (None, identity(), "unknown"),
        (identity(), {**identity(), "argv": PRIVATE}, "unknown"),
        (identity(), identity(start_microseconds=2346), "raced"),
        (identity(456), identity(456), "raced"),
    ],
)
def test_unavailable_malformed_and_raced_identity_reads_have_no_fingerprint(first, second, state):
    reader = Mock(side_effect=[first, second])
    diagnostics = collector(reader)
    leader = Mock(pid=123)
    diagnostics.observe_leader(leader, {leader})
    record = diagnostics.processes["leader"][0]
    assert record == {"moment": "initial", "state": state, "identity": None, "fingerprint_sha256": None}
    assert reader.call_count == 2
    assert PRIVATE not in json.dumps(diagnostics.sidecar({"status": "failed"}))


@pytest.mark.parametrize("role", ["leader", "fixture_child"])
def test_pid_reuse_between_initial_and_cleanup_is_explicit_and_never_retains_replacement(role):
    current = {123: identity(), 456: identity(456)}
    reader = Mock(side_effect=lambda pid: dict(current[pid]))
    diagnostics = collector(reader)
    leader = Mock(pid=123)
    diagnostics.observe_leader(leader, {leader})
    diagnostics.observe_fixture_child(456)
    pid = 123 if role == "leader" else 456
    current[pid]["start_seconds"] += 1
    diagnostics.cleanup_entry(leader)
    initial, cleanup = diagnostics.processes[role]
    assert initial["state"] == "observed"
    assert cleanup == {"moment": "cleanup_entry", "state": "reused", "identity": None, "fingerprint_sha256": None}


def test_unknown_initial_identity_cannot_become_owned_at_cleanup():
    reader = Mock(side_effect=[None, None, identity(), identity()])
    diagnostics = collector(reader)
    leader = Mock(pid=123)
    diagnostics.observe_leader(leader, {leader})
    diagnostics.cleanup_entry(leader)
    assert [item["state"] for item in diagnostics.processes["leader"]] == ["unknown", "unknown"]
    assert all(item["identity"] is None for item in diagnostics.processes["leader"])
    assert reader.call_count == 4


@pytest.mark.parametrize("failure", [RuntimeError(PRIVATE), KeyboardInterrupt(PRIVATE)])
def test_identity_capture_failure_is_redacted_and_does_not_escape(failure):
    reader = Mock(side_effect=failure)
    diagnostics = collector(reader)
    leader = Mock(pid=123)
    diagnostics.observe_leader(leader, {leader})
    assert diagnostics.capture_failed is True
    assert diagnostics.processes["leader"][0]["state"] == "unknown"
    sidecar = diagnostics.sidecar({"status": "failed"})
    assert sidecar["capture_failed"] is True
    assert PRIVATE not in json.dumps(sidecar)
    reader.assert_called_once_with(123)


@pytest.mark.parametrize(
    "changes",
    [
        {"pid": True},
        {"pgid": 1},
        {"sid": 2**31},
        {"uid": -1},
        {"uid": 2**32},
        {"start_seconds": 0},
        {"start_seconds": 2**63},
        {"start_microseconds": -1},
        {"start_microseconds": 10**6},
        {"start_microseconds": [1]},
        {"argv": PRIVATE},
    ],
)
def test_identity_validator_rejects_non_numeric_out_of_bounds_or_extra_fields(changes):
    assert verifier.valid_diagnostic_identity(identity())
    assert not verifier.valid_diagnostic_identity({**identity(), **changes})


def test_darwin_reader_decodes_only_the_fixed_numeric_kernel_fields(monkeypatch):
    expected = identity()

    def fill_kernel_record(pid, flavor, argument, buffer, size):
        assert (pid, flavor, argument, size) == (123, 3, 0, 136)
        buffer.raw = (PRIVATE.encode() * 6)[:136]
        for offset, field in ((12, "pid"), (20, "uid"), (100, "pgid")):
            struct.pack_into("=I", buffer, offset, expected[field])
        for offset, field in ((120, "start_seconds"), (128, "start_microseconds")):
            struct.pack_into("=Q", buffer, offset, expected[field])
        return size

    kernel = Mock(side_effect=fill_kernel_record)
    library = Mock(return_value=Mock(proc_pidinfo=kernel))
    session = Mock(return_value=expected["sid"])
    monkeypatch.setattr(verifier.sys, "platform", "darwin")
    monkeypatch.setattr(verifier.ctypes, "CDLL", library)
    monkeypatch.setattr(verifier.os, "getsid", session)

    result = verifier.read_diagnostic_identity(123)
    assert result == expected
    assert all(type(value) is int for value in result.values())
    assert PRIVATE not in json.dumps(result)
    library.assert_called_once_with("/usr/lib/libproc.dylib", use_errno=True)
    kernel.assert_called_once()
    assert len(kernel.call_args.args[3]) == 136
    session.assert_called_once_with(123)


@pytest.mark.parametrize("returned_size", [0, 135])
def test_darwin_reader_treats_a_short_kernel_record_as_unknown(monkeypatch, returned_size):
    kernel = Mock(return_value=returned_size)
    monkeypatch.setattr(verifier.sys, "platform", "darwin")
    monkeypatch.setattr(verifier.ctypes, "CDLL", Mock(return_value=Mock(proc_pidinfo=kernel)))
    session = Mock(side_effect=AssertionError("A short kernel record must not trigger SID lookup"))
    monkeypatch.setattr(verifier.os, "getsid", session)

    assert verifier.read_diagnostic_identity(123) is None
    kernel.assert_called_once()
    assert kernel.call_args.args[:3] == (123, 3, 0)
    assert len(kernel.call_args.args[3]) == kernel.call_args.args[4] == 136
    session.assert_not_called()


def test_sidecar_binds_source_run_executable_and_exact_result_without_retaining_output():
    diagnostics = collector()
    diagnostics.observe_exception(cleanup_failure(136, helper_line=121), "production_cleanup")
    result = {"status": "failed", "error": {"code": "cleanup_unconfirmed", "message": PRIVATE}}
    snapshot = deepcopy(result)
    sidecar = diagnostics.sidecar(result)
    assert result == snapshot
    assert sidecar["schema_version"] == 1
    assert sidecar["kind"] == "family_cleanup_diagnostics"
    assert sidecar["run_id"] == RUN_ID and sidecar["executable_sha256"] == EXECUTABLE_SHA256
    assert sidecar["result_sha256"] == hashlib.sha256(verifier.canonical_json(result)).hexdigest()
    assert sidecar["source"] == {
        "baseline_commit": "ab01466c84ba3661e8807b54d26d53b78c94e3bf",
        **{
            key: hashlib.sha256((verifier.PROJECT / path).read_bytes()).hexdigest()
            for key, path in (
                ("runner_sha256", "clink/runner.py"),
                ("verifier_sha256", "scripts/verify_family_generation.py"),
                ("native_diagnostics_sha256", "scripts/native_diagnostics.py"),
            )
        },
    }
    assert PRIVATE not in json.dumps(sidecar)
    sidecar["source"]["runner_sha256"] = "0" * 64
    assert diagnostics.source["runner_sha256"] == verifier.PINNED_DIAGNOSTIC_RUNNER_SHA256


@pytest.fixture
def populated_sidecar():
    diagnostics = collector(Mock(side_effect=lambda pid: identity(pid)))
    leader = Mock(pid=123)
    diagnostics.observe_leader(leader, {leader})
    diagnostics.observe_fixture_child(456)
    diagnostics.cleanup_entry(leader)
    diagnostics.observe_exception(cleanup_failure(127, helper_line=121), "production_cleanup")
    return diagnostics.sidecar({"status": "failed"})


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema_version",), True),
        (("kind",), PRIVATE),
        (("run_id",), PRIVATE),
        (("result_sha256",), "0"),
        (("executable_sha256",), None),
        (("capture_failed",), 0),
        (("message",), PRIVATE),
        (("source", "baseline_commit"), "0" * 40),
        (("source", "runner_sha256"), "0" * 64),
        (("source", "argv"), PRIVATE),
        (("exception", "execution_phase"), PRIVATE),
        (("exception", "execution_phase"), []),
        (("exception", "cleanup_operation"), []),
        (("exception", "cleanup_operation"), "kill_group"),
        (("exception", "cleanup_caller", "line"), True),
        (("exception", "cleanup_caller", "file"), PRIVATE),
        (("exception", "exception_diagnostic", "message"), PRIVATE),
        (("processes", "extra"), []),
        (("processes", "leader", 0, "moment"), "cleanup_entry"),
        (("processes", "leader", 0, "state"), []),
        (("processes", "leader", 0, "state"), "unknown"),
        (("processes", "leader", 0, "fingerprint_sha256"), "0" * 64),
        (("processes", "leader", 0, "identity", "pid"), True),
        (("processes", "leader", 0, "identity", "argv"), PRIVATE),
    ],
)
def test_sidecar_validator_rejects_malformed_nested_values_without_raising(populated_sidecar, path, replacement):
    changed = deepcopy(populated_sidecar)
    target = changed
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = replacement
    assert verifier.valid_cleanup_diagnostic_sidecar(changed) is False


def test_sidecar_validator_rejects_extra_or_reordered_process_snapshots(populated_sidecar):
    extra = deepcopy(populated_sidecar)
    extra["processes"]["leader"].append(deepcopy(extra["processes"]["leader"][-1]))
    assert not verifier.valid_cleanup_diagnostic_sidecar(extra)
    reordered = deepcopy(populated_sidecar)
    reordered["processes"]["fixture_child"].reverse()
    assert not verifier.valid_cleanup_diagnostic_sidecar(reordered)


def replace_identity(snapshot, **changes):
    snapshot["identity"].update(changes)
    snapshot["fingerprint_sha256"] = hashlib.sha256(verifier.canonical_json(snapshot["identity"])).hexdigest()


@pytest.mark.parametrize("role", ["leader", "fixture_child"])
def test_sidecar_validator_rejects_replaced_cleanup_identity_even_with_a_valid_fingerprint(populated_sidecar, role):
    cleanup = populated_sidecar["processes"][role][1]
    replace_identity(cleanup, start_seconds=cleanup["identity"]["start_seconds"] + 1)
    assert not verifier.valid_cleanup_diagnostic_sidecar(populated_sidecar)


@pytest.mark.parametrize("role", ["leader", "fixture_child"])
def test_sidecar_validator_rejects_observed_cleanup_after_unknown_initial_identity(populated_sidecar, role):
    populated_sidecar["processes"][role][0].update(state="unknown", identity=None, fingerprint_sha256=None)
    assert not verifier.valid_cleanup_diagnostic_sidecar(populated_sidecar)


@pytest.mark.parametrize(("field", "replacement"), [("pgid", 999), ("sid", 999), ("uid", 999), ("pid", 123)])
def test_sidecar_validator_rejects_child_outside_leader_identity_even_with_valid_fingerprints(
    populated_sidecar, field, replacement
):
    for snapshot in populated_sidecar["processes"]["fixture_child"]:
        replace_identity(snapshot, **{field: replacement})
    assert not verifier.valid_cleanup_diagnostic_sidecar(populated_sidecar)


def test_sidecar_validator_rejects_observed_child_without_an_observed_leader(populated_sidecar):
    populated_sidecar["processes"]["leader"] = []
    assert not verifier.valid_cleanup_diagnostic_sidecar(populated_sidecar)


@pytest.mark.parametrize("failure", [RuntimeError(PRIVATE), KeyboardInterrupt(PRIVATE)])
def test_collector_capture_failure_retains_no_exception_contents(monkeypatch, failure):
    diagnostics = collector()
    helper = Mock(side_effect=failure)
    monkeypatch.setattr(verifier, "exception_diagnostic", helper)
    original = cleanup_failure(127, helper_line=121)
    diagnostics.observe_exception(original, "production_cleanup")
    diagnostics.observe_exception(cleanup_failure(136, helper_line=121), "production_cleanup")
    helper.assert_called_once_with(original)
    sidecar = diagnostics.sidecar({"status": "failed"})
    assert sidecar["exception"] is None and sidecar["capture_failed"] is True
    assert PRIVATE not in json.dumps(sidecar)


@pytest.mark.parametrize("source_state", ["changed", "missing"])
def test_collector_source_drift_or_loss_retains_only_unknown_operation(monkeypatch, source_state):
    diagnostics = collector()
    source = Mock(return_value=b"changed runner source")
    if source_state == "missing":
        source.side_effect = FileNotFoundError(PRIVATE)
    monkeypatch.setattr(verifier, "regular_bytes", source)
    diagnostics.observe_exception(cleanup_failure(127, helper_line=121), "production_cleanup")
    source.assert_called_once_with(RUNNER)
    sidecar = diagnostics.sidecar({"status": "failed"})
    assert sidecar["capture_failed"] is True
    assert sidecar["source"]["runner_sha256"] == verifier.PINNED_DIAGNOSTIC_RUNNER_SHA256
    assert sidecar["exception"]["cleanup_operation"] == "unknown"
    assert sidecar["exception"]["cleanup_caller"] is None
    assert sidecar["exception"]["exception_diagnostic"]["os_error_number"] == errno.EPERM
    assert verifier.valid_cleanup_diagnostic_sidecar(sidecar)
    assert PRIVATE not in json.dumps(sidecar)


def test_cli_diagnostic_sidecar_is_disabled_by_default():
    args = verifier.argument_parser().parse_args(["--family", "agy", "--label", "synthetic"])
    assert args.diagnostic_sidecar is None


@pytest.mark.parametrize(
    ("family", "check", "live"), [("grok", "cancel", True), ("agy", "text", True), ("agy", "cancel", False)]
)
def test_cli_diagnostics_reject_other_family_gate_or_nonlive_mode_before_any_history_read(
    monkeypatch, family, check, live
):
    history = Mock(side_effect=AssertionError("Invalid diagnostic options must stop before history or native setup"))
    monkeypatch.setattr(verifier, "load_history", history)
    arguments = [
        "--family",
        family,
        "--label",
        "synthetic",
        "--check",
        check,
        "--diagnostic-sidecar",
        "synthetic-sidecar.json",
    ]
    if live:
        arguments.append("--live")
    args = verifier.argument_parser().parse_args(arguments)
    with pytest.raises(ValueError, match="Cleanup diagnostics require explicit live Agy cancellation"):
        asyncio.run(verifier._verify(args))
    history.assert_not_called()


@pytest.fixture
def diagnostic_destinations(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    lineage = tmp_path / "independent-lineage"
    monkeypatch.setattr(verifier, "PROJECT", project)
    monkeypatch.setattr(verifier, "LINEAGE_ROOT", lineage)
    monkeypatch.chdir(project)
    return project, lineage


def collision_destination(kind, project, lineage):
    future = lineage / "agy/future"
    direct = {
        "receipt": future / "receipt.json",
        "preflight": future / "preflight.json",
        "submitted": future / "submitted.json",
        "other_family": lineage / "grok/future/receipt.json",
        "other_authoritative": project / "docs/verification/native-agy-admission.json",
        "lineage_root": lineage,
        "verification_root": project / "docs/verification",
        "dotdot": project / "reports/../docs/verification/future.json",
        "relative": Path("../independent-lineage/agy/future/receipt.json"),
    }
    if kind in direct:
        return direct[kind]
    if kind == "prior":
        prior = lineage / "agy/prior/receipt.json"
        prior.parent.mkdir(parents=True)
        prior.write_bytes(b"synthetic immutable prior receipt")
        return prior
    alias = project / "diagnostic-alias"
    if kind == "symlink_parent":
        alias.symlink_to(lineage, target_is_directory=True)
        return alias / "agy/future/receipt.json"
    assert kind == "final_symlink"
    alias.symlink_to(future / "receipt.json")
    return alias


@pytest.mark.parametrize(
    "kind",
    [
        "receipt",
        "preflight",
        "submitted",
        "prior",
        "other_family",
        "other_authoritative",
        "lineage_root",
        "verification_root",
        "dotdot",
        "relative",
        "symlink_parent",
        "final_symlink",
    ],
)
def test_diagnostic_destination_collision_rejects_before_history_reservation_or_dispatch(
    diagnostic_destinations, tmp_path, monkeypatch, kind
):
    project, lineage = diagnostic_destinations
    destination = collision_destination(kind, project, lineage)
    original_entries = sorted(tmp_path.rglob("*"))
    prior = lineage / "agy/prior/receipt.json"
    prior_bytes = prior.read_bytes() if prior.exists() else None
    calls = {}
    for name in (
        "load_history",
        "authoritative_history",
        "installed_binding",
        "submission_authorization",
        "evidence_directory",
        "persist",
    ):
        calls[name] = Mock(side_effect=AssertionError(f"Rejected destination reached {name}"))
        monkeypatch.setattr(verifier, name, calls[name])
    dispatch = AsyncMock(side_effect=AssertionError("Rejected destination must not dispatch"))
    monkeypatch.setattr(verifier, "run_check", dispatch)
    args = verifier.argument_parser().parse_args(
        [
            "--family",
            "agy",
            "--label",
            "future",
            "--check",
            "cancel",
            "--live",
            "--diagnostic-sidecar",
            str(destination),
        ]
    )

    with pytest.raises(ValueError, match="diagnostic|authoritative|evidence"):
        asyncio.run(verifier._verify(args))

    for call in calls.values():
        call.assert_not_called()
    dispatch.assert_not_called()
    assert sorted(tmp_path.rglob("*")) == original_entries
    assert not (lineage / "agy/future").exists()
    assert list(tmp_path.rglob("submitted.json")) == []
    if prior_bytes is not None:
        assert prior.read_bytes() == prior_bytes


@pytest.mark.parametrize("kind", ["receipt", "dotdot", "symlink_parent", "final_symlink"])
def test_direct_run_check_rejects_protected_sidecar_before_fixture_or_service_setup(
    diagnostic_destinations, tmp_path, monkeypatch, kind
):
    project, lineage = diagnostic_destinations
    destination = collision_destination(kind, project, lineage)
    original_entries = sorted(tmp_path.rglob("*"))
    calls = {}
    for name in ("generation_modules", "prepare_fixture", "candidate_service", "persist"):
        calls[name] = Mock(side_effect=AssertionError(f"Rejected destination reached {name}"))
        monkeypatch.setattr(verifier, name, calls[name])

    with pytest.raises(ValueError, match="diagnostic|authoritative|evidence"):
        asyncio.run(
            verifier.run_check(
                "agy",
                "cancel",
                "synthetic",
                project / "fixture",
                {},
                lineage / "agy/future/preflight.json",
                20,
                diagnostic_sidecar=destination,
            )
        )

    for call in calls.values():
        call.assert_not_called()
    assert sorted(tmp_path.rglob("*")) == original_entries
    assert not (lineage / "agy/future").exists()
    assert not (project / "fixture").exists()


def test_diagnostic_destination_accepts_canonical_separate_path_without_creating_it(diagnostic_destinations, tmp_path):
    project, _ = diagnostic_destinations
    original_entries = sorted(tmp_path.rglob("*"))
    destination = verifier.diagnostic_sidecar_destination(Path("reports/../diagnostics/cleanup.json"))
    assert destination == project / "diagnostics/cleanup.json"
    assert destination.is_absolute()
    assert sorted(tmp_path.rglob("*")) == original_entries
    assert not destination.exists() and not destination.parent.exists()


def test_persist_sidecar_rechecks_a_parent_alias_before_writing_authoritative_evidence(
    diagnostic_destinations, tmp_path, monkeypatch
):
    project, lineage = diagnostic_destinations
    separate = tmp_path / "separate-diagnostics"
    separate.mkdir()
    alias = project / "diagnostics"
    alias.symlink_to(separate, target_is_directory=True)
    destination = alias / "agy/future/receipt.json"
    assert verifier.diagnostic_sidecar_destination(destination) == separate / "agy/future/receipt.json"
    alias.unlink()
    alias.symlink_to(lineage, target_is_directory=True)
    diagnostics = collector()
    writer = Mock(side_effect=AssertionError("Retargeted alias must not reach the sidecar writer"))
    monkeypatch.setattr(verifier, "persist", writer)
    original_entries = sorted(tmp_path.rglob("*"))

    with pytest.raises(ValueError, match="diagnostic|authoritative|evidence"):
        diagnostics.persist_sidecar(destination, {"status": "failed"})

    writer.assert_not_called()
    assert sorted(tmp_path.rglob("*")) == original_entries
    assert not lineage.exists()


def test_diagnostic_destination_symlink_loop_has_a_fixed_redacted_error(diagnostic_destinations):
    project, _ = diagnostic_destinations
    alias = project / PRIVATE
    alias.symlink_to(alias, target_is_directory=True)
    with pytest.raises(ValueError) as caught:
        verifier.diagnostic_sidecar_destination(alias / "cleanup.json")
    assert str(caught.value) == "Cleanup diagnostics require a resolvable destination"
    assert PRIVATE not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
