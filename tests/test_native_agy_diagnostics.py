"""Keep verifier-only Agy parser failure receipts fixed and free of native text."""

import json
from copy import deepcopy

import pytest

from clink.parsers.base import ParserError
from clink.runner import ProcessOutput
from scripts import verify_native_adapters as verifier

PRIVATE = "PRIVATE_NATIVE_CONTENT_SESSION_ARG_ENV"


def stream(result=None):
    events = [{"event": "init", "conversation_id": PRIVATE, "init": {"model": PRIVATE}}]
    if result is not None:
        events.append({"event": "result", "result": result})
    return "\n".join(json.dumps(event) for event in events)


def result(**changes):
    return {"conversation_id": PRIVATE, "status": "SUCCESS", "response": PRIVATE, **changes}


@pytest.mark.parametrize(
    "stdout,stderr,code",
    [
        ("", "", "empty_or_event_limit"),
        (PRIVATE, "", "malformed_json"),
        (json.dumps({"event": PRIVATE}), "", "unsupported_stream_dialect"),
        (stream(), "", "terminal_result_missing"),
        (stream(), "authentication required " + PRIVATE, "authentication_required"),
        (stream(), "rate limit exceeded " + PRIVATE, "rate_limited"),
        (stream(result(status=PRIVATE)), "", "invalid_terminal_status"),
        (stream(result(finished_reason=PRIVATE)), "", "invalid_finish_reason"),
        (stream(result(error={"private": PRIVATE})), "", "terminal_error_not_text"),
        (stream(result(response=None)), "", "response_not_text"),
        (stream(result(denied_actions=[{"action": PRIVATE}])), "", "invalid_denied_action"),
        (stream(result(usage=PRIVATE)), "", "invalid_usage"),
    ],
)
def test_known_native_parser_failures_use_only_fixed_codes_and_shape(stdout, stderr, code):
    diagnostic = verifier.agy_parser_diagnostic(stdout, stderr)
    assert diagnostic["native_parser_failure_code"] == code
    assert verifier.valid_agy_parser_diagnostic(diagnostic)
    assert PRIVATE not in json.dumps(diagnostic)


def test_missing_response_is_distinct_from_null_or_text_without_retaining_error():
    terminal = result(status="ERROR", error=PRIVATE, finished_reason="error")
    del terminal["response"]
    diagnostic = verifier.agy_parser_diagnostic(stream(terminal), PRIVATE)
    shape = diagnostic["native_parser_shape"]
    assert diagnostic["native_parser_failure_code"] == "response_not_text"
    assert shape["result_seen"] is shape["result_payload_object"] is True
    assert shape["response_field_present"] is shape["response_textual"] is False
    assert shape["error_field_present"] is shape["error_textual"] is True
    assert shape["terminal_status"] == "ERROR"
    assert shape["finished_reason"] == "error"
    assert PRIVATE not in json.dumps(diagnostic)
    null_shape = verifier.agy_parser_diagnostic(stream(result(response=None)), "")["native_parser_shape"]
    assert null_shape["response_field_present"] is True
    assert null_shape["response_textual"] is False


def test_source_known_unknown_terminal_status_remains_a_fixed_diagnostic_enum():
    diagnostic = verifier.agy_parser_diagnostic(stream(result(status="UNKNOWN", response=None)), "")
    assert diagnostic["native_parser_failure_code"] == "response_not_text"
    assert diagnostic["native_parser_shape"]["terminal_status"] == "UNKNOWN"
    assert verifier.valid_agy_parser_diagnostic(diagnostic)


def test_successful_native_output_adds_no_failure_diagnostic():
    assert verifier.agy_parser_diagnostic(stream(result()), PRIVATE) == {}


def test_over_limit_shape_never_claims_uninspected_json_lines_are_valid():
    stdout = (json.dumps({"event": PRIVATE}) + "\n") * (verifier.AGY_MAX_EVENTS + 1)
    diagnostic = verifier.agy_parser_diagnostic(stdout, "")
    assert diagnostic["native_parser_failure_code"] == "empty_or_event_limit"
    assert diagnostic["native_parser_shape"]["event_limit_exceeded"] is True
    assert diagnostic["native_parser_shape"]["json_lines_valid"] is False
    assert PRIVATE not in json.dumps(diagnostic)


