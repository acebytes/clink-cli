"""Project only pinned structured refusal categories; never native payloads."""

import json
from copy import deepcopy

import pytest

from clink.runner import ProcessOutput
from scripts import native_denial_diagnostics as diagnostic
from scripts import verify_native_adapters as verifier

PRIVATE = "PRIVATE_NATIVE_ID_PROMPT_COMMAND_ARGUMENT_VALUE"
SESSION = "5231ee56-9b1a-4b5d-9153-aab94402f39f"
TEST_COMMAND = "/synthetic/python fixture.py"
PERMISSION_COMMAND = "/usr/bin/printf CLINK_NATIVE_POLICY_CANARY"


def codex_stream(*items, prose=PRIVATE):
    return "\n".join(
        json.dumps(event)
        for event in [
            {"type": "thread.started", "thread_id": SESSION},
            {"type": "turn.started"},
            *[{"type": "item.completed", "item": {"id": str(number), **item}} for number, item in enumerate(items)],
            {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": prose}},
            {"type": "turn.completed", "usage": {}},
        ]
    )


def command(message, *, text=PRIVATE, **changes):
    return {
        "type": "command_execution",
        "command": text,
        "status": "declined",
        "exit_code": -1,
        "aggregated_output": message,
        **changes,
    }


def mcp(message, **changes):
    return {
        "type": "mcp_tool_call",
        "server": "native_fixture",
        "tool": "canary",
        "arguments": {},
        "status": "failed",
        "error": {"message": message},
        "result": None,
        **changes,
    }


def agy_stream(denials, *, tool_error=False):
    events = [{"event": "init", "conversation_id": PRIVATE, "init": {}}]
    if tool_error:
        events.append(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": PRIVATE,
                    "step_index": 0,
                    "step_type": "TOOL",
                    "state": "DONE",
                    "tool_info": {"error": {"type": PRIVATE, "message": PRIVATE}},
                },
            }
        )
    events.append(
        {
            "event": "result",
            "result": {
                "conversation_id": PRIVATE,
                "status": "SUCCESS",
                "response": PRIVATE,
                "denied_actions": denials,
            },
        }
    )
    return "\n".join(json.dumps(event) for event in events)


@pytest.mark.parametrize(
    "message,reason",
    [
        ("approval required by policy, but AskForApproval is set to Never", "command_approval_never"),
        (
            "approval required by policy, but AskForApproval::Granular.sandbox_approval is false",
            "command_sandbox_approval_disabled",
        ),
        (
            "approval required by policy rule, but AskForApproval::Granular.rules is false",
            "command_rules_approval_disabled",
        ),
    ],
)
def test_exact_command_approval_signatures_are_fixed_and_unknown_commands_stay_unknown(message, reason):
    result = diagnostic.native_denial_diagnostic("codex", codex_stream(command(message)))
    assert result["parser_valid"] is True
    assert result["permission_denial_count"] == result["tool_error_count"] == 1
    assert result["denials"] == [
        {
            "tool_category": "command",
            "reason_code": reason,
            "fixture_operation": "unknown",
            "signature": "codex_0_166_0_" + reason,
        }
    ]
    assert diagnostic.valid_native_denial_diagnostic(result, "codex")
    assert PRIVATE not in json.dumps(result)
    assert SESSION not in json.dumps(result)


@pytest.mark.parametrize(
    "text,operation",
    [
        (TEST_COMMAND, "fixture_test"),
        (TEST_COMMAND + " cancel", "fixture_cancel"),
        (PERMISSION_COMMAND, "permission_canary"),
        (TEST_COMMAND + " && echo extra", "unknown"),
    ],
)
def test_exec_policy_signature_binds_command_and_exact_fixture_operation(text, operation):
    message = f"`{text}` rejected: policy forbids commands starting with `{PRIVATE}`"
    result = diagnostic.native_denial_diagnostic(
        "codex",
        codex_stream(command(message, text=text)),
        test_command=TEST_COMMAND,
        permission_command=PERMISSION_COMMAND,
    )
    assert result["denials"][0]["reason_code"] == "exec_policy_forbidden"
    assert result["denials"][0]["fixture_operation"] == operation
    encoded = json.dumps(result)
    assert text not in encoded
    assert PRIVATE not in encoded


@pytest.mark.parametrize(
    "message,reason",
    [
        ("MCP tool call requires approval, but approval policy is never", "mcp_approval_never"),
        (
            "MCP tool call cancelled: interactive approval is unavailable in exec mode",
            "mcp_interactive_approval_unavailable",
        ),
    ],
)
def test_exact_mcp_signatures_bind_the_pinned_empty_argument_canary(message, reason):
    result = diagnostic.native_denial_diagnostic("codex", codex_stream(mcp(message)))
    assert result["denials"] == [
        {
            "tool_category": "mcp",
            "reason_code": reason,
            "fixture_operation": "mcp_canary",
            "signature": "codex_0_166_0_" + reason,
        }
    ]
    assert diagnostic.valid_native_denial_diagnostic(result, "codex")


