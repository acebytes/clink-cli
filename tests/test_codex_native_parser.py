"""Codex native outcomes preserve recovery, private identity and bounded telemetry."""

import json

import pytest

from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.codex_native import MAX_EVENTS, RESULT_CHARACTERS, CodexNativeParser, validate_session_id

SESSION = "019a3f08-e78b-7aba-9bcc-d2e5603adb35"
OTHER_SESSION = "019a3f08-e78b-7aba-9bcc-d2e5603adb36"


def stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def thread(**overrides):
    return {"type": "thread.started", "thread_id": SESSION, **overrides}


def terminal(**overrides):
    return {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 5}, **overrides}


def item(item_type="agent_message", *, event_type="item.completed", **fields):
    return {"type": event_type, "item": {"id": "item_0", "type": item_type, "text": "done", **fields}}


def command(**fields):
    return item(
        "command_execution",
        **{
            "command": "private command",
            "aggregated_output": "private output",
            "status": "completed",
            "exit_code": 0,
            **fields,
        },
    )


def test_native_success_returns_private_session_separately():
    result = CodexNativeParser().parse(stream(thread(), {"type": "turn.started"}, item(), terminal()))
    assert result.state == "completed"
    assert result.content == "done"
    assert result.session_id == SESSION
    assert result.metadata == {
        "tool_error_count": 0,
        "permission_denial_count": 0,
        "truncated": False,
        "usage": {"input_tokens": 3, "output_tokens": 5},
    }
    assert SESSION not in json.dumps(result.metadata)


@pytest.mark.parametrize("status,exit_code", [("failed", 1), ("completed", 2), ("failed", None), ("declined", -1)])
def test_recoverable_command_failure_keeps_success(status, exit_code):
    failed = command()
    failed["item"].update(status=status, exit_code=exit_code)
    result = CodexNativeParser().parse(stream(thread(), failed, item(id="answer"), terminal()))
    assert result.state == "completed"
    assert result.metadata["tool_error_count"] == 1
    assert "private" not in json.dumps(result.metadata)


@pytest.mark.parametrize("exit_code", [-1, None])
def test_declined_command_is_not_alone_evidence_of_a_permission_denial(exit_code):
    result = CodexNativeParser().parse(
        stream(thread(), command(status="declined", exit_code=exit_code), item(id="answer"), terminal())
    )
    assert result.state == "completed"
    assert result.metadata["tool_error_count"] == 1
    assert result.metadata["permission_denial_count"] == 0


@pytest.mark.parametrize("field", ["command", "aggregated_output", "exit_code"])
def test_declined_command_requires_the_pinned_native_fields(field):
    declined = command(status="declined", exit_code=None)
    del declined["item"][field]
    with pytest.raises(ParserError):
        CodexNativeParser().parse(stream(thread(), declined, terminal()))


@pytest.mark.parametrize("field", ["command", "aggregated_output"])
def test_declined_command_requires_textual_command_and_output(field):
    declined = command(status="declined", exit_code=None, **{field: None})
    with pytest.raises(ParserError):
        CodexNativeParser().parse(stream(thread(), declined, terminal()))


def policy_denial(command_text="synthetic-command", prefix="synthetic-command", **overrides):
    return command(
        **{
            "command": command_text,
            "aggregated_output": f"`{command_text}` rejected: policy forbids commands starting with `{prefix}`",
            "status": "declined",
            "exit_code": -1,
            **overrides,
        }
    )


def test_pinned_exec_policy_diagnostic_reports_permission_needed_after_completed_turn():
    result = CodexNativeParser().parse(
        stream(thread(), policy_denial(), item(id="answer", text="permission declined"), terminal())
    )
    assert result.state == "permission_needed"
    assert result.session_id == SESSION
    assert result.metadata["permission_denial_count"] == 1
    assert result.metadata["tool_error_count"] == 1
    assert "synthetic-command" not in json.dumps(result.metadata)
    assert "error_code" not in result.metadata


