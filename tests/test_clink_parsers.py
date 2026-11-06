"""Strict Codex JSONL parsing and shared diagnostic redaction contracts."""

import json
from pathlib import Path

import pytest

from clink.parsers import get_parser
from clink.parsers.base import MAX_METADATA_NUMBER, MAX_SOURCE_URL_LENGTH, MAX_SOURCE_URLS, ParserError, cli_failure
from clink.parsers.codex import CodexJSONLParser

_MESSAGE = {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Hello"}}
_TERMINAL = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}


def _jsonl(*events):
    return "\n".join(json.dumps(event) for event in events)


def test_codex_parser_success():
    parsed = CodexJSONLParser().parse(_jsonl(_MESSAGE, _TERMINAL), stderr="")
    assert parsed.content == "Hello"
    assert parsed.metadata == {"usage": {"input_tokens": 10, "output_tokens": 5}}


def test_codex_stream_keeps_only_completed_messages_and_safe_terminal_metadata():
    events = [
        {"type": "thread.started", "thread_id": "PRIVATE_SESSION"},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"type": "agent_message", "text": "PRIVATE_PARTIAL"}},
        _MESSAGE,
        {"type": "item.completed", "item": {"type": "agent_message", "text": " World "}},
        {
            "type": "turn.completed",
            "model": "gpt-6.1-sol",
            "usage": {"output_tokens": 5, "secret": "PRIVATE_USAGE", "input_tokens": True},
            "session": "PRIVATE_SESSION",
        },
    ]
    parsed = CodexJSONLParser().parse(_jsonl(*events), stderr="PRIVATE_STDERR")
    assert parsed.content == "Hello\n\nWorld"
    assert parsed.metadata == {"usage": {"output_tokens": 5}, "actual_model": "gpt-6.1-sol"}
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize(
    "line", ["not JSON", "[]", "null", "true", "42", '"text"', "{", "{}", '{"type": []}', '{"type": ""}']
)
def test_codex_rejects_every_malformed_or_nonobject_line(line):
    with pytest.raises(ParserError):
        CodexJSONLParser().parse(_jsonl(_MESSAGE) + "\n" + line + "\n" + _jsonl(_TERMINAL), "")


@pytest.mark.parametrize(
    "item",
    [
        None,
        [],
        "text",
        1,
        {},
        {"type": []},
        {"type": ""},
        {"type": "agent_message", "text": []},
        {"type": "agent_message", "text": "Hi", "status": []},
    ],
)
def test_codex_rejects_invalid_item_shapes_as_parser_errors(item):
    with pytest.raises(ParserError):
        CodexJSONLParser().parse(_jsonl({"type": "item.completed", "item": item}, _TERMINAL), "")


@pytest.mark.parametrize(
    "events",
    [
        [],
        [_MESSAGE],
        [_TERMINAL, _MESSAGE],
        [_MESSAGE, _TERMINAL, _TERMINAL],
        [_MESSAGE, {"type": "turn.completed", "usage": []}],
        [_MESSAGE, {"type": "turn.completed", "status": []}],
        [_MESSAGE, {"type": "turn.completed", "status": "in_progress"}],
    ],
)
def test_codex_requires_a_single_final_terminal_record(events):
    with pytest.raises(ParserError):
        CodexJSONLParser().parse(_jsonl(*events), "")


def test_codex_parser_requires_agent_message():
    with pytest.raises(ParserError) as failure:
        CodexJSONLParser().parse(_jsonl(_TERMINAL), "")
    assert failure.value.code == "empty_response"


