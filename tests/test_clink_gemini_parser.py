"""Tests for shape-safe Gemini parsing while adapter execution is unavailable."""

import json

import pytest

from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.gemini import GeminiJSONParser


def _success(**changes):
    payload = {
        "response": " Answer ",
        "session_id": "PRIVATE_SESSION",
        "stats": {
            "models": {
                "gemini-2.5-pro": {
                    "api": {"totalRequests": 1, "totalErrors": 0, "totalLatencyMs": 1234},
                    "tokens": {"prompt": 10, "candidates": 5, "total": 15},
                },
            },
            "files": {"PRIVATE": "BODY"},
        },
    }
    payload.update(changes)
    return payload


def test_gemini_success_contains_response_and_only_safe_metadata():
    parsed = GeminiJSONParser().parse(json.dumps(_success()), "PRIVATE_STDERR")
    assert parsed.content == "Answer"
    assert parsed.metadata == {
        "actual_model": "gemini-2.5-pro",
        "latency_ms": 1234,
        "usage": {"prompt": 10, "candidates": 5, "total": 15},
    }
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize("stdout", ["", " ", "not JSON", "[]", "null", "true", "42", '"text"'])
def test_gemini_rejects_empty_malformed_and_nonobject_json(stdout):
    with pytest.raises(ParserError):
        GeminiJSONParser().parse(stdout, "PRIVATE_STDERR")


@pytest.mark.parametrize("response", [None, [], {}, 42, True, "", " "])
def test_gemini_never_falls_back_to_message_or_stderr(response):
    with pytest.raises(ParserError) as failure:
        GeminiJSONParser().parse(json.dumps(_success(response=response, message="PRIVATE_FALLBACK")), "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"error": {"type": "ApiError", "message": "PRIVATE_ERROR"}},
        {"error": "PRIVATE_ERROR"},
        {"error": []},
        {"errors": ["PRIVATE_ERROR"]},
        {"is_error": True},
        {"is_error": "false"},
        {"type": "error", "message": "PRIVATE_ERROR"},
        {"type": []},
        {"status": "failed"},
        {"status": []},
        {"status": "in_progress"},
        {"stats": []},
        {"stats": {"models": []}},
        {"stats": {"models": {"model": []}}},
        {"stats": {"models": {"model": {"api": []}}}},
        {"stats": {"models": {"model": {"api": {"totalErrors": "0"}}}}},
        {"stats": {"models": {"model": {"api": {"totalErrors": True}}}}},
        {"stats": {"models": {"model": {"api": {"totalErrors": -1}}}}},
        {"stats": {"models": {"model": {"api": {"totalErrors": 1}}}}},
        {"stats": {"tools": {"totalFail": 1}}},
        {"stats": {"tools": {"byName": {"read": {"fail": 1}}}}},
        {"stats": {"error": {"message": "PRIVATE_ERROR"}}},
        {"stats": {"tools": {"totalFail": []}}},
        {"stats": {"models": {"model": {"api": {"totalErrors": 0, "status": "failed"}}}}},
        {"stats": {"models": {"model": {"api": {"status": []}}}}},
    ],
)
def test_gemini_rejects_error_records_and_invalid_stats_shapes_even_with_text(changes):
    with pytest.raises(ParserError):
        GeminiJSONParser().parse(json.dumps(_success(**changes)), "PRIVATE_STDERR")


def test_gemini_parser_classifies_rate_limited_empty_response_without_recovering_success():
    payload = _success(response="", stats={"models": {"gemini-2.5-pro": {"api": {"totalErrors": 5}}}})
    stderr = "Attempt 1 failed with status 429. Retrying with backoff... PRIVATE_SECRET"
    with pytest.raises(ParserError) as failure:
        GeminiJSONParser().parse(json.dumps(payload), stderr)
    assert failure.value.code == "rate_limited"
    assert str(failure.value) == "CLI rate limit was reached"


def test_gemini_checks_errors_in_all_models():
    payload = _success(stats={"models": {"first": {"api": {"totalErrors": 0}}, "second": {"api": {"totalErrors": 1}}}})
    with pytest.raises(ParserError):
        GeminiJSONParser().parse(json.dumps(payload), "")


def test_gemini_omits_ambiguous_model_and_per_model_usage():
    payload = _success(stats={"models": {"first": {}, "second": {}}})
    assert GeminiJSONParser().parse(json.dumps(payload), "").metadata == {}


@pytest.mark.parametrize("model", ["PRIVATE MODEL", "https://private.test/key", "x" * 129])
def test_gemini_drops_invalid_model_identifier(model):
    parsed = GeminiJSONParser().parse(json.dumps(_success(stats={"models": {model: {}}})), "")
    assert parsed.metadata == {}


def test_gemini_ignores_unsafe_metadata_and_bounds_token_counts():
    stats = {
        "models": {
            "gemini-test": {
                "api": {"totalErrors": 0, "totalLatencyMs": -1, "PRIVATE": "BODY"},
                "tokens": {"prompt": True, "candidates": 0, "total": MAX_METADATA_NUMBER + 1, "secret": "PRIVATE"},
            },
        },
    }
    parsed = GeminiJSONParser().parse(json.dumps(_success(stats=stats)), "PRIVATE_STDERR")
    assert parsed.metadata == {"actual_model": "gemini-test", "usage": {"candidates": 0}}


def test_gemini_empty_response_has_explicit_error_code():
    with pytest.raises(ParserError) as failure:
        GeminiJSONParser().parse('{"response":""}', "")
    assert failure.value.code == "empty_response"