@pytest.mark.parametrize(
    "diagnostic",
    [
        "approval required by policy, but AskForApproval is set to Never",
        "approval required by policy, but AskForApproval::Granular.sandbox_approval is false",
        "approval required by policy rule, but AskForApproval::Granular.rules is false",
    ],
)
def test_pinned_command_approval_diagnostics_require_declined_status_and_native_error_exit(diagnostic):
    denied = CodexNativeParser().parse(
        stream(thread(), command(status="declined", exit_code=-1, aggregated_output=diagnostic), terminal())
    )
    assert denied.state == "permission_needed"
    assert denied.metadata["permission_denial_count"] == 1
    for fields in ({"status": "failed"}, {"exit_code": 1}, {"aggregated_output": diagnostic + "\n"}):
        details = {"status": "declined", "exit_code": -1, "aggregated_output": diagnostic, **fields}
        result = CodexNativeParser().parse(stream(thread(), command(**details), terminal()))
        assert result.state == "completed"
        assert result.metadata["permission_denial_count"] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "failed"},
        {"status": "completed"},
        {"exit_code": 1},
        {"exit_code": None},
        {"command": "different-command"},
        {"aggregated_output": "permission denied; approval required"},
        {"aggregated_output": "`synthetic-command` rejected: policy forbids commands starting with ``"},
        {"aggregated_output": "`synthetic-command` rejected: policy forbids commands starting with ` `"},
        {"aggregated_output": "`synthetic-command` rejected: policy forbids commands starting with `prefix`\n"},
        {"aggregated_output": "`synthetic-command` rejected: policy forbids commands starting with `prefix`\nprivate"},
        {"aggregated_output": "private `synthetic-command` rejected: policy forbids commands starting with `prefix`"},
    ],
)
def test_exec_policy_signature_requires_all_source_backed_conditions(overrides):
    result = CodexNativeParser().parse(stream(thread(), policy_denial(**overrides), terminal()))
    assert result.state == "completed"
    assert result.metadata["permission_denial_count"] == 0


@pytest.mark.parametrize("command_text", ["", " ", "synthetic`command", "synthetic\ncommand"])
def test_ambiguous_exec_policy_diagnostics_remain_unclassified(command_text):
    result = CodexNativeParser().parse(stream(thread(), policy_denial(command_text=command_text), terminal()))
    assert result.state == "completed"
    assert result.metadata["permission_denial_count"] == 0


@pytest.mark.parametrize(
    "diagnostic",
    [
        "MCP tool call requires approval, but approval policy is never",
        "MCP tool call cancelled: interactive approval is unavailable in exec mode",
    ],
)
def test_pinned_failed_mcp_approval_diagnostic_reports_permission_needed(diagnostic):
    result = CodexNativeParser().parse(
        stream(
            thread(),
            item(
                "mcp_tool_call",
                server="private",
                tool="private",
                status="failed",
                error={"message": diagnostic},
                result=None,
            ),
            terminal(),
        )
    )
    assert result.state == "permission_needed"
    assert result.metadata["permission_denial_count"] == 1
    assert result.metadata["tool_error_count"] == 1
    assert "private" not in json.dumps(result.metadata)
    assert diagnostic not in json.dumps(result.metadata)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "completed", "result": None},
        {"result": {}},
        {"error": {"message": "permission denied; approval required"}, "result": None},
        {"error": {"message": "MCP tool call requires approval, but approval policy is never\n"}, "result": None},
        {},
    ],
)
def test_mcp_permission_diagnostic_requires_failed_status_exact_message_and_explicit_null_result(fields):
    evidence = {
        "status": "failed",
        "error": {"message": "MCP tool call requires approval, but approval policy is never"},
        **fields,
    }
    result = CodexNativeParser().parse(
        stream(thread(), item("mcp_tool_call", server="private", tool="private", **evidence), terminal())
    )
    assert result.state == "completed"
    assert result.metadata["permission_denial_count"] == 0


def test_native_denial_diagnostics_are_not_inferred_from_other_prose_fields():
    diagnostic = policy_denial()["item"]["aggregated_output"]
    result = CodexNativeParser().parse(
        stream(
            thread(),
            command(status="failed", exit_code=1, aggregated_output=diagnostic),
            item(id="answer", text=diagnostic),
            terminal(),
        ),
        "MCP tool call requires approval, but approval policy is never",
    )
    assert result.state == "completed"
    assert result.metadata["permission_denial_count"] == 0


def test_denial_counts_only_completed_items_and_survives_turn_failure():
    started = policy_denial(event_type="item.started")
    result = CodexNativeParser().parse(
        stream(
            thread(),
            started,
            policy_denial(),
            policy_denial(id="other"),
            {"type": "turn.failed", "error": {"message": "private error"}},
        )
    )
    assert result.state == "permission_needed"
    assert result.metadata["permission_denial_count"] == 2
    assert result.metadata["tool_error_count"] == 2
    assert "error_code" not in result.metadata