@pytest.mark.parametrize(
    "failure_event",
    [
        {"type": "error", "message": "PRIVATE_ERROR"},
        {"type": "turn.failed", "error": {"message": "PRIVATE_ERROR"}},
        {"type": "turn.cancelled"},
        {"type": "turn.completed", "status": "failed"},
        {"type": "item.completed", "item": {"type": "error", "message": "PRIVATE_ERROR"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi", "status": "failed"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi", "status": "error"}},
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 1, "status": "completed"}},
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": -15}},
    ],
)
def test_codex_never_converts_error_events_into_success(failure_event):
    with pytest.raises(ParserError) as failure:
        CodexJSONLParser().parse(_jsonl(_MESSAGE, failure_event, _TERMINAL), "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize("exit_code", [None, [], True, "0"])
def test_codex_rejects_malformed_command_exit_codes(exit_code):
    event = {"type": "item.completed", "item": {"type": "command_execution", "exit_code": exit_code}}
    with pytest.raises(ParserError):
        CodexJSONLParser().parse(_jsonl(event, _MESSAGE, _TERMINAL), "")


def test_codex_accepts_successful_command_without_retaining_command_body():
    event = {
        "type": "item.completed",
        "item": {"type": "command_execution", "exit_code": 0, "aggregated_output": "PRIVATE_COMMAND_BODY"},
    }
    parsed = CodexJSONLParser().parse(_jsonl(event, _MESSAGE, _TERMINAL), "")
    assert parsed.content == "Hello"
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize("count", [True, -1, MAX_METADATA_NUMBER + 1, "PRIVATE_TOKEN", [], 1.5])
def test_codex_drops_invalid_or_unbounded_telemetry(count):
    terminal = {"type": "turn.completed", "usage": {"input_tokens": count, "output_tokens": 0}}
    parsed = CodexJSONLParser().parse(_jsonl(_MESSAGE, terminal), "")
    assert parsed.metadata == {"usage": {"output_tokens": 0}}


def test_codex_does_not_claim_search_or_sources_from_unverified_records_or_prose():
    events = [
        {"type": "item.completed", "item": {"type": "web_search", "query": "PRIVATE_QUERY", "url": "file:///secret"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "I searched https://example.com"}},
        _TERMINAL,
    ]
    parsed = CodexJSONLParser().parse(_jsonl(*events), "")
    assert "search_performed" not in parsed.metadata
    assert "source_urls" not in parsed.metadata


def _search_event(results, *, action=None, event_type="item.completed"):
    return {
        "type": event_type,
        "item": {
            "id": "synthetic_search",
            "type": "web_search",
            "query": "PRIVATE_QUERY",
            "action": action if action is not None else {"type": "search"},
            "results": results,
        },
    }


def test_codex_source_shaped_web_search_fixture_keeps_literal_duplicate_ids():
    # Synthetic, not retained native output. Shape is pinned to rust-v0.166.0:
    # exec/src/exec_events.rs ThreadItem/WebSearchItem and the flat text_result
    # fixture in codex-api/src/endpoint/search.rs::search_posts_typed_request_and_parses_output.
    fixture = Path(__file__).parent / "fixtures/clink/codex-0.166.0-web-search-source-shape.jsonl"
    stdout = fixture.read_text()
    assert '"id":"item_1","type":"web_search","id":"synthetic_search"' in stdout
    parsed = CodexJSONLParser().parse(stdout, "PRIVATE_STDERR")
    assert parsed.content == "Synthetic source found."
    assert parsed.metadata["search_performed"] is True
    assert parsed.metadata["source_urls"] == ["https://example.com/result"]
    assert parsed.metadata["usage"]["cache_write_input_tokens"] == 0
    assert "synthetic_search" not in json.dumps(parsed.metadata)
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize(
    "raw",
    [
        '{"type":"item.completed","item":{"id":"a","id":"b","type":"agent_message","text":"Hi"}}',
        '{"type":"item.completed","item":{"id":"a","id":"b","id":"c","type":"web_search"}}',
        '{"type":"item.completed","item":{"id":"a","id":null,"type":"web_search"}}',
        '{"type":"turn.started","item":{"id":"a","id":"b","type":"web_search"}}',
        '{"type":"item.completed","id":"a","id":"b","item":{"type":"web_search"}}',
        '{"type":"item.completed","item":{"type":"web_search","action":{"id":"a","id":"b"}}}',
        '{"type":"item.completed","item":{"type":"web_search","results":[{"id":"a","id":"b"}]}}',
        '{"type":"item.completed","item":{"id":"a","id":"b","type":"error","type":"web_search"}}',
        '{"type":"item.completed","item":{"id":"a","id":"b","type":"web_search","error":{},"error":null}}',
        '{"type":"item.completed","item":{"id":"a","id":"b","type":"web_search","status":"failed","status":"completed"}}',
    ],
)
def test_codex_native_id_exception_never_admits_other_duplicate_locations_or_fields(raw):
    with pytest.raises(ParserError):
        CodexJSONLParser().parse(raw + "\n" + _jsonl(_MESSAGE, _TERMINAL), "")


def test_codex_native_duplicate_id_does_not_hide_real_item_failure():
    raw = '{"type":"item.completed","item":{"id":"a","id":"b","type":"web_search","error":{"message":"PRIVATE_ERROR"}}}'
    with pytest.raises(ParserError):
        CodexJSONLParser().parse(raw + "\n" + _jsonl(_MESSAGE, _TERMINAL), "")


@pytest.mark.parametrize("action", [{"type": "search"}, {"type": "search", "query": None, "queries": None}])
def test_codex_explicit_empty_search_results_prove_search_with_no_results(action):
    parsed = CodexJSONLParser().parse(_jsonl(_search_event([], action=action), _MESSAGE, _TERMINAL), "")
    assert parsed.metadata["search_performed"] is True
    assert parsed.metadata["source_urls"] == []


@pytest.mark.parametrize(
    "event",
    [
        _search_event(None),
        _search_event([{"type": "future_result", "url": "https://example.com"}]),
        _search_event(["https://example.com"]),
        _search_event([{"type": "text_result", "url": "file:///private"}]),
        _search_event([], action={"type": "open_page", "url": "https://example.com"}),
        _search_event([{"type": "text_result", "url": "https://example.com"}], action={"type": "open_page"}),
        _search_event([], event_type="item.started"),
    ],
)
def test_codex_absent_unrecognized_or_nonsearch_results_do_not_prove_search(event):
    parsed = CodexJSONLParser().parse(_jsonl(event, _MESSAGE, _TERMINAL), "")
    assert "search_performed" not in parsed.metadata
    assert "source_urls" not in parsed.metadata


@pytest.mark.parametrize(
    "result",
    [
        {"type": "error", "message": "PRIVATE_ERROR"},
        {"type": "text_result", "status": "failed", "url": "https://example.com"},
        {"type": "text_result", "error": {}, "url": "https://example.com"},
        {"type": "text_result", "is_error": True, "url": "https://example.com"},
        {"type": "text_result", "is_error": 0, "url": "https://example.com"},
        {"type": "text_result", "is_error": [], "url": "https://example.com"},
    ],
)
def test_codex_search_result_errors_are_never_sources_even_with_unknown_action(result):
    for action in ({"type": "search"}, {"type": "other"}):
        with pytest.raises(ParserError) as failure:
            CodexJSONLParser().parse(_jsonl(_search_event([result], action=action), _MESSAGE, _TERMINAL), "")
        assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "action",
    [
        {"type": []},
        {"type": "search", "query": []},
        {"type": "search", "queries": [None]},
        {"type": "open_page", "url": []},
    ],
)
def test_codex_native_optional_action_fields_are_shape_checked(action):
    with pytest.raises(ParserError):
        CodexJSONLParser().parse(_jsonl(_search_event([], action=action), _MESSAGE, _TERMINAL), "")


