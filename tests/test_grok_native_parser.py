"""Synthetic installed-document Grok native contracts; no CLI execution."""

import json

import pytest

from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.grok_native import GrokNativeParser, validate_session_id

_SESSION = "11111111-2222-4333-8444-555555555555"


def terminal(**changes):
    return {"text": "done", "stopReason": "end_turn", "sessionId": _SESSION, **changes}


def test_native_success_keeps_private_identity_outside_allowlisted_metadata():
    result = GrokNativeParser().parse(
        json.dumps(
            terminal(
                cwd="PRIVATE_PATH",
                requestId="PRIVATE_REQUEST",
                usage={"input_tokens": 3, "secret": "PRIVATE_USAGE"},
                modelUsage={"grok-model": {"inputTokens": 3}},
                num_turns=2,
                command="PRIVATE_COMMAND",
            )
        ),
        "PRIVATE_STDERR",
    )
    assert result.state == "completed"
    assert result.session_id == _SESSION
    assert result.content == "done"
    assert result.metadata == {
        "permission_denial_count": 0,
        "truncated": False,
        "terminal_stop_reason": "end_turn",
        "usage": {"input_tokens": 3},
        "num_turns": 2,
        "actual_model": "grok-model",
    }
    assert "PRIVATE" not in json.dumps(result.metadata)
    assert _SESSION not in json.dumps(result.metadata)


@pytest.mark.parametrize("tool", ["Edit calc.py", 'Edit REVIEW.md (workflow "review")'])
def test_exact_headless_denial_overrides_end_turn_and_hides_diagnostic(tool):
    diagnostic = f"permission required: {tool} was not run, as a headless run cannot ask for approval"
    result = GrokNativeParser().parse(json.dumps(terminal()), diagnostic)
    assert result.state == "permission_needed"
    assert result.metadata["permission_denial_count"] == 1
    assert tool not in json.dumps(result.metadata)


def test_exact_headless_denial_without_stdout_still_reports_permission_needed():
    result = GrokNativeParser().parse(
        "", "permission required: Edit PRIVATE.py was not run, as a headless run cannot ask for approval"
    )
    assert result.state == "permission_needed"
    assert result.session_id is None
    assert "PRIVATE" not in json.dumps(result.metadata)


@pytest.mark.parametrize(
    "diagnostic",
    [
        "permission denied",
        "approval required",
        "A tool failed but recovered",
        '"permission required: Edit calc.py was not run, as a headless run cannot ask for approval"',
        "permission required: Edit calc.py",
    ],
)
def test_prose_and_unrecognized_diagnostics_do_not_guess_permission_state(diagnostic):
    result = GrokNativeParser().parse(json.dumps(terminal(text="permission required")), diagnostic)
    assert result.state == "completed"


@pytest.mark.parametrize("reason", ["max_tokens", "max_turn_requests", "refusal", "cancelled"])
def test_noncompletion_reasons_report_failed_and_never_permission_needed(reason):
    result = GrokNativeParser().parse(json.dumps(terminal(stopReason=reason)))
    assert result.state == "failed"
    assert result.metadata["error_code"] == "native_error"


def test_error_document_without_session_reports_failed_and_redacts_error():
    result = GrokNativeParser().parse(json.dumps({"type": "error", "message": "PRIVATE_ERROR"}))
    assert result.state == "failed"
    assert result.session_id is None
    assert result.content == ""
    assert "PRIVATE" not in json.dumps(result.metadata)


@pytest.mark.parametrize("changes", [{"error": "PRIVATE_ERROR"}, {"errors": ["PRIVATE_ERROR"]}, {"is_error": True}])
def test_end_turn_cannot_override_explicit_error_markers(changes):
    result = GrokNativeParser().parse(json.dumps(terminal(**changes)))
    assert result.state == "failed"
    assert "PRIVATE" not in json.dumps(result.metadata)


@pytest.mark.parametrize("output", ["", "PRIVATE_INVALID", "[]", "{}", "null", '{"text":"a","text":"b"}'])
def test_invalid_json_is_rejected_without_echoing(output):
    with pytest.raises(ParserError) as failure:
        GrokNativeParser().parse(output, "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "result"},
        {"event": "result"},
        {"status": "success"},
        {"stopReason": None},
        {"stopReason": True},
        {"stopReason": "unknown"},
        {"sessionId": None},
        {"sessionId": "--unsafe"},
        {"sessionId": "abc123"},
        {"sessionId": []},
        {"text": None},
        {"is_error": "false"},
        {"errors": "PRIVATE"},
    ],
)
def test_invalid_terminal_fields_and_dialects_are_rejected(changes):
    with pytest.raises(ParserError):
        GrokNativeParser().parse(json.dumps(terminal(**changes)))


def test_multiple_documents_or_trailing_stream_events_are_rejected():
    with pytest.raises(ParserError):
        GrokNativeParser().parse(json.dumps(terminal()) + "\n" + json.dumps(terminal()))


def test_result_truncation_and_numeric_metadata_are_bounded():
    result = GrokNativeParser().parse(
        json.dumps(
            terminal(
                text="x" * 20001,
                num_turns=-1,
                usage={"input_tokens": True, "output_tokens": MAX_METADATA_NUMBER + 1, "reasoning_tokens": 2},
            )
        )
    )
    assert len(result.content) == 20000
    assert result.metadata["truncated"] is True
    assert "num_turns" not in result.metadata
    assert result.metadata["usage"] == {"reasoning_tokens": 2}


def test_session_validator_allows_only_uuid_or_missing_identity():
    assert validate_session_id(_SESSION) == _SESSION
    assert validate_session_id(None) is None
    with pytest.raises(ParserError):
        validate_session_id("--unsafe")