def test_recoverable_mcp_and_other_native_tool_failures_are_counted_once():
    mcp = item(
        "mcp_tool_call",
        id="mcp",
        server="private server",
        tool="private tool",
        arguments={"secret": "private argument"},
        error={"message": "private error"},
        result={"content": [{"text": "private result"}]},
        status="failed",
    )
    started = {**mcp, "type": "item.started", "item": {**mcp["item"], "status": "in_progress", "error": None}}
    result = CodexNativeParser().parse(
        stream(
            thread(),
            started,
            {**started, "type": "item.updated"},
            mcp,
            item("file_change", id="file", status="failed", changes=[]),
            item("collab_tool_call", id="collab", status="failed"),
            item("error", id="warning", message="private recoverable warning"),
            item(id="answer"),
            terminal(),
        )
    )
    assert result.state == "completed"
    assert result.metadata["tool_error_count"] == 4
    assert "private" not in json.dumps(result.metadata)


def test_native_file_change_in_progress_lifecycle_is_supported():
    result = CodexNativeParser().parse(
        stream(
            thread(),
            item("file_change", event_type="item.started", status="in_progress", changes=[]),
            item("file_change", event_type="item.updated", status="in_progress", changes=[]),
            item("file_change", status="completed", changes=[]),
            terminal(),
        )
    )
    assert result.state == "completed"
    assert result.metadata["tool_error_count"] == 0
    with pytest.raises(ParserError):
        CodexNativeParser().parse(stream(thread(), item("file_change", status="in_progress", changes=[]), terminal()))


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "turn.failed", "error": {"message": "private fatal"}}],
        [{"type": "error", "message": "private fatal"}],
        [
            {"type": "error", "message": "private fatal"},
            {"type": "turn.failed", "error": {"message": "private fatal"}},
        ],
    ],
)
def test_fatal_outcomes_are_failed_without_returning_diagnostics(events):
    result = CodexNativeParser().parse(stream(*events))
    assert result.state == "failed"
    assert result.content == ""
    assert result.session_id is None
    assert result.metadata["error_code"] == "native_error"
    assert "private" not in json.dumps(result.metadata)


def test_terminal_failure_classifies_only_known_error_fields():
    result = CodexNativeParser().parse(
        stream(thread(), {"type": "turn.failed", "error": {"message": "authentication required: private"}})
    )
    assert result.metadata["error_code"] == "authentication_required"
    assert "private" not in json.dumps(result.metadata)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "private malformed output",
        "[]",
        "null",
        '{"type":"turn.completed","usage":{"input_tokens":NaN}}',
        '{"type":"thread.started","thread_id":"a","thread_id":"b"}',
        '{"type":"item.completed","item":{"id":"a","id":"b","type":"agent_message","text":"private"}}',
        stream({"type": []}),
        stream({"type": "result", "subtype": "success", "result": "private"}),
        stream({"type": "approval_requested", "tool_input": "private"}),
        stream(thread()),
        stream(thread(), item()),
        stream(terminal()),
        stream(thread(thread_id=None), terminal()),
        stream({"type": "thread.started"}, terminal()),
        stream(thread(thread_id="private-session"), terminal()),
        stream(thread(), thread(thread_id=OTHER_SESSION), terminal()),
        stream(thread(), {"type": "turn.started", "thread_id": []}, terminal()),
        stream(thread(), {"type": "item.completed", "item": []}, terminal()),
        stream(thread(), item(item_type=[]), terminal()),
        stream(thread(), item(item_type="unsupported"), terminal()),
        stream(thread(), item(id=None), terminal()),
        stream(thread(), item(text={"private": "body"}), terminal()),
        stream(thread(), command(status=True), terminal()),
        stream(thread(), command(exit_code=True), terminal()),
        stream(thread(), item("mcp_tool_call", server="s", tool="t", status="failed", error="private"), terminal()),
        stream(thread(), item("mcp_tool_call", server="s", tool="t", status="failed", result=[]), terminal()),
        stream(thread(), terminal(usage=[])),
        stream(thread(), terminal(usage=None)),
        stream({"type": "error", "message": {"private": "body"}}),
        stream({"type": "turn.failed", "error": "private"}),
    ],
)
def test_invalid_or_incomplete_stream_is_rejected_without_echoing_input(output):
    with pytest.raises(ParserError) as caught:
        CodexNativeParser().parse(output)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "events",
    [
        [terminal(), terminal()],
        [terminal(), {"type": "turn.started"}],
        [terminal(), {"type": "turn.failed", "error": {"message": "private"}}],
        [{"type": "turn.failed", "error": {"message": "private"}}, terminal()],
        [{"type": "error", "message": "private"}, terminal()],
        [{"type": "error", "message": "private"}, {"type": "error", "message": "private"}],
        [{"type": "error", "message": "private"}, {"type": "turn.started"}],
        [item(), item()],
        [item(event_type="item.started"), item("reasoning")],
        [terminal(status="failed")],
        [terminal(error={"message": "private"})],
        [terminal(errors=["private"])],
        [terminal(is_error=True)],
    ],
)
def test_duplicate_and_contradictory_lifecycles_are_rejected(events):
    with pytest.raises(ParserError):
        CodexNativeParser().parse(stream(thread(), *events))