@pytest.mark.parametrize(
    "changes",
    [
        {"server": PRIVATE},
        {"tool": PRIVATE},
        {"arguments": None},
        {"arguments": {PRIVATE: PRIVATE}},
        {"arguments": "{}"},
    ],
)
def test_mcp_fixture_identity_and_argument_shape_are_not_guessed(changes):
    result = diagnostic.native_denial_diagnostic(
        "codex", codex_stream(mcp("MCP tool call requires approval, but approval policy is never", **changes))
    )
    assert result["denials"][0]["fixture_operation"] == "unknown"
    assert PRIVATE not in json.dumps(result)


def test_missing_mcp_arguments_are_unknown_even_though_native_parser_allows_compatibility():
    item = mcp("MCP tool call requires approval, but approval policy is never")
    del item["arguments"]
    result = diagnostic.native_denial_diagnostic("codex", codex_stream(item))
    assert result["denials"][0]["fixture_operation"] == "unknown"


@pytest.mark.parametrize(
    "item",
    [
        command("ordinary setup failure"),
        command("approval required by policy, but AskForApproval is set to Never", status="failed"),
        command("approval required by policy, but AskForApproval is set to Never", exit_code=1),
        command("`different` rejected: policy forbids commands starting with `prefix`"),
        mcp("MCP tool call requires approval, but approval policy is never", result={}),
        mcp("ordinary setup failure"),
    ],
)
def test_generic_failed_tools_and_wrong_denial_shapes_remain_unclassified(item):
    result = diagnostic.native_denial_diagnostic("codex", codex_stream(item))
    assert result["parser_valid"] is True
    assert result["tool_error_count"] == 1
    assert result["permission_denial_count"] == 0
    assert result["denials"] == []


def test_assistant_prose_and_incomplete_items_are_not_permission_evidence():
    prose = "MCP tool call requires approval, but approval policy is never"
    result = diagnostic.native_denial_diagnostic("codex", codex_stream(prose=prose))
    assert result["permission_denial_count"] == result["tool_error_count"] == 0
    events = [json.loads(line) for line in codex_stream(mcp(prose)).splitlines()]
    events[2]["type"] = "item.started"
    result = diagnostic.native_denial_diagnostic("codex", "\n".join(json.dumps(event) for event in events))
    assert result["denials"] == []


def test_agy_free_form_action_and_display_never_identify_a_tool_or_fixture_operation():
    denials = [{"action": value, "display_name": PRIVATE} for value in (PRIVATE, TEST_COMMAND, "call_mcp_tool")]
    result = diagnostic.native_denial_diagnostic("agy", agy_stream(denials, tool_error=True), test_command=TEST_COMMAND)
    assert result["parser_valid"] is True
    assert result["permission_denial_count"] == 3
    assert result["tool_error_count"] == 1
    assert all(
        entry
        == {
            "tool_category": "unknown",
            "reason_code": "agy_denied_action",
            "fixture_operation": "unknown",
            "signature": "agy_1_4_12_denied_action",
        }
        for entry in result["denials"]
    )
    assert diagnostic.valid_native_denial_diagnostic(result, "agy")
    assert PRIVATE not in json.dumps(result)
    assert TEST_COMMAND not in json.dumps(result)


@pytest.mark.parametrize("adapter", ["agy", "codex"])
def test_denial_detail_list_is_capped_while_bounded_native_counts_remain_exact(adapter):
    count = diagnostic.MAX_DENIALS + 3
    stdout = (
        agy_stream([{"display_name": PRIVATE}] * count)
        if adapter == "agy"
        else codex_stream(*[mcp("MCP tool call requires approval, but approval policy is never") for _ in range(count)])
    )
    result = diagnostic.native_denial_diagnostic(adapter, stdout)
    assert result["permission_denial_count"] == count
    assert len(result["denials"]) == diagnostic.MAX_DENIALS
    assert result["truncated"] is True
    assert diagnostic.valid_native_denial_diagnostic(result, adapter)


@pytest.mark.parametrize("adapter", ["agy", "codex"])
@pytest.mark.parametrize("stdout", ["", PRIVATE, '{"duplicate":1,"duplicate":2}', "[]"])
def test_malformed_native_output_never_produces_partial_denial_classification(adapter, stdout):
    result = diagnostic.native_denial_diagnostic(adapter, stdout)
    assert result == {
        "schema_version": 1,
        "parser_valid": False,
        "permission_denial_count": 0,
        "tool_error_count": 0,
        "denials": [],
        "truncated": False,
    }
    assert diagnostic.valid_native_denial_diagnostic(result, adapter)


