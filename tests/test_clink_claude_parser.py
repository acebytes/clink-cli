"""Tests for successful-terminal Claude parsing and metadata redaction."""

import json

import pytest

from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.claude import ClaudeJSONParser


def _success(**changes):
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1234,
        "duration_api_ms": 1200,
        "num_turns": 1,
        "result": "42",
        "session_id": "PRIVATE_SESSION",
        "uuid": "PRIVATE_UUID",
        "total_cost_usd": 0.12,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "modelUsage": {"claude-sonnet-4-5-20250929": {"inputTokens": 10, "outputTokens": 5}},
    }
    payload.update(changes)
    return payload


def test_claude_parser_extracts_result_and_allowlisted_metadata():
    parsed = ClaudeJSONParser().parse(json.dumps(_success()), stderr="PRIVATE_STDERR")
    assert parsed.content == "42"
    assert parsed.metadata == {
        "actual_model": "claude-sonnet-4-5-20250929",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "duration_ms": 1234,
        "duration_api_ms": 1200,
        "num_turns": 1,
    }
    assert "PRIVATE" not in json.dumps(parsed.metadata)


def test_claude_parser_handles_streamed_event_array_with_final_result():
    events = [
        {"type": "system", "session_id": "PRIVATE_SESSION"},
        {"type": "assistant", "message": {"content": "PRIVATE_INTERMEDIATE"}},
        _success(result=" Final answer "),
    ]
    parsed = ClaudeJSONParser().parse(json.dumps(events), stderr="warning")
    assert parsed.content == "Final answer"
    assert "PRIVATE" not in json.dumps(parsed.metadata)
    assert "raw_events" not in parsed.metadata


@pytest.mark.parametrize(
    "stdout", ["", " ", "not JSON", "null", "42", '"text"', "[]", "[{} , null]", "[{} , 1]", "[[]]", "{}"]
)
def test_claude_parser_rejects_empty_malformed_or_invalid_root_shapes(stdout):
    with pytest.raises(ParserError):
        ClaudeJSONParser().parse(stdout, "PRIVATE_STDERR")


@pytest.mark.parametrize(
    "changes",
    [
        {"is_error": True, "result": "PRIVATE_ERROR"},
        {"is_error": "false"},
        {"is_error": None},
        {"subtype": "error_max_turns", "result": "PRIVATE_ERROR"},
        {"subtype": "error_during_execution", "result": "PRIVATE_ERROR"},
        {"status": "failed"},
        {"status": []},
        {"status": "in_progress"},
        {"subtype": None},
        {"subtype": []},
        {"subtype": "other"},
        {"type": "assistant"},
        {"type": []},
        {"error": {"message": "PRIVATE_ERROR"}},
        {"errors": ["PRIVATE_ERROR"]},
        {"result": []},
        {"result": ["42"]},
        {"result": {}},
        {"result": None},
        {"result": " "},
    ],
)
def test_claude_parser_never_returns_error_or_malformed_results_as_success(changes):
    with pytest.raises(ParserError) as failure:
        ClaudeJSONParser().parse(json.dumps(_success(**changes)), "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "assistant", "message": "Fallback answer"}],
        [{"type": "error", "message": "PRIVATE_ERROR"}, _success()],
        [{"type": "assistant", "status": "failed"}, _success()],
        [_success(), {"type": "assistant", "message": "trailing"}],
        [_success(), _success()],
        [{"type": "result", "subtype": "error_max_turns"}, _success()],
    ],
)
def test_claude_requires_final_success_and_rejects_errors_anywhere_in_event_array(events):
    with pytest.raises(ParserError):
        ClaudeJSONParser().parse(json.dumps(events), "")


@pytest.mark.parametrize(
    "block",
    [
        {"type": "tool_result", "is_error": True, "content": "PRIVATE_ERROR"},
        {"type": "tool_result", "is_error": "false", "content": "PRIVATE_ERROR"},
        {"type": "tool_result", "subtype": "error", "content": "PRIVATE_ERROR"},
        {"type": "error", "message": "PRIVATE_ERROR"},
    ],
)
def test_claude_rejects_structured_tool_errors_even_if_terminal_result_reports_success(block):
    events = [{"type": "user", "message": {"role": "user", "content": [block]}}, _success()]
    with pytest.raises(ParserError) as failure:
        ClaudeJSONParser().parse(json.dumps(events), "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


def test_claude_accepts_successful_tool_result_without_returning_tool_output():
    events = [
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "PRIVATE_TOOL_BODY"}]}},
        _success(),
    ]
    parsed = ClaudeJSONParser().parse(json.dumps(events), "")
    assert parsed.content == "42"
    assert "PRIVATE" not in json.dumps(parsed.metadata)


def test_claude_does_not_fall_back_to_message_or_stderr():
    payload = _success()
    del payload["result"]
    payload["message"] = "PRIVATE_FALLBACK"
    with pytest.raises(ParserError) as failure:
        ClaudeJSONParser().parse(json.dumps(payload), "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "model_usage", [{"PRIVATE MODEL": {}}, {"x" * 129: {}}, {"one": {}, "two": {}}, {"model": []}, [], None]
)
def test_claude_drops_invalid_or_ambiguous_model_usage(model_usage):
    parsed = ClaudeJSONParser().parse(json.dumps(_success(modelUsage=model_usage)), "")
    assert "actual_model" not in parsed.metadata
    assert "model_usage" not in parsed.metadata


def test_claude_filters_usage_and_duration_metadata_to_bounded_numbers():
    payload = _success(
        duration_ms=True,
        duration_api_ms=-1,
        num_turns=MAX_METADATA_NUMBER + 1,
        usage={
            "input_tokens": True,
            "output_tokens": 0,
            "cache_read_input_tokens": MAX_METADATA_NUMBER,
            "secret": "PRIVATE",
        },
        permission_denials=[{"PRIVATE": "SECRET"}],
        modelUsage={"claude-test": {"PRIVATE": "SECRET"}},
    )
    parsed = ClaudeJSONParser().parse(json.dumps(payload), "PRIVATE_STDERR")
    assert parsed.metadata == {
        "usage": {"output_tokens": 0, "cache_read_input_tokens": MAX_METADATA_NUMBER},
        "actual_model": "claude-test",
    }


def test_claude_authentication_error_is_classified_without_raw_body():
    payload = _success(is_error=True, result="Invalid API key PRIVATE_SECRET")
    with pytest.raises(ParserError) as failure:
        ClaudeJSONParser().parse(json.dumps(payload), "PRIVATE_STDERR")
    assert failure.value.code == "authentication_required"
    assert str(failure.value) == "CLI authentication is required"


def test_claude_empty_result_is_not_a_success():
    with pytest.raises(ParserError) as failure:
        ClaudeJSONParser().parse(json.dumps(_success(result="")), "")
    assert failure.value.code == "empty_response"