@pytest.mark.parametrize("value", ["", "--unsafe", "a" * 36, "0" * 32, "{" + SESSION + "}", [], 7, True])
def test_session_validator_rejects_noncanonical_uuid_shapes(value):
    with pytest.raises(ParserError):
        validate_session_id(value)


def test_session_validator_accepts_initial_none_and_uuid():
    assert validate_session_id(None) is None
    assert validate_session_id(SESSION) == SESSION


def test_result_is_bounded_across_multiple_messages_and_still_validates_tail():
    result = CodexNativeParser().parse(
        stream(thread(), item(text="x" * (RESULT_CHARACTERS - 1)), item(id="next", text="y" * 100), terminal())
    )
    assert result.content == "x" * (RESULT_CHARACTERS - 1) + "\n"
    assert result.metadata["truncated"] is True
    with pytest.raises(ParserError):
        CodexNativeParser().parse(
            stream(thread(), item(text="x" * (RESULT_CHARACTERS + 1)), terminal(), {"type": "turn.started"})
        )


@pytest.mark.parametrize("size", [0, RESULT_CHARACTERS, RESULT_CHARACTERS + 1])
def test_truncation_exact_boundary_and_empty_success(size):
    result = CodexNativeParser().parse(stream(thread(), item(text="x" * size), terminal()))
    assert result.content == "x" * min(size, RESULT_CHARACTERS)
    assert result.metadata["truncated"] is (size > RESULT_CHARACTERS)


def test_public_metadata_is_allowlisted_and_numerically_bounded():
    result = CodexNativeParser().parse(
        stream(
            thread(),
            item("web_search", id="search", results=[{"url": "https://private.example/?secret=private"}]),
            item(),
            terminal(
                usage={
                    "input_tokens": MAX_METADATA_NUMBER,
                    "cached_input_tokens": True,
                    "cache_write_input_tokens": -1,
                    "output_tokens": MAX_METADATA_NUMBER + 1,
                    "total_tokens": 3.5,
                    "reasoning_output_tokens": 2,
                    "private": "private secret",
                },
                model="private model",
                session_id="private session",
                extra={"private": "private details"},
            ),
        )
    )
    assert result.metadata["usage"] == {"input_tokens": MAX_METADATA_NUMBER, "reasoning_output_tokens": 2}
    assert "private" not in json.dumps(result.metadata)
    assert len(json.dumps(result.metadata)) < 512


def test_installed_flattened_web_search_id_exception_is_supported():
    search = '{"type":"item.completed","item":{"id":"outer","id":"inner","type":"web_search"}}'
    result = CodexNativeParser().parse(stream(thread()) + "\n" + search + "\n" + stream(terminal()))
    assert result.state == "completed"
    assert "inner" not in json.dumps(result.metadata)


def test_event_limit_counts_nonempty_lines_and_accepts_exact_boundary():
    events = [thread()] + [{"type": "turn.started"}] * (MAX_EVENTS - 2) + [terminal()]
    assert CodexNativeParser().parse("\n\n" + stream(*events)).state == "completed"
    with pytest.raises(ParserError):
        CodexNativeParser().parse(stream(thread(), *events))


def test_permission_prose_is_never_permission_classification():
    prose = "Permission denied; approval required; please allow this command."
    failed_command = command()
    failed_command["item"].update(aggregated_output=prose, status="failed", exit_code=1)
    result = CodexNativeParser().parse(stream(thread(), failed_command, item(id="answer", text=prose), terminal()))
    assert result.state == "completed"
    assert result.content == prose
    assert result.metadata["permission_denial_count"] == 0
    failure = CodexNativeParser().parse(stream({"type": "turn.failed", "error": {"message": prose}}))
    assert failure.state == "failed"
    assert failure.metadata["error_code"] == "native_error"
