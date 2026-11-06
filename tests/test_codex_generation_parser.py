"""Generation accepts only a complete, unambiguous text-only Codex turn."""

import hashlib
import json
from pathlib import Path

import pytest

from clink.parsers import codex_generation
from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.codex_generation import PARSER_CONTRACT, CodexGenerationParser, parser_contract_digest

_THREAD = {"type": "thread.started", "thread_id": "PRIVATE_THREAD"}
_START = {"type": "turn.started"}
_USAGE = {"input_tokens": 3, "cached_input_tokens": 1, "output_tokens": 5}
_TERMINAL = {"type": "turn.completed", "usage": _USAGE}


def _item(text="done", *, item_type="agent_message", event_type="item.completed", **fields):
    return {"type": event_type, "item": {"type": item_type, "text": text, **fields}}


def _stream(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events)


def _parse(*events, stderr=""):
    return CodexGenerationParser().parse(_stream(_THREAD, _START, *events), stderr)


def _reject(*events, code="protocol_error", stderr="PRIVATE_STDERR"):
    with pytest.raises(ParserError) as error:
        _parse(*events, stderr=stderr)
    assert error.value.code == code
    assert "PRIVATE" not in str(error.value)


def test_normalized_direct_success_fixture_is_accepted_without_claiming_model():
    fixture = Path(__file__).parent / "fixtures/clink/codex-0.166.0-direct-success.jsonl"
    parsed = CodexGenerationParser().parse(fixture.read_text(), "PRIVATE_STDERR")
    assert parsed.content == "CLINK_NATIVE_TEXT_READY"
    assert parsed.metadata == {
        "actual_model": None,
        "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
    }


@pytest.mark.parametrize(
    "text",
    [
        "  leading and trailing  \n\t",
        "\r\n\t{x: \\\\value} \r\n",
        "e\u0301 é 中文 🦉 \u2028 \u2029 \u0085\n",
    ],
)
def test_final_text_preserves_exact_whitespace_unicode_and_backslashes(text):
    parsed = _parse(_item(text), _TERMINAL)
    assert parsed.content.encode("utf-8") == text.encode("utf-8")
    assert parsed.metadata == {"actual_model": None, "usage": _USAGE}


def test_source_shaped_reasoning_and_usage_are_supported_without_leaking_reasoning():
    usage = {**_USAGE, "cache_write_input_tokens": 0, "reasoning_output_tokens": 7}
    parsed = _parse(
        _item("PRIVATE_REASONING", item_type="reasoning", id="reasoning"),
        _item("done", id="answer"),
        {"type": "turn.completed", "usage": usage},
    )
    assert parsed.content == "done"
    assert parsed.metadata == {"actual_model": None, "usage": usage}
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize("item_type", ["reasoning", "agent_message"])
def test_text_lifecycle_uses_only_completed_text(item_type):
    events = [
        _item("PRIVATE_PARTIAL", item_type=item_type, event_type="item.started", id="text"),
        _item("PRIVATE_PARTIAL", item_type=item_type, event_type="item.updated", id="text"),
        _item("final", item_type=item_type, id="text"),
    ]
    if item_type == "reasoning":
        events.append(_item("final", id="answer"))
    parsed = _parse(*events, _TERMINAL)
    assert parsed.content == "final"


def test_crlf_and_json_whitespace_are_accepted():
    stdout = " \t\r\n" + "\r\n".join(_stream(_THREAD, _START, _item(), _TERMINAL).split("\n")) + "\r\n"
    assert CodexGenerationParser().parse(stdout).content == "done"


@pytest.mark.parametrize("model", [None, "gpt-6.1-sol", "native_identifier-1"])
def test_actual_model_is_preserved_only_when_reported(model):
    parsed = _parse(_item(), {**_TERMINAL, "model": model})
    assert parsed.metadata["actual_model"] == model


@pytest.mark.parametrize("model", [True, 3, [], {}, "", "PRIVATE MODEL", "a" * 129, "\ud800"])
def test_invalid_actual_model_does_not_become_unreported(model):
    _reject(_item(), {**_TERMINAL, "model": model})


@pytest.mark.parametrize(
    "item_type",
    [
        "command_execution",
        "web_search",
        "mcp_tool_call",
        "file_change",
        "collab_tool_call",
        "todo_list",
        "image_generation",
    ],
)
@pytest.mark.parametrize("event_type", ["item.started", "item.updated", "item.completed"])
def test_every_known_tool_item_fails_closed_before_a_final(item_type, event_type):
    _reject(
        _item("PRIVATE_TOOL", item_type=item_type, event_type=event_type, payload={"PRIVATE": "value"}),
        _item(),
        _TERMINAL,
        code="unexpected_tool_event",
    )