def test_unknown_future_exception_text_is_never_retained(monkeypatch):
    class UnknownFailure:
        def parse(self, stdout, stderr):
            raise ParserError(PRIVATE)

    monkeypatch.setattr(verifier, "AgyNativeParser", UnknownFailure)
    diagnostic = verifier.agy_parser_diagnostic(stream(result(status=PRIVATE, finished_reason=PRIVATE)), PRIVATE)
    assert diagnostic["native_parser_failure_code"] == "unknown_parser_error"
    assert diagnostic["native_parser_shape"]["terminal_status"] == "invalid"
    assert diagnostic["native_parser_shape"]["finished_reason"] == "invalid"
    assert PRIVATE not in json.dumps(diagnostic)


def prior_receipt(tmp_path, monkeypatch, entry, *, adapter="agy"):
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    directory = tmp_path / "docs" / "verification"
    directory.mkdir(parents=True)
    path = directory / "prior.json"
    path.write_text(
        json.dumps(
            {
                "scope": verifier.SCOPE,
                "adapter": adapter,
                "submission_ledger": [{"ordinal": 1, "check": "edit", "reason": "exited", **entry}],
                "submission_allowance_total": 5,
                "submissions_consumed_total": 1,
            }
        )
    )
    return path


def test_strict_diagnostic_survives_lineage_without_reset_or_rewriting(tmp_path, monkeypatch):
    diagnostic = verifier.agy_parser_diagnostic(stream(result(response=None)), "")
    path = prior_receipt(tmp_path, monkeypatch, diagnostic)
    before = path.read_bytes()
    ledger, allowance, previous_hash = verifier.load_history(path, "agy")
    assert ledger == [{"ordinal": 1, "check": "edit", "reason": "exited", **diagnostic}]
    assert allowance == 5
    assert previous_hash == verifier.digest(before)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"native_parser_failure_code": PRIVATE},
        {"native_parser_failure_code": []},
        {"native_parser_shape": None},
        {"native_parser_shape": {"response_field_present": True}},
        {"shape_extra": {"raw_output": PRIVATE}},
        {"shape_update": {"terminal_status": PRIVATE}},
        {"shape_update": {"finished_reason": PRIVATE}},
        {"shape_update": {"response_textual": 1}},
        {"shape_update": {"session_textual": PRIVATE}},
        {"remove": "native_parser_failure_code"},
        {"remove": "native_parser_shape"},
    ],
)
def test_history_rejects_private_unknown_or_incomplete_parser_diagnostics(tmp_path, monkeypatch, change):
    diagnostic = deepcopy(verifier.agy_parser_diagnostic(stream(result(response=None)), ""))
    for key, value in change.items():
        if key == "remove":
            del diagnostic[value]
        elif key in {"shape_extra", "shape_update"}:
            diagnostic["native_parser_shape"].update(value)
        else:
            diagnostic[key] = value
    path = prior_receipt(tmp_path, monkeypatch, diagnostic)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="technical native parser diagnostic") as caught:
        verifier.load_history(path, "agy")
    assert PRIVATE not in str(caught.value)
    assert path.read_bytes() == before


def test_agy_diagnostic_is_not_admitted_to_another_adapter_history(tmp_path, monkeypatch):
    diagnostic = verifier.agy_parser_diagnostic(stream(result(response=None)), "")
    path = prior_receipt(tmp_path, monkeypatch, diagnostic, adapter="grok")
    with pytest.raises(ValueError, match="technical native parser diagnostic"):
        verifier.load_history(path, "grok")


@pytest.mark.asyncio
async def test_pool_records_failure_once_and_returns_the_original_streams(tmp_path, monkeypatch):
    stdout = stream(result(response=None, error=PRIVATE))
    output = ProcessOutput(stdout, PRIVATE, 0, len(stdout), len(PRIVATE), 0)
    parse_calls = []
    original_parser = verifier.AgyNativeParser

    class CountingParser:
        def parse(self, stdout, stderr):
            parse_calls.append(True)
            return original_parser().parse(stdout, stderr)

    async def execute(*args, **kwargs):
        kwargs["on_started"]()
        return output

    monkeypatch.setattr(verifier, "AgyNativeParser", CountingParser)
    monkeypatch.setattr(verifier.ProcessPool, "execute", execute)
    evidence = {"adapter": "agy", "submission_ledger": [], "submission_allowance_total": 5}
    path = tmp_path / "technical-receipt.json"
    pool = verifier.VerificationPool(evidence, path)
    assert await pool.execute(("synthetic-not-executed",)) is output
    assert len(parse_calls) == 1
    entry = evidence["submission_ledger"][0]
    assert entry["native_parser_failure_code"] == "response_not_text"
    assert entry["reason"] == "exited"
    assert entry["spawn_observed"] is True
    assert evidence["submissions_consumed_total"] == 1
    assert PRIVATE not in path.read_text()
