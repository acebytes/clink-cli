"""Native parsing keeps tool errors, terminal outcomes and private sessions distinct."""

import json

import pytest

from clink.parsers.base import ParserError
from clink.parsers.claude_native import ClaudeNativeParser


def stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def terminal(**overrides):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "session-1",
        "result": "done",
        **overrides,
    }


def test_success_after_recoverable_tool_failure_is_completed():
    result = ClaudeNativeParser().parse(
        stream(
            {"type": "system", "subtype": "init", "session_id": "session-1"},
            {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": "private"}]}},
            terminal(usage={"input_tokens": 3, "secret": "private"}, modelUsage={"claude-model": {}}),
        )
    )
    assert result.state == "completed"
    assert result.session_id == "session-1"
    assert result.metadata["tool_error_count"] == 1
    assert result.metadata["usage"] == {"input_tokens": 3}
    assert result.metadata["actual_model"] == "claude-model"
    assert "session" not in json.dumps(result.metadata)
    assert "private" not in json.dumps(result.metadata)


def test_permission_denied_success_reports_permission_needed_without_tool_input():
    result = ClaudeNativeParser().parse(
        stream(
            {
                "type": "system",
                "subtype": "permission_denied",
                "tool_name": "Bash",
                "tool_input": {"private": "secret"},
            },
            terminal(permission_denials=[{"tool_name": "Bash", "tool_input": {"private": "secret"}}]),
        )
    )
    assert result.state == "permission_needed"
    assert result.metadata["permission_denial_count"] == 1
    assert "secret" not in json.dumps(result.metadata)


def test_terminal_error_remains_failed_and_error_fields_are_not_returned():
    result = ClaudeNativeParser().parse(
        stream(terminal(subtype="error_during_execution", is_error=True, result="", errors=["private error"]))
    )
    assert result.state == "failed"
    assert result.metadata["error_code"] == "native_error"
    assert "private error" not in json.dumps(result.metadata)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "private malformed output",
        '{"type":"result","type":"result"}',
        stream({"type": "system", "subtype": "init", "session_id": "session-1"}),
        stream(terminal(), {"type": "assistant"}),
        stream({"type": "system", "session_id": "other-session"}, terminal()),
        stream(terminal(session_id="--unsafe")),
        stream(terminal(session_id=None)),
        stream(terminal(permission_denials={"private": "secret"})),
        stream(terminal(is_error="false")),
        stream(terminal(result=None)),
        stream({"type": "result", "subtype": "success", "session_id": "session-1"}),
        stream({"type": "result", "subtype": "success", "session_id": "session-1", "is_error": False}),
    ],
)
def test_invalid_or_incomplete_stream_is_rejected_without_echoing_input(output):
    with pytest.raises(ParserError) as caught:
        ClaudeNativeParser().parse(output)
    assert "private" not in str(caught.value)


def test_result_and_metadata_are_bounded():
    result = ClaudeNativeParser().parse(stream(terminal(result="x" * 20001, num_turns=-1, duration_ms=True)))
    assert len(result.content) == 20000
    assert result.metadata["truncated"] is True
    assert "num_turns" not in result.metadata
    assert "duration_ms" not in result.metadata


@pytest.mark.parametrize("markers", [{"error": {"message": "fatal"}}, {"errors": ["fatal"]}, {"status": "failed"}])
def test_success_label_does_not_override_fatal_terminal_markers(markers):
    result = ClaudeNativeParser().parse(stream(terminal(**markers)))
    assert result.state == "failed"
    assert result.metadata["error_code"] == "native_error"