@pytest.mark.parametrize(
    "event",
    [
        {"type": "future.event", "payload": {"status": "failed"}},
        {"type": "tool.started", "name": "shell"},
        {"type": "item.completed", "item": {"type": "future_item", "text": "PRIVATE_TEXT"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": {"value": "done"}}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done", "status": "failed"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done", "error": None}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done", "metadata": {}}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done", "id": None}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done", "id": " "}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}, "status": "failed"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}, "error": {}},
        {"type": "item.completed", "item": []},
    ],
)
def test_unknown_or_nested_payloads_never_establish_generation(event):
    _reject(event, _TERMINAL)


@pytest.mark.parametrize(
    "terminal",
    [
        {**_TERMINAL, "status": "completed"},
        {**_TERMINAL, "status": "failed"},
        {**_TERMINAL, "error": None},
        {**_TERMINAL, "errors": []},
        {**_TERMINAL, "is_error": False},
        {**_TERMINAL, "payload": {"status": "failed"}},
        {"type": "turn.completed"},
        {**_TERMINAL, "usage": None},
        {**_TERMINAL, "usage": {**_USAGE, "nested": {"status": "failed"}}},
        {**_TERMINAL, "usage": {**_USAGE, "total_tokens": 8}},
        {**_TERMINAL, "usage": {"input_tokens": 3, "output_tokens": 5}},
    ],
)
def test_terminal_requires_the_explicit_success_shape(terminal):
    _reject(_item(), terminal)


@pytest.mark.parametrize("count", [True, -1, MAX_METADATA_NUMBER + 1, "3", None, [], {}, 1.5])
def test_usage_counts_are_validated_instead_of_dropped(count):
    _reject(_item(), {**_TERMINAL, "usage": {**_USAGE, "input_tokens": count}})


@pytest.mark.parametrize(
    "events",
    [
        [],
        [_item()],
        [_TERMINAL],
        [_item(" "), _TERMINAL],
        [_item(""), _TERMINAL],
        [_item(), _item("second"), _TERMINAL],
        [_item(), _item("later reasoning", item_type="reasoning"), _TERMINAL],
        [_item(), _TERMINAL, _TERMINAL],
        [_item(), _TERMINAL, {"type": "error", "message": "authentication_required"}],
        [_item(event_type="item.started", id="incomplete"), _TERMINAL],
        [_item(item_type="reasoning", event_type="item.started", id="incomplete"), _item(), _TERMINAL],
        [_item(item_type="reasoning", id="same"), _item(id="same"), _TERMINAL],
        [_item(item_type="reasoning", event_type="item.started", id="same"), _item(id="same"), _TERMINAL],
        [_item(event_type="item.started", id="same"), _item(event_type="item.started", id="same"), _TERMINAL],
        [{"type": "turn.started"}, _item(), _TERMINAL],
        [{"type": "thread.started", "thread_id": "PRIVATE_OTHER"}, _item(), _TERMINAL],
    ],
)
def test_incomplete_ambiguous_or_contradictory_turns_fail(events):
    _reject(*events)


@pytest.mark.parametrize(
    "events",
    [
        [_START, _item(), _TERMINAL],
        [_THREAD, _item(), _TERMINAL],
        [_THREAD, _START, {**_START, "payload": {}}, _item(), _TERMINAL],
        [{"type": "thread.started"}, _START, _item(), _TERMINAL],
        [{**_THREAD, "thread_id": []}, _START, _item(), _TERMINAL],
        [{**_THREAD, "unknown": {}}, _START, _item(), _TERMINAL],
    ],
)
def test_thread_and_turn_start_have_strict_shapes_and_order(events):
    with pytest.raises(ParserError) as error:
        CodexGenerationParser().parse(_stream(*events))
    assert error.value.code == "protocol_error"


@pytest.mark.parametrize(
    "event",
    [
        {"type": "error", "message": "PRIVATE_FAILURE"},
        {"type": "turn.failed", "error": {"message": "PRIVATE_FAILURE"}},
        {"type": "item.completed", "item": {"type": "error", "message": "PRIVATE_FAILURE"}},
        {"type": "turn.cancelled"},
        {"type": "turn.incomplete"},
        {"type": "error", "message": "PRIVATE_FAILURE", "nested": {"code": 401}},
        {"type": "turn.failed", "error": {"message": "PRIVATE_FAILURE", "code": 401}},
    ],
)
def test_errors_and_warnings_cannot_produce_partial_success(event):
    _reject(event, _item(), _TERMINAL)