@pytest.mark.parametrize(
    "url",
    [
        "file:///private/secret",
        "javascript:alert(1)",
        "https://PRIVATE@example.com/path",
        "https://example.com:99999/path",
        "https://[invalid/path",
        "https://example.com/PRIVATE\nvalue",
        "https://example.com/white space",
        "https://example.com/" + "x" * MAX_SOURCE_URL_LENGTH,
        "https://example.com\\private",
        "https://%",
        "https://exa%mple.test",
        "https://\\ud800.test",
        "https://example.com/\\ud800",
        "https://example.com/\\u0080",
        "https://example.com/\\u202e",
        None,
        [],
    ],
)
def test_codex_never_returns_unsafe_or_unbounded_result_urls(url):
    event = _search_event([{"type": "text_result", "url": url}])
    parsed = CodexJSONLParser().parse(_jsonl(event, _MESSAGE, _TERMINAL), "")
    assert "source_urls" not in parsed.metadata


def test_codex_sources_are_bounded_deduplicated_and_exclude_result_bodies_and_url_tokens():
    results = [
        {"type": "text_result", "url": "https://example.com/result?token=PRIVATE#PRIVATE", "title": "PRIVATE_BODY"},
        {"type": "text_result", "url": "https://example.com/result"},
    ]
    results.extend(
        {"type": "text_result", "url": f"https://example.com/{index}"} for index in range(MAX_SOURCE_URLS + 5)
    )
    parsed = CodexJSONLParser().parse(_jsonl(_search_event(results), _MESSAGE, _TERMINAL), "PRIVATE_STDERR")
    assert parsed.metadata["source_urls"][0] == "https://example.com/result"
    assert len(parsed.metadata["source_urls"]) == MAX_SOURCE_URLS
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize("model", ["PRIVATE MODEL", "https://private.test/key", "x" * 129, [], None])
def test_codex_drops_invalid_reported_model(model):
    parsed = CodexJSONLParser().parse(_jsonl(_MESSAGE, {"type": "turn.completed", "model": model}), "")
    assert parsed.metadata == {}


