"""Synthetic installed-doc/type Antigravity contracts, never captured model output."""

import json

import pytest

from clink.parsers.agy_native import AgyNativeParser, validate_session_id
from clink.parsers.base import MAX_METADATA_NUMBER, ParserError


def stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def init(**changes):
    return {"event": "init", "conversation_id": "session-1", "init": {"tools": [], **changes}}


def terminal(**changes):
    return {
        "event": "result",
        "result": {"conversation_id": "session-1", "status": "SUCCESS", "response": "done", **changes},
    }


def step(**changes):
    return {
        "event": "step_update",
        "step_update": {
            "conversation_id": "session-1",
            "step_index": 1,
            "state": "DONE",
            "step_type": "tool",
            **changes,
        },
    }


def test_native_success_keeps_only_final_response_and_allowlisted_metadata():
    result = AgyNativeParser().parse(
        stream(
            init(model="agy-model", cwd="PRIVATE_CWD"),
            step(text_delta="PRIVATE_DELTA", subagent_info={"subagents": [{"conversation_id": "PRIVATE_CHILD"}]}),
            terminal(usage={"input_tokens": 2, "secret": "PRIVATE"}, num_turns=1, duration_seconds=0.5),
        ),
        "PRIVATE_STDERR",
    )
    assert result.state == "completed"
    assert result.content == "done"
    assert result.session_id == "session-1"
    assert result.metadata == {
        "tool_error_count": 0,
        "permission_denial_count": 0,
        "terminal_status": "SUCCESS",
        "truncated": False,
        "native_reported_model": "agy-model",
        "usage": {"input_tokens": 2},
        "num_turns": 1,
        "duration_seconds": 0.5,
    }
    assert "actual_model" not in result.metadata
    assert "PRIVATE" not in json.dumps(result.metadata)
    assert "session-1" not in json.dumps(result.metadata)


def test_recovered_tool_failure_does_not_override_success_and_is_counted_once():
    tool_info = {
        "parameters": "PRIVATE_ARGS",
        "output": "PRIVATE_OUTPUT",
        "error": {"type": "exit", "message": "PRIVATE_ERROR"},
    }
    result = AgyNativeParser().parse(
        stream(
            init(),
            step(state="ACTIVE", tool_info=tool_info),
            step(tool_info=tool_info),
            step(tool_info=tool_info),
            terminal(),
        )
    )
    assert result.state == "completed"
    assert result.metadata["tool_error_count"] == 1
    assert "PRIVATE" not in json.dumps(result.metadata)


def test_denied_actions_report_permission_needed_and_hide_details():
    result = AgyNativeParser().parse(
        stream(terminal(denied_actions=[{"action": "PRIVATE_ACTION", "display_name": "PRIVATE_TARGET"}]))
    )
    assert result.state == "permission_needed"
    assert result.metadata["permission_denial_count"] == 1
    assert "PRIVATE" not in json.dumps(result.metadata)


def test_prose_denial_does_not_guess_permission_state():
    result = AgyNativeParser().parse(
        stream(init(), terminal(response="Permission denied; approval required")), "permission denied"
    )
    assert result.state == "completed"


@pytest.mark.parametrize("reason", ["print_timeout", "interrupted", "error"])
def test_new_finished_reason_prevents_false_success(reason):
    result = AgyNativeParser().parse(stream(terminal(finished_reason=reason)))
    assert result.state == "failed"
    assert result.metadata["finished_reason"] == reason


@pytest.mark.parametrize("status", ["ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING", "RUNNING"])
def test_nonsuccess_status_is_failed_without_permission_guess(status):
    result = AgyNativeParser().parse(stream(terminal(status=status, error="PRIVATE_ERROR")))
    assert result.state == "failed"
    assert result.metadata["error_code"] == "native_error"
    assert "PRIVATE" not in json.dumps(result.metadata)


def test_success_status_does_not_override_terminal_error():
    result = AgyNativeParser().parse(stream(terminal(error="PRIVATE_ERROR")))
    assert result.state == "failed"


@pytest.mark.parametrize(
    "output",
    [
        "",
        "PRIVATE_INVALID",
        "[]",
        '{"event":"init","event":"result"}',
        stream({"status": "SUCCESS", "response": "PRIVATE"}),
        stream({"type": "turn_complete", "status": "SUCCESS"}),
        stream({"event": "turn_complete", "result": {}}),
        stream(init()),
        stream(terminal(), step()),
        stream(terminal(), terminal()),
        stream(init(), init(), terminal()),
        stream(init(), terminal(conversation_id="session-2")),
        stream(init(), step(conversation_id="session-2"), terminal()),
        stream({"event": "result", "result": []}),
        stream({"event": "result", "result": {}, "status": "SUCCESS"}),
    ],
)
def test_malformed_wrong_dialect_missing_or_contradictory_terminal_is_rejected(output):
    with pytest.raises(ParserError) as failure:
        AgyNativeParser().parse(output, "PRIVATE_STDERR")
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": None},
        {"status": True},
        {"status": "success"},
        {"finished_reason": "unknown"},
        {"finished_reason": None},
        {"error": {"message": "PRIVATE"}},
        {"response": None},
        {"conversation_id": None},
        {"conversation_id": "--unsafe"},
        {"usage": []},
        {"denied_actions": {}},
        {"denied_actions": ["PRIVATE"]},
        {"denied_actions": [{}]},
        {"denied_actions": [{"action": 2, "display_name": "PRIVATE"}]},
    ],
)
def test_invalid_terminal_fields_are_rejected(changes):
    with pytest.raises(ParserError):
        AgyNativeParser().parse(stream(terminal(**changes)))


@pytest.mark.parametrize(
    "changes",
    [
        {"step_index": True},
        {"step_index": -1},
        {"state": "unknown"},
        {"step_type": []},
        {"text_delta": 1},
        {"tool_info": []},
        {"tool_info": {"error": "PRIVATE"}},
        {"tool_info": {"error": {"type": "exit"}}},
    ],
)
def test_invalid_step_fields_are_rejected(changes):
    with pytest.raises(ParserError):
        AgyNativeParser().parse(stream(init(), step(**changes), terminal()))


def test_result_numeric_metadata_and_events_are_bounded():
    result = AgyNativeParser().parse(
        stream(
            terminal(
                response="x" * 20001,
                num_turns=-1,
                duration_seconds=True,
                usage={"input_tokens": MAX_METADATA_NUMBER + 1, "output_tokens": True, "thinking_tokens": 2},
            )
        )
    )
    assert len(result.content) == 20000
    assert result.metadata["truncated"] is True
    assert "num_turns" not in result.metadata
    assert "duration_seconds" not in result.metadata
    assert result.metadata["usage"] == {"thinking_tokens": 2}
    with pytest.raises(ParserError):
        AgyNativeParser().parse(stream(init(), *[step()] * 16384, terminal()))


def test_private_session_validation_accepts_only_safe_bounded_identity():
    assert validate_session_id("session-1") == "session-1"
    assert validate_session_id(None) is None
    for invalid in ("--unsafe", "x" * 129, "PRIVATE\nVALUE", 3):
        with pytest.raises(ParserError):
            validate_session_id(invalid)
