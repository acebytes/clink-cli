"""Offline Grok JSON contracts; fixtures are synthetic, not native captures."""

import hashlib
import json
from pathlib import Path

import pytest

from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.grok import GrokJSONParser

_FIXTURES = Path(__file__).parent / "fixtures/clink"


def _success(**changes):
    payload = {
        "text": " Answer ",
        "stopReason": "end_turn",
        "sessionId": "PRIVATE_SESSION",
        "requestId": "PRIVATE_REQUEST",
        "cwd": "PRIVATE_CWD",
        "thought": "PRIVATE_THOUGHT",
        "num_turns": 1,
        "usage": {"input_tokens": 10, "output_tokens": 5, "reasoning_tokens": 2, "total_tokens": 15},
        "modelUsage": {"grok-4.6": {"inputTokens": 10, "outputTokens": 5}},
        "total_cost_usd": 0.1,
        "total_cost_usd_ticks": 1000000000,
    }
    payload.update(changes)
    return payload


def test_grok_success_keeps_only_documented_bounded_metadata_and_reported_model():
    parsed = GrokJSONParser().parse(
        json.dumps(_success(prompt="PRIVATE_PROMPT", command="PRIVATE_COMMAND")), "PRIVATE_STDERR"
    )
    assert parsed.content == "Answer"
    assert parsed.metadata == {
        "num_turns": 1,
        "usage": {"input_tokens": 10, "output_tokens": 5, "reasoning_tokens": 2, "total_tokens": 15},
        "actual_model": "grok-4.6",
    }
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize("stdout", ["", " ", "not JSON", "[]", "null", "true", "42", '"text"', "{}"])
def test_grok_rejects_empty_malformed_and_nonobject_json(stdout):
    with pytest.raises(ParserError) as failure:
        GrokJSONParser().parse(stdout, "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "stop_reason",
    [
        None,
        True,
        0,
        [],
        {},
        "",
        "END_TURN",
        "max_tokens",
        "max_turn_requests",
        "refusal",
        "cancelled",
        "tool_use",
        "pause_turn",
        "unknown",
    ],
)
def test_grok_requires_the_documented_complete_terminal_reason(stop_reason):
    with pytest.raises(ParserError):
        GrokJSONParser().parse(json.dumps(_success(stopReason=stop_reason)), "")


def test_grok_missing_stop_reason_never_uses_text_as_success():
    payload = _success()
    del payload["stopReason"]
    with pytest.raises(ParserError):
        GrokJSONParser().parse(json.dumps(payload), "")


@pytest.mark.parametrize("text", [None, [], {}, 42, True, "", " "])
def test_grok_never_falls_back_to_message_response_result_or_stderr(text):
    with pytest.raises(ParserError) as failure:
        GrokJSONParser().parse(
            json.dumps(_success(text=text, message="PRIVATE", response="PRIVATE", result="PRIVATE")), "PRIVATE_STDERR"
        )
    assert "PRIVATE" not in str(failure.value)


def test_grok_missing_text_is_not_a_success():
    payload = _success()
    del payload["text"]
    with pytest.raises(ParserError):
        GrokJSONParser().parse(json.dumps(payload), "")


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "error", "message": "PRIVATE_ERROR"},
        {"type": "failed"},
        {"type": "result"},
        {"type": "end"},
        {"type": []},
        {"event": "result"},
        {"status": "SUCCESS"},
        {"status": "failed"},
        {"status": []},
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
    ],
)
def test_grok_rejects_error_plus_text_and_conflicting_protocols(changes):
    with pytest.raises(ParserError) as failure:
        GrokJSONParser().parse(json.dumps(_success(**changes)), "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Not logged in. PRIVATE_SECRET", "authentication_required"),
        ("Invalid API key PRIVATE_SECRET", "authentication_required"),
        ("rate_limit_exceeded PRIVATE_SECRET", "rate_limited"),
        ("HTTP 429 PRIVATE_SECRET", "rate_limited"),
        ("PRIVATE_SECRET", "parse_error"),
    ],
)
def test_grok_failure_classification_is_sanitized(message, code):
    with pytest.raises(ParserError) as failure:
        GrokJSONParser().parse(json.dumps({"type": "error", "message": message}), "PRIVATE_STDERR")
    assert failure.value.code == code
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize("value", [True, -1, MAX_METADATA_NUMBER + 1, "PRIVATE_TOKEN", [], None, 1.5])
def test_grok_filters_invalid_token_and_turn_counts(value):
    parsed = GrokJSONParser().parse(
        json.dumps(_success(num_turns=value, usage={"input_tokens": value, "output_tokens": 0, "secret": "PRIVATE"})),
        "PRIVATE_STDERR",
    )
    assert parsed.metadata == {"usage": {"output_tokens": 0}, "actual_model": "grok-4.6"}


