"""Native generation publishes no telemetry bodies and requires one valid turn."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from clink.parsers import codex_native_generation
from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.codex_native_generation import PARSER_CONTRACT, CodexNativeGenerationParser, parser_contract_digest

_SESSION = "019a3f08-e78b-7aba-9bcc-d2e5603adb35"
_THREAD = {"type": "thread.started", "thread_id": _SESSION}
_START = {"type": "turn.started"}
_USAGE = {"input_tokens": 3, "cached_input_tokens": 1, "output_tokens": 5, "reasoning_output_tokens": 0}
_TERMINAL = {"type": "turn.completed", "usage": _USAGE}


def _stream(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events)


def _item(item_type="agent_message", *, event_type="item.completed", id="item_0", **fields):
    return {"type": event_type, "item": {"id": id, "type": item_type, **fields}}


def _command(**fields):
    return _item(
        "command_execution",
        **{
            "command": "PRIVATE_COMMAND",
            "aggregated_output": "PRIVATE_OUTPUT",
            "exit_code": 0,
            "status": "completed",
            **fields,
        },
    )


def _mcp(**fields):
    return _item(
        "mcp_tool_call",
        **{
            "server": "PRIVATE_SERVER",
            "tool": "PRIVATE_TOOL",
            "arguments": None,
            "result": None,
            "error": None,
            "status": "completed",
            **fields,
        },
    )


def _collab(**fields):
    return _item(
        "collab_tool_call",
        **{
            "tool": "spawn_agent",
            "sender_thread_id": "PRIVATE_SENDER",
            "receiver_thread_ids": ["PRIVATE_RECEIVER"],
            "prompt": "PRIVATE_PROMPT",
            "agents_states": {"PRIVATE_RECEIVER": {"status": "running", "message": None}},
            "status": "completed",
            **fields,
        },
    )


def _parse(*events, stderr="PRIVATE_STDERR"):
    return CodexNativeGenerationParser().parse(_stream(_THREAD, _START, *events), stderr)


def _reject(*events, code="protocol_error", stderr="PRIVATE_STDERR"):
    with pytest.raises(ParserError) as error:
        _parse(*events, stderr=stderr)
    assert error.value.code == code
    assert "PRIVATE" not in str(error.value)


def test_completed_turn_needs_no_observed_message_or_finalfile_equality():
    result = _parse(_TERMINAL)
    assert result.content == ""
    assert result.metadata == {
        "actual_model": None,
        "tool_error_count": 0,
        "permission_denial_count": 0,
        "usage": _USAGE,
    }


def test_all_streamed_message_bodies_are_discarded_with_unicode_and_exact_jsonl_delimiters():
    result = _parse(
        _item("reasoning", text="PRIVATE_REASONING \u2028 \u2029 \u0085"),
        _item(id="comment", text="PRIVATE_COMMENTARY"),
        _command(id="tool"),
        _item(id="answer", text="PRIVATE_FINAL"),
        _TERMINAL,
    )
    assert result.content == ""
    assert "PRIVATE" not in json.dumps(result.metadata)
    assert _SESSION not in json.dumps(result.metadata)


def test_supported_tool_lifecycles_and_opaque_json_payloads_remain_private():
    opaque = {
        "type": "turn.failed",
        "error": {"message": "401 unauthorized"},
        "status": "declined",
        "PRIVATE": [1, None],
    }
    mcp = _mcp(
        id="mcp",
        arguments=[opaque],
        result={"content": [opaque, None, 7], "structured_content": False, "_meta": opaque},
    )
    todo = _item(
        "todo_list", id="todo", event_type="item.started", items=[{"text": "PRIVATE_PLAN", "completed": False}]
    )
    result = _parse(
        _command(event_type="item.started", status="in_progress", exit_code=None),
        _command(event_type="item.updated", status="in_progress", exit_code=None),
        _command(),
        {**mcp, "type": "item.started", "item": {**mcp["item"], "status": "in_progress", "result": None}},
        mcp,
        _collab(id="collab"),
        _item("file_change", id="file", changes=[{"path": "PRIVATE_FILE", "kind": "update"}], status="completed"),
        _item(
            "web_search",
            id="web",
            query="PRIVATE_QUERY",
            action={"type": "search", "queries": ["PRIVATE"]},
            results=[opaque],
        ),
        todo,
        {**todo, "type": "item.updated"},
        {**todo, "type": "item.completed"},
        _TERMINAL,
    )
    assert result.content == ""
    assert result.metadata["tool_error_count"] == 0
    assert "PRIVATE" not in json.dumps(result.metadata)


def test_recovered_failures_and_pre_turn_warnings_count_without_rejecting_success():
    warning = _item("error", id="warning", message="PRIVATE authentication_required")
    events = [
        _THREAD,
        warning,
        _START,
        _command(status="failed", exit_code=1),
        _mcp(id="mcp", status="failed", error={"message": "PRIVATE rate_limit_exceeded"}),
        _collab(id="collab", status="failed"),
        _item("file_change", id="file", changes=[], status="failed"),
        _TERMINAL,
    ]
    result = CodexNativeGenerationParser().parse(_stream(*events))
    assert result.content == ""
    assert result.metadata["tool_error_count"] == 5
    assert result.metadata["permission_denial_count"] == 0


@pytest.mark.parametrize("status,exit_code", [("declined", -1), ("declined", None), ("completed", 2)])
def test_command_failure_without_source_supported_signature_is_only_counted(status, exit_code):
    result = _parse(_command(status=status, exit_code=exit_code, aggregated_output="permission denied"), _TERMINAL)
    assert result.metadata["tool_error_count"] == 1
    assert result.metadata["permission_denial_count"] == 0


@pytest.mark.parametrize(
    "diagnostic",
    [
        "approval required by policy, but AskForApproval is set to Never",
        "approval required by policy, but AskForApproval::Granular.sandbox_approval is false",
        "approval required by policy rule, but AskForApproval::Granular.rules is false",
        "`PRIVATE_COMMAND` rejected: policy forbids commands starting with `PRIVATE_PREFIX`",
    ],
)
def test_exact_completed_command_refusals_fail_with_permission_needed(diagnostic):
    _reject(
        _command(status="declined", exit_code=-1, aggregated_output=diagnostic), _TERMINAL, code="permission_needed"
    )
    for overrides in ({"status": "failed"}, {"exit_code": 1}, {"aggregated_output": diagnostic + "\n"}):
        fields = {"status": "declined", "exit_code": -1, "aggregated_output": diagnostic, **overrides}
        assert _parse(_command(**fields), _TERMINAL).metadata["permission_denial_count"] == 0


@pytest.mark.parametrize(
    "diagnostic",
    [
        "MCP tool call requires approval, but approval policy is never",
        "MCP tool call cancelled: interactive approval is unavailable in exec mode",
    ],
)
def test_exact_failed_mcp_refusal_requires_null_result_and_exact_message(diagnostic):
    fields = {"status": "failed", "error": {"message": diagnostic}}
    _reject(_mcp(**fields), _TERMINAL, code="permission_needed")
    for overrides in (
        {"status": "completed"},
        {"error": {"message": diagnostic + "\n"}},
        {"result": {"content": [], "structured_content": None}},
    ):
        assert _parse(_mcp(**{**fields, **overrides}), _TERMINAL).metadata["permission_denial_count"] == 0


def test_refusal_prose_in_assistant_reasoning_warning_or_mcp_arguments_has_no_authority():
    prose = "MCP tool call requires approval, but approval policy is never"
    result = _parse(
        _item(text=prose),
        _item("reasoning", id="reason", text=prose),
        _item("error", id="warning", message=prose),
        _mcp(id="mcp", arguments={"error": {"message": prose}}),
        _TERMINAL,
    )
    assert result.metadata["permission_denial_count"] == 0
    assert result.metadata["tool_error_count"] == 1


def test_permission_signature_in_started_item_does_not_establish_refusal():
    diagnostic = "approval required by policy, but AskForApproval is set to Never"
    result = _parse(
        _command(event_type="item.started", status="declined", exit_code=-1, aggregated_output=diagnostic),
        _command(),
        _TERMINAL,
    )
    assert result.metadata["permission_denial_count"] == 0


@pytest.mark.parametrize(
    "diagnostic,code", [("authentication_required", "authentication_required"), ("rate_limit_exceeded", "rate_limited")]
)
@pytest.mark.parametrize("source", ["error", "turn.failed", "error_then_failed", "stderr"])
def test_fatal_outcomes_classify_only_safe_known_diagnostics(diagnostic, code, source):
    error = {"type": "error", "message": diagnostic if source == "error" else "PRIVATE_FAILURE"}
    failure = {"type": "turn.failed", "error": {"message": diagnostic if source != "stderr" else "PRIVATE_FAILURE"}}
    events = (
        [error, failure] if source == "error_then_failed" else [error] if source in {"error", "stderr"} else [failure]
    )
    _reject(*events, code=code, stderr=diagnostic if source == "stderr" else "PRIVATE_STDERR")


@pytest.mark.parametrize(
    "diagnostic,code", [("authentication_required", "authentication_required"), ("rate_limit_exceeded", "rate_limited")]
)
@pytest.mark.parametrize("item_type", ["command_execution", "todo_list"])
def test_fatal_diagnostic_survives_native_terminal_item_reconciliation(diagnostic, code, item_type):
    if item_type == "command_execution":
        started = _command(event_type="item.started", status="in_progress", exit_code=None)
        completed = _command(status="failed", exit_code=1)
    else:
        started = _item("todo_list", event_type="item.started", items=[])
        completed = {**started, "type": "item.completed"}
    _reject(
        started,
        {"type": "error", "message": diagnostic},
        completed,
        {"type": "turn.failed", "error": {"message": "PRIVATE_FAILURE"}},
        code=code,
    )


@pytest.mark.parametrize(
    "events",
    [
        [],
        [_item(text="PRIVATE_FINAL")],
        [_command(event_type="item.started", status="in_progress"), _TERMINAL],
        [_command(event_type="item.updated", status="in_progress"), _TERMINAL],
        [_command(status="in_progress"), _TERMINAL],
        [_TERMINAL, _TERMINAL],
        [_TERMINAL, _command()],
        [_TERMINAL, {"type": "turn.failed", "error": {"message": "PRIVATE"}}],
        [{"type": "turn.failed", "error": {"message": "PRIVATE"}}, _TERMINAL],
        [{"type": "error", "message": "PRIVATE"}, _TERMINAL],
        [_command(), _command(), _TERMINAL],
        [_command(event_type="item.started"), _command(event_type="item.started"), _command(), _TERMINAL],
        [_command(event_type="item.started"), _item(text="PRIVATE"), _TERMINAL],
        [_START, _TERMINAL],
        [_THREAD, _TERMINAL],
    ],
)
def test_incomplete_or_contradictory_lifecycles_fail_even_if_a_finalfile_might_exist(events):
    _reject(*events)


@pytest.mark.parametrize(
    "events",
    [
        [_START, _TERMINAL],
        [_THREAD, _TERMINAL],
        [{**_THREAD, "thread_id": "PRIVATE"}, _START, _TERMINAL],
        [{**_THREAD, "extra": None}, _START, _TERMINAL],
        [_THREAD, _item(text="PRIVATE"), _START, _TERMINAL],
        [_THREAD, {**_START, "thread_id": _SESSION}, _TERMINAL],
    ],
)
def test_thread_and_turn_identity_shape_and_order_are_strict(events):
    with pytest.raises(ParserError) as error:
        CodexNativeGenerationParser().parse(_stream(*events))
    assert error.value.code == "protocol_error"


@pytest.mark.parametrize("item_type", ["future_item", "image_generation", "plan", "tool_result"])
def test_unsupported_item_types_fail_closed(item_type):
    _reject(_item(item_type, text="PRIVATE"), _TERMINAL)


@pytest.mark.parametrize(
    "event",
    [
        _item(text="PRIVATE", metadata={}),
        _item(text="PRIVATE", id=" "),
        _command(exit_code=True),
        _command(exit_code=2**31),
        _command(status="cancelled"),
        _item("file_change", changes=[{"path": "PRIVATE", "kind": "rename"}], status="completed"),
        _item("file_change", changes=[{"path": "PRIVATE", "kind": "update", "extra": 1}], status="completed"),
        _mcp(arguments={}, result={"content": {}, "structured_content": None}),
        _mcp(error={"message": "PRIVATE", "code": 401}),
        _mcp(result={"content": []}),
        _collab(tool="send_message"),
        _collab(receiver_thread_ids="PRIVATE"),
        _collab(agents_states={"PRIVATE": {"status": "future", "message": None}}),
        _collab(agents_states={"PRIVATE": {"status": "running", "message": None, "extra": 1}}),
        _item("web_search", query="PRIVATE", action=[], results=[]),
        _item("web_search", query="PRIVATE", action={}, results=None),
        _item("todo_list", items=[{"text": "PRIVATE", "completed": 1}]),
        {**_TERMINAL, "model": "gpt-6.1-sol"},
        {**_TERMINAL, "status": "completed"},
        {**_TERMINAL, "usage": {**_USAGE, "total_tokens": 8}},
        {"type": "turn.completed"},
        {"type": "turn.interrupted"},
        {"type": "error", "message": "PRIVATE", "status": 401},
    ],
)
def test_malformed_typed_shapes_and_unknown_fields_fail_closed(event):
    _reject(event, *([] if event.get("type") == "turn.completed" else [_TERMINAL]))


@pytest.mark.parametrize("count", [True, -1, MAX_METADATA_NUMBER + 1, "3", None, [], {}, 1.5])
def test_usage_is_validated_instead_of_silently_dropped(count):
    _reject({**_TERMINAL, "usage": {**_USAGE, "input_tokens": count}})


@pytest.mark.parametrize("missing", list(_USAGE))
def test_pinned_required_usage_fields_cannot_be_omitted(missing):
    _reject({**_TERMINAL, "usage": {key: value for key, value in _USAGE.items() if key != missing}})


def test_all_source_usage_fields_are_allowlisted_and_bounded():
    usage = {**_USAGE, "cache_write_input_tokens": MAX_METADATA_NUMBER, "reasoning_output_tokens": 0}
    assert _parse({**_TERMINAL, "usage": usage}).metadata["usage"] == usage


def test_source_flattened_web_search_duplicate_id_is_the_only_duplicate_exception():
    item = '"id":"item_0","id":"PRIVATE_SEARCH","type":"web_search","query":"PRIVATE","action":{"type":"other"}'
    line = '{"type":"item.completed","item":{' + item + "}}"
    stdout = _stream(_THREAD, _START) + "\n" + line + "\n" + _stream(_TERMINAL)
    assert CodexNativeGenerationParser().parse(stdout).content == ""
    for malformed in (
        line.replace('"id":"item_0",', '"id":"x","id":"item_0",'),
        line.replace('"id":"item_0",', '"id":1,'),
        line.replace('{"type":"other"}', '{"type":"other","id":"a","id":"b"}'),
        line.replace('"type":"web_search"', '"type":"agent_message"'),
    ):
        with pytest.raises(ParserError) as error:
            CodexNativeGenerationParser().parse(_stream(_THREAD, _START) + "\n" + malformed + "\n" + _stream(_TERMINAL))
        assert error.value.code == "protocol_error"


@pytest.mark.parametrize(
    "line",
    [
        "not JSON",
        "[]",
        "null",
        "true",
        "42",
        "{}",
        "\v",
        '{"type":[]}',
        '{"type":"turn.started","type":"turn.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":NaN}}',
    ],
)
def test_malformed_json_is_safely_rejected(line):
    with pytest.raises(ParserError) as error:
        CodexNativeGenerationParser().parse(_stream(_THREAD, _START) + "\n" + line + "\n" + _stream(_TERMINAL))
    assert error.value.code == "protocol_error"


@pytest.mark.parametrize("payload", ["\ud800", {"PRIVATE": float("inf")}, {"\ud800": "PRIVATE"}])
def test_opaque_json_still_requires_finite_numbers_and_utf8(payload):
    event = _mcp(arguments=payload)
    stdout = "\n".join(json.dumps(value) for value in (_THREAD, _START, event, _TERMINAL))
    with pytest.raises(ParserError) as error:
        CodexNativeGenerationParser().parse(stdout)
    assert error.value.code == "protocol_error"


def test_json_exponent_overflow_in_opaque_payload_is_rejected():
    stdout = _stream(_THREAD, _START, _mcp(arguments="OVERFLOW"), _TERMINAL).replace('"OVERFLOW"', "1e999")
    with pytest.raises(ParserError) as error:
        CodexNativeGenerationParser().parse(stdout)
    assert error.value.code == "protocol_error"


def test_crlf_and_event_budget(monkeypatch):
    stdout = " \t\r\n" + _stream(_THREAD, _START, _TERMINAL).replace("\n", "\r\n") + "\r\n"
    monkeypatch.setattr(codex_native_generation, "MAX_EVENTS", 3)
    assert CodexNativeGenerationParser().parse(stdout).content == ""
    _reject(_item(text="PRIVATE"), _TERMINAL)


def test_parser_digest_binds_only_its_versioned_contract_and_parser_dependencies():
    identity = {
        "contract": PARSER_CONTRACT,
        "source_sha256": {
            name: hashlib.sha256(Path(codex_native_generation.__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("codex_native_generation.py", "base.py")
        },
    }
    expected = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert parser_contract_digest() == expected
    assert PARSER_CONTRACT.startswith("codex-native-generation-jsonl/v1\n")


@pytest.mark.parametrize("changed", ["codex_native_generation.py", "base.py", "contract"])
def test_identity_changes_when_its_implementation_or_contract_changes(tmp_path, monkeypatch, changed):
    source = tmp_path / "codex_native_generation.py"
    source.write_text("synthetic parser")
    (tmp_path / "base.py").write_text("synthetic base")
    monkeypatch.setattr(codex_native_generation, "__file__", str(source))
    original = parser_contract_digest()
    if changed == "contract":
        monkeypatch.setattr(codex_native_generation, "PARSER_CONTRACT", PARSER_CONTRACT + "changed")
    else:
        (tmp_path / changed).write_text("changed")
    assert parser_contract_digest() != original


@pytest.mark.parametrize("condition", ["missing", "oversized"])
def test_digest_source_reads_are_bounded_and_fail_closed(tmp_path, monkeypatch, condition):
    source = tmp_path / "codex_native_generation.py"
    source.write_text("synthetic parser")
    monkeypatch.setattr(codex_native_generation, "__file__", str(source))
    if condition == "oversized":
        monkeypatch.setattr(codex_native_generation, "_MAX_SOURCE_BYTES", 1)
    with pytest.raises(ParserError) as error:
        parser_contract_digest()
    assert error.value.code == "protocol_error"
    assert str(tmp_path) not in str(error.value)


def test_missing_required_tool_fields_are_rejected():
    for event in (_command(), _mcp(), _collab(), _item("file_change", changes=[], status="completed")):
        for field in event["item"]:
            malformed = copy.deepcopy(event)
            del malformed["item"][field]
            _reject(malformed, _TERMINAL)