@pytest.mark.parametrize(
    "diagnostic,code", [("authentication_required", "authentication_required"), ("rate_limit_exceeded", "rate_limited")]
)
@pytest.mark.parametrize("source", ["error", "turn.failed", "item", "stderr"])
def test_known_failure_diagnostics_keep_safe_codes_without_returning_raw_text(diagnostic, code, source):
    if source == "turn.failed":
        event = {"type": "turn.failed", "error": {"message": diagnostic + " PRIVATE_FAILURE"}}
    elif source == "item":
        event = {"type": "item.completed", "item": {"type": "error", "message": diagnostic + " PRIVATE_FAILURE"}}
    else:
        event = {"type": "error", "message": "PRIVATE_FAILURE" if source == "stderr" else diagnostic}
    _reject(event, code=code, stderr=diagnostic if source == "stderr" else "PRIVATE_STDERR")


def test_assistant_or_reasoning_prose_is_never_treated_as_an_authentication_error():
    parsed = _parse(_item("rate_limit_exceeded", item_type="reasoning"), _item("authentication_required"), _TERMINAL)
    assert parsed.content == "authentication_required"


@pytest.mark.parametrize(
    "line",
    [
        "not JSON",
        "[]",
        "null",
        "true",
        "42",
        '"text"',
        "{}",
        '{"type":[]}',
        '{"type":"turn.started","type":"turn.started"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"PRIVATE","text":"done"}}',
        '{"type":"item.completed","item":{"type":"agent_message","id":"a","id":"b","text":"done"}}',
        '{"type":"turn.completed","usage":{"input_tokens":NaN}}',
        '{"type":"turn.completed","usage":{"input_tokens":Infinity}}',
        '{"type":"turn.started"} {"type":"turn.started"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"\\ud800"}}',
        "\v",
    ],
)
def test_malformed_duplicate_or_non_utf8_records_are_protocol_errors(line):
    stdout = _stream(_THREAD, _START) + "\n" + line + "\n" + _stream(_item(), _TERMINAL)
    with pytest.raises(ParserError) as error:
        CodexGenerationParser().parse(stdout)
    assert error.value.code == "protocol_error"
    assert "PRIVATE" not in str(error.value)


def test_event_budget_is_enforced(monkeypatch):
    monkeypatch.setattr("clink.parsers.codex_generation.MAX_EVENTS", 3)
    _reject(_item(), _TERMINAL)


def test_parser_contract_has_a_stable_independent_digest():
    identity = {
        "contract": PARSER_CONTRACT,
        "source_sha256": {
            name: hashlib.sha256(Path(codex_generation.__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("codex_generation.py", "base.py")
        },
    }
    expected = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert parser_contract_digest() == expected
    assert parser_contract_digest() == expected
    assert PARSER_CONTRACT.startswith("codex-generation-jsonl/v1\n")


@pytest.mark.parametrize("changed", ["codex_generation.py", "base.py", "contract"])
def test_identity_changes_when_contract_or_implementation_changes(tmp_path, monkeypatch, changed):
    parser_source = tmp_path / "codex_generation.py"
    parser_source.write_text("synthetic parser")
    base_source = tmp_path / "base.py"
    base_source.write_text("synthetic base")
    monkeypatch.setattr(codex_generation, "__file__", str(parser_source))
    original = parser_contract_digest()
    if changed == "contract":
        monkeypatch.setattr(codex_generation, "PARSER_CONTRACT", PARSER_CONTRACT + "changed")
    else:
        (tmp_path / changed).write_text("changed implementation")
    assert parser_contract_digest() != original


@pytest.mark.parametrize("condition", ["missing", "oversized"])
def test_identity_reads_are_bounded_and_fail_closed(tmp_path, monkeypatch, condition):
    parser_source = tmp_path / "codex_generation.py"
    parser_source.write_text("synthetic parser")
    monkeypatch.setattr(codex_generation, "__file__", str(parser_source))
    if condition == "oversized":
        monkeypatch.setattr(codex_generation, "_MAX_SOURCE_BYTES", 1)
    with pytest.raises(ParserError) as error:
        parser_contract_digest()
    assert error.value.code == "protocol_error"
    assert str(tmp_path) not in str(error.value)