@pytest.mark.parametrize(
    ("diagnostic", "code"),
    [
        ("Invalid API key: PRIVATE_SECRET", "authentication_required"),
        ({"error": {"code": "authentication_error", "message": "PRIVATE_SECRET"}}, "authentication_required"),
        ({"error": {"code": 401}}, "authentication_required"),
        ("Not logged in. PRIVATE_SECRET", "authentication_required"),
        ("rate_limit_exceeded PRIVATE_SECRET", "rate_limited"),
        ("Attempt 1 failed with status 429. PRIVATE_SECRET", "rate_limited"),
        ({"error": {"code": 429}}, "rate_limited"),
        ("RESOURCE_EXHAUSTED PRIVATE_SECRET", "rate_limited"),
        ("PRIVATE_SECRET unknown failure 4290", "parse_error"),
        ({"session": {"message": "Invalid API key"}}, "parse_error"),
    ],
)
def test_known_error_signatures_return_only_fixed_sanitized_failures(diagnostic, code):
    failure = cli_failure(diagnostic)
    assert failure.code == code
    assert "PRIVATE" not in str(failure)
    assert "session" not in str(failure)


def test_codex_error_classification_uses_error_record_without_echoing_it():
    stdout = _jsonl({"type": "error", "message": "Invalid API key PRIVATE_SECRET"})
    with pytest.raises(ParserError) as failure:
        CodexJSONLParser().parse(stdout, "PRIVATE_STDERR")
    assert failure.value.code == "authentication_required"
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "raw", ['{"type":"turn.completed","type":"error"}', '{"type":"turn.completed","usage":{"input_tokens":NaN}}']
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_constants_without_raw_context(raw):
    with pytest.raises(ParserError) as failure:
        CodexJSONLParser().parse(_jsonl(_MESSAGE) + "\n" + raw, "")
    assert failure.value.__context__ is None
    assert failure.value.__cause__ is None


@pytest.mark.parametrize("name", ["PRIVATE_UNKNOWN_PARSER", None, [], {}])
def test_parser_registry_rejects_unknown_names_without_echoing_them(name):
    with pytest.raises(ParserError) as failure:
        get_parser(name)
    assert failure.value.code == "parse_error"
    assert "PRIVATE" not in str(failure.value)


def test_parser_error_default_code():
    assert ParserError("Fixed error").code == "parse_error"