def test_grok_keeps_zero_and_maximum_bounded_telemetry():
    usage = {"input_tokens": MAX_METADATA_NUMBER, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    parsed = GrokJSONParser().parse(json.dumps(_success(num_turns=0, usage=usage)), "")
    assert parsed.metadata == {"num_turns": 0, "usage": usage, "actual_model": "grok-4.6"}


@pytest.mark.parametrize(
    "model_usage",
    [None, [], {}, {"one": {}, "two": {}}, {"unknown": {}}, {"PRIVATE MODEL": {}}, {"x" * 129: {}}, {"model": []}],
)
def test_grok_omits_invalid_unknown_or_ambiguous_reported_model(model_usage):
    parsed = GrokJSONParser().parse(json.dumps(_success(modelUsage=model_usage, model="PRIVATE_REQUESTED_MODEL")), "")
    assert "actual_model" not in parsed.metadata
    assert "model_usage" not in parsed.metadata


@pytest.mark.parametrize(
    "stdout",
    [
        '{"stopReason":"end_turn","stopReason":"cancelled","text":"PRIVATE"}',
        '{"stopReason":"end_turn","text":"PRIVATE","usage":{"input_tokens":1,"input_tokens":2}}',
        '{"stopReason":"end_turn","text":"PRIVATE","usage":{"input_tokens":NaN}}',
        '{"stopReason":"end_turn","text":"PRIVATE","num_turns":Infinity}',
        '{"stopReason":"end_turn","text":"PRIVATE"}\n{"type":"error"}',
        '{"type":"end","stopReason":"end_turn","text":"PRIVATE"}',
        '{"type":"result","subtype":"success","is_error":false,"result":"PRIVATE","stop_reason":"end_turn"}',
        '[{"stopReason":"end_turn","text":"PRIVATE"}]',
    ],
)
def test_grok_rejects_duplicate_nonfinite_streamed_and_ambiguous_json(stdout):
    with pytest.raises(ParserError) as failure:
        GrokJSONParser().parse(stdout, "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)
    assert failure.value.__context__ is None
    assert failure.value.__cause__ is None


def test_grok_empty_response_has_explicit_error_code():
    with pytest.raises(ParserError) as failure:
        GrokJSONParser().parse(json.dumps(_success(text="")), "")
    assert failure.value.code == "empty_response"


def test_grok_does_not_classify_successful_response_prose_as_an_error():
    parsed = GrokJSONParser().parse(json.dumps(_success(text="The error was: invalid API key.")), "HTTP 429")
    assert parsed.content == "The error was: invalid API key."


def test_grok_documented_synthetic_fixtures_have_explicit_provenance():
    provenance = json.loads((_FIXTURES / "grok-1.0.70-json.synthetic.provenance.json").read_text())
    assert provenance["fixture_kind"] == "synthetic_documented_shape_not_captured_native_output"
    assert provenance["native_output_captured"] is False
    assert provenance["live_execution_verified"] is False
    for fixture, digest in provenance["fixture_sha256"].items():
        assert hashlib.sha256((_FIXTURES / fixture).read_bytes()).hexdigest() == digest
    parsed = GrokJSONParser().parse((_FIXTURES / "grok-1.0.70-json-success.synthetic.json").read_text(), "")
    assert parsed.content == "Synthetic advisory result."
    assert parsed.metadata["actual_model"] == "grok-4.6"
    with pytest.raises(ParserError):
        GrokJSONParser().parse((_FIXTURES / "grok-1.0.70-json-error.synthetic.json").read_text(), "")