def test_malformed_suffix_and_overflow_discard_preceding_valid_denials(monkeypatch):
    stdout = codex_stream(mcp("MCP tool call requires approval, but approval policy is never"))
    assert diagnostic.native_denial_diagnostic("codex", stdout + "\n" + PRIVATE)["parser_valid"] is False
    monkeypatch.setattr(diagnostic, "MAX_BYTES", 128)
    result = diagnostic.native_denial_diagnostic("codex", stdout)
    assert result["parser_valid"] is False
    assert result["denials"] == []


def prior(tmp_path, monkeypatch, value=None, *, adapter="codex"):
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    path = tmp_path / "docs/verification/prior.json"
    path.parent.mkdir(parents=True)
    entry = {"ordinal": 1, "check": "edit", "passed": False}
    if value is not None:
        entry["native_denial_diagnostic"] = value
    path.write_text(
        json.dumps(
            {
                "scope": verifier.SCOPE,
                "adapter": adapter,
                "submission_ledger": [entry],
                "submissions_consumed_total": 1,
                "submission_allowance_total": 4,
            }
        )
    )
    return path, entry


@pytest.mark.parametrize("with_diagnostic", [False, True])
def test_old_and_new_prior_receipts_preserve_bytes_counters_and_failure_history(tmp_path, monkeypatch, with_diagnostic):
    value = (
        diagnostic.native_denial_diagnostic(
            "codex", codex_stream(mcp("MCP tool call requires approval, but approval policy is never"))
        )
        if with_diagnostic
        else None
    )
    path, entry = prior(tmp_path, monkeypatch, value)
    before = path.read_bytes()
    ledger, allowance, previous_hash = verifier.load_history(path, "codex")
    assert ledger == [entry]
    assert allowance == 4
    assert previous_hash == verifier.digest(before)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"parser_valid": "true"},
        {"permission_denial_count": True},
        {"permission_denial_count": diagnostic.MAX_EVENTS + 1},
        {"tool_error_count": -1},
        {"truncated": True},
        {"raw_output": PRIVATE},
        {"denial": {"reason_code": PRIVATE}},
        {"denial": {"signature": "sha256:" + "a" * 64}},
        {"denial": {"tool_category": "command"}},
        {"denial": {"fixture_operation": PRIVATE}},
        {"denial": {"raw_command": PRIVATE}},
        {"parser_valid": False},
    ],
)
def test_prior_schema_rejects_arbitrary_fields_tokens_hashes_and_inconsistent_types(tmp_path, monkeypatch, change):
    value = deepcopy(
        diagnostic.native_denial_diagnostic(
            "codex", codex_stream(mcp("MCP tool call requires approval, but approval policy is never"))
        )
    )
    for key, part in change.items():
        if key == "denial":
            value["denials"][0].update(part)
        else:
            value[key] = part
    path, _ = prior(tmp_path, monkeypatch, value)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="technical native denial diagnostic") as caught:
        verifier.load_history(path, "codex")
    assert PRIVATE not in str(caught.value)
    assert path.read_bytes() == before


def test_cross_adapter_signature_is_rejected(tmp_path, monkeypatch):
    value = diagnostic.native_denial_diagnostic(
        "codex", codex_stream(mcp("MCP tool call requires approval, but approval policy is never"))
    )
    path, _ = prior(tmp_path, monkeypatch, value, adapter="agy")
    with pytest.raises(ValueError, match="technical native denial diagnostic"):
        verifier.load_history(path, "agy")


@pytest.mark.asyncio
async def test_pool_preserves_output_identity_and_state_contract_without_stderr_disclosure(tmp_path, monkeypatch):
    stdout = codex_stream(mcp("MCP tool call requires approval, but approval policy is never"))
    output = ProcessOutput(stdout, PRIVATE, 0, len(stdout), len(PRIVATE), 0)

    async def execute(*args, **kwargs):
        kwargs["on_started"]()
        return output

    monkeypatch.setattr(verifier.ProcessPool, "execute", execute)
    evidence = {"adapter": "codex", "submission_ledger": [], "submission_allowance_total": 4}
    path = tmp_path / "receipt.json"
    pool = verifier.VerificationPool(evidence, path)
    assert await pool.execute(("synthetic-not-executed",)) is output
    entry = evidence["submission_ledger"][0]
    assert entry["native_denial_diagnostic"]["denials"][0]["reason_code"] == "mcp_approval_never"
    assert entry["reason"] == "exited"
    assert "state" not in entry
    assert evidence["submissions_consumed_total"] == 1
    encoded = path.read_text()
    assert PRIVATE not in encoded and SESSION not in encoded
    assert "MCP tool call requires approval" not in encoded
