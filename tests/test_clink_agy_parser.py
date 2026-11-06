"""Offline Antigravity JSON contracts; fixtures are synthetic, not native captures."""

import hashlib
import json
from pathlib import Path

import pytest

from clink.parsers.agy import AgyJSONParser
from clink.parsers.base import MAX_METADATA_NUMBER, ParserError

_FIXTURES = Path(__file__).parent / "fixtures/clink"


def _success(**changes):
    payload = {
        "status": "SUCCESS",
        "response": " Answer ",
        "conversation_id": "PRIVATE_CONVERSATION",
        "duration_seconds": 1.25,
        "num_turns": 1,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "thinking_tokens": 2,
            "cache_read_tokens": 0,
            "total_tokens": 15,
        },
    }
    payload.update(changes)
    return payload


def test_agy_success_keeps_only_documented_bounded_metadata():
    payload = _success(
        model="PRIVATE_REQUESTED_MODEL",
        command="PRIVATE_COMMAND",
        prompt="PRIVATE_PROMPT",
        events=[{"body": "PRIVATE_EVENT"}],
    )
    parsed = AgyJSONParser().parse(json.dumps(payload), "PRIVATE_STDERR")
    assert parsed.content == "Answer"
    assert parsed.metadata == {
        "duration_seconds": 1.25,
        "num_turns": 1,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "thinking_tokens": 2,
            "cache_read_tokens": 0,
            "total_tokens": 15,
        },
    }
    assert "PRIVATE" not in json.dumps(parsed.metadata)
    assert "actual_model" not in parsed.metadata


@pytest.mark.parametrize("stdout", ["", " ", "not JSON", "[]", "null", "true", "42", '"text"', "{}"])
def test_agy_rejects_empty_malformed_and_nonobject_json(stdout):
    with pytest.raises(ParserError) as failure:
        AgyJSONParser().parse(stdout, "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "status",
    [None, True, 0, [], {}, "success", "ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING", "RUNNING", ""],
)
def test_agy_requires_literal_terminal_success_status(status):
    with pytest.raises(ParserError):
        AgyJSONParser().parse(json.dumps(_success(status=status)), "")


def test_agy_missing_status_never_uses_response_as_success():
    payload = _success()
    del payload["status"]
    with pytest.raises(ParserError):
        AgyJSONParser().parse(json.dumps(payload), "")


def test_agy_treats_null_optional_error_as_absent():
    parsed = AgyJSONParser().parse(json.dumps(_success(error=None)), "")
    assert parsed.content == "Answer"


@pytest.mark.parametrize("response", [None, [], {}, 42, True, "", " "])
def test_agy_never_falls_back_to_message_result_or_stderr(response):
    with pytest.raises(ParserError) as failure:
        AgyJSONParser().parse(
            json.dumps(_success(response=response, message="PRIVATE_FALLBACK", result="PRIVATE_FALLBACK")),
            "PRIVATE_STDERR",
        )
    assert "PRIVATE" not in str(failure.value)


def test_agy_missing_response_is_not_a_success():
    payload = _success()
    del payload["response"]
    with pytest.raises(ParserError):
        AgyJSONParser().parse(json.dumps(payload), "")


@pytest.mark.parametrize(
    "changes",
    [
        {"error": {"message": "PRIVATE_ERROR"}},
        {"error": "PRIVATE_ERROR"},
        {"error": []},
        {"error": {}},
        {"error": False},
        {"error": ""},
        {"errors": ["PRIVATE_ERROR"]},
        {"errors": {}},
        {"errors": False},
        {"errors": ""},
        {"is_error": True},
        {"is_error": "false"},
        {"is_error": 0},
        {"is_error": None},
        {"type": "error", "message": "PRIVATE_ERROR"},
        {"type": "result"},
        {"type": []},
        {"event": "result"},
    ],
)
def test_agy_rejects_conflicting_failure_signals_and_other_dialects(changes):
    with pytest.raises(ParserError) as failure:
        AgyJSONParser().parse(json.dumps(_success(**changes)), "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    ("changes", "stderr", "code"),
    [
        ({"status": "ERROR", "error": "Not logged in. PRIVATE_SECRET"}, "", "authentication_required"),
        ({"status": "ERROR", "error": {"code": 401}}, "", "authentication_required"),
        ({"status": "ERROR", "error": "rate_limit_exceeded PRIVATE_SECRET"}, "", "rate_limited"),
        ({"status": "ERROR"}, "HTTP 429 PRIVATE_SECRET", "rate_limited"),
        ({"status": "ERROR"}, "PRIVATE_SECRET", "parse_error"),
    ],
)
def test_agy_failure_classification_is_sanitized(changes, stderr, code):
    with pytest.raises(ParserError) as failure:
        AgyJSONParser().parse(json.dumps(_success(**changes)), stderr)
    assert failure.value.code == code
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize("value", [True, -1, MAX_METADATA_NUMBER + 1, "PRIVATE_TOKEN", [], None, 1.5])
def test_agy_filters_invalid_token_and_turn_counts(value):
    parsed = AgyJSONParser().parse(
        json.dumps(_success(num_turns=value, usage={"input_tokens": value, "output_tokens": 0, "secret": "PRIVATE"})),
        "PRIVATE_STDERR",
    )
    assert parsed.metadata == {"duration_seconds": 1.25, "usage": {"output_tokens": 0}}


@pytest.mark.parametrize("value", [True, -1, MAX_METADATA_NUMBER + 1, "PRIVATE_DURATION", [], None])
def test_agy_drops_invalid_or_unbounded_duration(value):
    parsed = AgyJSONParser().parse(json.dumps(_success(duration_seconds=value, usage=None)), "")
    assert parsed.metadata == {"num_turns": 1}


def test_agy_keeps_zero_and_maximum_bounded_telemetry():
    parsed = AgyJSONParser().parse(
        json.dumps(_success(duration_seconds=0, num_turns=0, usage={"input_tokens": MAX_METADATA_NUMBER})), ""
    )
    assert parsed.metadata == {"duration_seconds": 0, "num_turns": 0, "usage": {"input_tokens": MAX_METADATA_NUMBER}}


@pytest.mark.parametrize(
    "stdout",
    [
        '{"status":"SUCCESS","status":"ERROR","response":"PRIVATE"}',
        '{"status":"SUCCESS","response":"PRIVATE","usage":{"input_tokens":1,"input_tokens":2}}',
        '{"status":"SUCCESS","response":"PRIVATE","duration_seconds":NaN}',
        '{"status":"SUCCESS","response":"PRIVATE","duration_seconds":Infinity}',
        '{"status":"SUCCESS","response":"PRIVATE"}\n{"status":"ERROR"}',
        '{"event":"result","result":{"status":"SUCCESS","response":"PRIVATE"}}',
        '[{"status":"SUCCESS","response":"PRIVATE"}]',
    ],
)
def test_agy_rejects_duplicate_nonfinite_streamed_and_ambiguous_json(stdout):
    with pytest.raises(ParserError) as failure:
        AgyJSONParser().parse(stdout, "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)
    assert failure.value.__context__ is None
    assert failure.value.__cause__ is None


def test_agy_empty_response_has_explicit_error_code():
    with pytest.raises(ParserError) as failure:
        AgyJSONParser().parse(json.dumps(_success(response="")), "")
    assert failure.value.code == "empty_response"


def test_agy_does_not_classify_successful_response_prose_as_an_error():
    parsed = AgyJSONParser().parse(json.dumps(_success(response="The error was: invalid API key.")), "HTTP 429")
    assert parsed.content == "The error was: invalid API key."


def test_agy_documented_synthetic_fixtures_have_explicit_provenance():
    provenance = json.loads((_FIXTURES / "agy-1.4.12-json.synthetic.provenance.json").read_text())
    assert provenance["fixture_kind"] == "synthetic_documented_shape_not_captured_native_output"
    assert provenance["native_output_captured"] is False
    assert provenance["live_execution_verified"] is False
    for fixture, digest in provenance["fixture_sha256"].items():
        assert hashlib.sha256((_FIXTURES / fixture).read_bytes()).hexdigest() == digest
    parsed = AgyJSONParser().parse((_FIXTURES / "agy-1.4.12-json-success.synthetic.json").read_text(), "")
    assert parsed.content == "Synthetic advisory result."
    with pytest.raises(ParserError):
        AgyJSONParser().parse((_FIXTURES / "agy-1.4.12-json-error.synthetic.json").read_text(), "")
