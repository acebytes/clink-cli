"""Separate Claude generation contracts, terminal parsing and synthetic supervision."""

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from clink.generation_contracts import OUTPUT_BYTES, GenerationInputError, canonical_json, parse_request, sha256
from clink.native_generation import NativeGenerationService, compose_native_prompt
from clink.native_generation_claude_policy import (
    DEFAULT_CLAUDE_NATIVE_ADMISSION,
    claude_generation_arguments,
    claude_generation_recipe_digest,
)
from clink.native_generation_contracts import parse_native_request
from clink.native_generation_policy import (
    DEFAULT_NATIVE_ADMISSION,
    NativeGenerationAdmission,
    native_generation_recipe_digest,
)
from clink.native_policy import native_recipe_digest
from clink.parsers.base import MAX_METADATA_NUMBER, ParserError
from clink.parsers.claude_native_generation import ClaudeNativeGenerationParser, parser_contract_digest
from clink.parsers.codex_native_generation import parser_contract_digest as codex_parser_digest
from clink.policy import executable_digest

MODEL = "claude-opus-5-5"
SESSION = "66f96fc9-969d-4c0e-ab92-e355fa0e7588"
FINAL = "  final café 🦉\r\n"


def body(project, **changes):
    return {
        "schema_version": 2,
        "execution_mode": "native",
        "project_dir": str(project),
        "request_id": SESSION,
        "cli": "claude",
        "model": MODEL,
        "instructions": "  PRIVATE instructions\r\n",
        "input": " PRIVATE source café 🦉\n",
        "timeout_seconds": 5,
        **changes,
    }


def events(text=FINAL):
    return [
        {"type": "system", "subtype": "init", "session_id": SESSION, "model": MODEL},
        {
            "type": "assistant",
            "session_id": SESSION,
            "message": {"content": [{"type": "text", "text": "PRIVATE intermediate"}]},
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": SESSION,
            "result": text,
            "permission_denials": [],
            "modelUsage": {"claude-haiku-4-5": {"inputTokens": 10}},
        },
    ]


def stream(records):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in records) + "\n"


def test_v2_claude_preserves_strings_and_independent_wire_hash_without_changing_v1(tmp_path):
    wire = body(tmp_path)
    request = parse_native_request(canonical_json(wire))
    effective = {key: value for key, value in wire.items() if key not in {"request_id", "timeout_seconds"}}
    effective["timeout_ms"] = 5000
    independent = json.dumps(effective, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert request.cli == "claude" and request.model == MODEL
    assert request.instructions == wire["instructions"] and request.input == wire["input"]
    assert request.request_sha256 == sha256(independent)
    v1 = {key: value for key, value in wire.items() if key not in {"execution_mode", "project_dir"}}
    with pytest.raises(GenerationInputError, match="only cli codex"):
        parse_request(canonical_json({**v1, "schema_version": 1}))


@pytest.mark.parametrize("model", ["auto", "default", "opus", "sonnet", "haiku", "claude-opus", "gpt-6.1-sol"])
def test_claude_model_selection_refuses_aliases_without_mapping(tmp_path, model):
    with pytest.raises(GenerationInputError):
        parse_native_request(canonical_json(body(tmp_path, model=model)))


def test_parser_publishes_only_exact_terminal_result_and_primary_init_model():
    parsed = ClaudeNativeGenerationParser().parse(stream(events()))
    assert parsed.content == FINAL
    assert parsed.metadata["actual_model"] == MODEL
    assert SESSION not in json.dumps(parsed.metadata) and "PRIVATE" not in json.dumps(parsed.metadata)
    assert codex_parser_digest() == "1af1e8e2ffb57385748cf2e79c36b24b43ffc2b59ad7b7511f18d28a1272d42d"
    assert native_generation_recipe_digest() == "26461116c00265235af55b5abf8a136b7178f5a3a697ce4d3f93508018840c73"


@pytest.mark.parametrize(
    "case,code",
    [
        ("no_init", "protocol_error"),
        ("duplicate_init", "protocol_error"),
        ("no_terminal", "protocol_error"),
        ("session_changed", "protocol_error"),
        ("missing_session", "protocol_error"),
        ("missing_model", "protocol_error"),
        ("post_terminal", "protocol_error"),
        ("unknown_event", "protocol_error"),
        ("malformed_message", "protocol_error"),
        ("failed", "protocol_error"),
        ("error_flag", "protocol_error"),
        ("tool_error", "protocol_error"),
        ("denial", "permission_needed"),
        ("failed_denial", "permission_needed"),
        ("stream_denial", "permission_needed"),
        ("malformed_denials", "protocol_error"),
        ("nontext", "protocol_error"),
        ("blank", "empty_response"),
        ("authentication", "authentication_required"),
        ("rate_limit", "rate_limited"),
    ],
)
def test_parser_failure_matrix_never_returns_intermediate_text(case, code):
    records = events()
    if case == "no_init":
        records.pop(0)
    elif case == "duplicate_init":
        records.insert(1, records[0].copy())
    elif case == "no_terminal":
        records.pop()
    elif case == "session_changed":
        records[-1]["session_id"] = "another-session"
    elif case == "missing_session":
        records[-1].pop("session_id")
    elif case == "missing_model":
        records[0].pop("model")
    elif case == "post_terminal":
        records.append(records[1])
    elif case == "unknown_event":
        records.insert(1, {"type": "future_event"})
    elif case == "malformed_message":
        records[1]["message"] = []
    elif case == "failed":
        records[-1]["subtype"] = "error_max_turns"
    elif case == "error_flag":
        records[-1]["is_error"] = 1
    elif case == "tool_error":
        records[1]["message"]["content"] = [{"type": "tool_result", "is_error": True}]
    elif case in {"denial", "failed_denial"}:
        records[-1]["permission_denials"] = [{"tool_name": "Read"}]
        if case == "failed_denial":
            records[-1].update(is_error=True, subtype="error_during_execution")
    elif case == "stream_denial":
        records.insert(1, {"type": "system", "subtype": "permission_denied", "session_id": SESSION})
    elif case == "malformed_denials":
        records[-1]["permission_denials"] = {}
    elif case == "nontext":
        records[-1]["result"] = []
    elif case == "blank":
        records[-1]["result"] = " \r\n"
    elif case in {"authentication", "rate_limit"}:
        records[-1].update(
            is_error=True,
            subtype="error_during_execution",
            errors=["authentication_required" if case == "authentication" else "rate_limit_exceeded"],
        )
    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse(stream(records))
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "raw", ['{"type":"system","type":"result"}', '{"type":"result","result":NaN}', "{", "[]", '"text"']
)
def test_parser_rejects_ambiguous_or_malformed_json(raw):
    with pytest.raises(ParserError, match="protocol"):
        ClaudeNativeGenerationParser().parse(raw)


@pytest.mark.parametrize(
    "location,number,accepted",
    [
        ("top_level", "1e309", False),
        ("nested", "-1e309", False),
        ("top_level", "1e308", True),
        ("nested", "-1e308", True),
    ],
)
def test_parser_rejects_overflowing_json_numbers_without_publishing_final_text(location, number, accepted):
    records = events()
    records[-1]["telemetry"] = "NUMERIC_TOKEN" if location == "top_level" else {"usage": [{"value": "NUMERIC_TOKEN"}]}
    raw = stream(records).replace('"NUMERIC_TOKEN"', number)
    if accepted:
        assert ClaudeNativeGenerationParser().parse(raw).content == FINAL
    else:
        with pytest.raises(ParserError) as caught:
            ClaudeNativeGenerationParser().parse(raw)
        assert caught.value.code == "protocol_error" and FINAL not in str(caught.value)


def native_notice(subtype, **changes):
    payloads = {
        "commands_changed": {
            "commands": [
                {
                    "name": "PRIVATE command",
                    "description": "PRIVATE description",
                    "argumentHint": "PRIVATE hint",
                    "aliases": ["PRIVATE alias"],
                    "builtin": False,
                    "plugin": value,
                }
                for value in ({"opaque": [None, True]}, "PRIVATE plugin", None, [1, "opaque"])
            ]
        },
        "session_title_changed": {"title": "PRIVATE title"},
        "informational": {
            "content": "PRIVATE informational text",
            "level": "notice",
            "tool_use_id": "PRIVATE tool identity",
            "prevent_continuation": False,
        },
        "instruction_size_warning": {
            "total_chars": MAX_METADATA_NUMBER,
            "total_limit_chars": 0,
            "file_count": 0,
            "largest_chars": MAX_METADATA_NUMBER,
        },
        "thinking_tokens": {"estimated_tokens": 8, "estimated_tokens_delta": 3},
        "api_retry": {
            "attempt": 3,
            "max_retries": 0,
            "retry_delay_ms": 0.5,
            "error_status": None,
            "error": "rate_limit",
            "no_response": {"waited_ms": 0, "retry_wait_ms": 1.5},
        },
    }
    return {"type": "system", "subtype": subtype, "session_id": SESSION, **payloads[subtype], **changes}


@pytest.mark.parametrize(
    "subtype,changes",
    [
        ("commands_changed", {}),
        ("session_title_changed", {}),
        ("instruction_size_warning", {}),
        *[("informational", {"level": level}) for level in ("info", "notice", "suggestion", "warning")],
        *[
            ("api_retry", {"error": error})
            for error in (
                "overloaded",
                "rate_limit",
                "authentication_failed",
                "server_error",
                "cloud_credential_error",
                "unknown",
            )
        ],
        pytest.param("api_retry", {"error_status": 0}, id="error_status_zero_accepted"),
        pytest.param("api_retry", {"error_status": MAX_METADATA_NUMBER}, id="error_status_max_accepted"),
    ],
)
def test_native_notice_compatibility_preserves_only_final_text_and_init_model(subtype, changes):
    before = native_notice(subtype, **changes)
    before.update(model="PRIVATE metadata model", result="PRIVATE metadata result")
    after = native_notice(subtype, **changes)
    for optional in ("aliases", "builtin", "plugin"):
        if subtype == "commands_changed":
            after["commands"][0].pop(optional)
    for optional in ("tool_use_id", "prevent_continuation", "largest_chars", "no_response"):
        after.pop(optional, None)
    if subtype == "api_retry":
        after["error_status"] = 429
    records = events()
    records.insert(0, before)
    records.insert(-1, after)
    parsed = ClaudeNativeGenerationParser().parse(stream(records))
    assert parsed.content == FINAL
    assert parsed.metadata == {"actual_model": MODEL, "tool_error_count": 0, "permission_denial_count": 0}
    assert "PRIVATE" not in json.dumps(parsed.metadata) and SESSION not in json.dumps(parsed.metadata)


@pytest.mark.parametrize(
    "subtype,changes",
    [
        ("session_title_changed", {"title": " "}),
        ("session_title_changed", {"title": 1}),
        ("commands_changed", {"commands": None}),
        ("commands_changed", {"commands": [None]}),
        ("commands_changed", {"commands": [{"name": "PRIVATE", "description": "PRIVATE"}]}),
        ("commands_changed", {"commands": [{"name": 1, "description": "PRIVATE", "argumentHint": ""}]}),
        ("commands_changed", {"commands": [{"name": "PRIVATE", "description": None, "argumentHint": ""}]}),
        ("commands_changed", {"commands": [{"name": "PRIVATE", "description": "PRIVATE", "argumentHint": False}]}),
        ("commands_changed", {"commands": [{"name": "", "description": "", "argumentHint": "", "aliases": "x"}]}),
        ("commands_changed", {"commands": [{"name": "", "description": "", "argumentHint": "", "aliases": [1]}]}),
        ("commands_changed", {"commands": [{"name": "", "description": "", "argumentHint": "", "builtin": 0}]}),
        ("informational", {"content": None}),
        ("informational", {"level": "error"}),
        ("informational", {"tool_use_id": 1}),
        ("informational", {"prevent_continuation": 0}),
        ("instruction_size_warning", {"total_chars": True}),
        ("instruction_size_warning", {"total_limit_chars": -1}),
        ("instruction_size_warning", {"file_count": 1.5}),
        ("instruction_size_warning", {"largest_chars": MAX_METADATA_NUMBER + 1}),
        ("api_retry", {"attempt": 0}),
        ("api_retry", {"max_retries": True}),
        ("api_retry", {"retry_delay_ms": -1}),
        ("api_retry", {"retry_delay_ms": MAX_METADATA_NUMBER + 1}),
        ("api_retry", {"error_status": True}),
        ("api_retry", {"error_status": -1}),
        ("api_retry", {"error_status": MAX_METADATA_NUMBER + 1}),
        ("api_retry", {"error": "PRIVATE unrecognized error"}),
        ("api_retry", {"no_response": []}),
        ("api_retry", {"no_response": {}}),
        ("api_retry", {"no_response": {"waited_ms": -1, "retry_wait_ms": 0}}),
        ("api_retry", {"no_response": {"waited_ms": 0, "retry_wait_ms": "PRIVATE"}}),
    ],
)
def test_native_notice_strict_fields_have_bounded_private_diagnostics(subtype, changes):
    from clink.parsers.claude_native_generation import protocol_diagnostic

    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse(stream([native_notice(subtype, **changes), *events()]))
    assert caught.value.code == "protocol_error"
    assert (
        str(caught.value) == "Claude native generation output violates its protocol [reason=notice_shape;event_index=1]"
    )
    assert protocol_diagnostic(str(caught.value)) == {"reason": "notice_shape", "event_index": 1}


@pytest.mark.parametrize(
    "changes", [{"prevent_continuation": True}, {"prompt_submit_outcome": None}, {"prompt_submit_outcome": {}}]
)
def test_native_notice_stop_markers_refuse_even_with_a_later_success(changes):
    from clink.parsers.claude_native_generation import protocol_diagnostic

    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse(stream([native_notice("informational", **changes), *events()]))
    assert caught.value.code == "protocol_error"
    assert protocol_diagnostic(str(caught.value)) == {"reason": "notice_stop", "event_index": 1}


@pytest.mark.parametrize(
    "case,reason,index",
    [
        ("missing_notice_session", "session_invalid", 1),
        ("invalid_notice_session", "session_invalid", 1),
        ("mismatched_init_session", "session_mismatch", 2),
        ("missing_init", "init_required", 2),
        ("missing_init_model", "init_model_invalid", 2),
        ("duplicate_init", "duplicate_init", 3),
        ("missing_terminal", "terminal_missing", None),
        ("post_terminal_notice", "post_terminal", 5),
        ("duplicate_terminal", "post_terminal", 5),
    ],
)
def test_native_notice_shared_session_and_terminal_authority_stays_strict(case, reason, index):
    from clink.parsers.claude_native_generation import protocol_diagnostic

    notice = native_notice("session_title_changed")
    records = [notice, *events()]
    if case == "missing_notice_session":
        notice.pop("session_id")
    elif case == "invalid_notice_session":
        notice["session_id"] = ""
    elif case == "mismatched_init_session":
        records[1]["session_id"] = "another-session"
    elif case == "missing_init":
        records.pop(1)
    elif case == "missing_init_model":
        records[1].pop("model")
        notice["model"] = MODEL
    elif case == "duplicate_init":
        records.insert(2, records[1].copy())
    elif case == "missing_terminal":
        records.pop()
    elif case == "post_terminal_notice":
        records.append(native_notice("informational"))
    else:
        records.append(records[-1].copy())
    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse("\n \t\r\n" + stream(records))
    assert caught.value.code == "protocol_error"
    assert protocol_diagnostic(str(caught.value)) == {"reason": reason, "event_index": index}
    assert "PRIVATE" not in str(caught.value) and SESSION not in str(caught.value)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"is_error": True}, "protocol_error"),
        ({"errors": ["authentication_required"]}, "authentication_required"),
        ({"status": "failed"}, "protocol_error"),
        ({"errors": ["rate_limit_exceeded"]}, "rate_limited"),
        ({"permission_denials": [{"tool_name": "Read"}]}, "permission_needed"),
    ],
)
def test_native_notice_only_exempts_validated_retry_error_not_other_failures(changes, code):
    notice = native_notice("api_retry", error="unknown", **changes)
    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse(stream([notice, *events()]))
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "message,expected",
    [
        (
            "Claude native generation output violates its protocol [reason=notice_shape;event_index=16384]",
            {"reason": "notice_shape", "event_index": 16384},
        ),
        (
            "Claude native generation output violates its protocol [reason=terminal_missing;event_index=null]",
            {"reason": "terminal_missing", "event_index": None},
        ),
        ("Claude native generation output violates its protocol [reason=PRIVATE;event_index=1]", None),
        ("Claude native generation output violates its protocol [reason=notice_shape;event_index=0]", None),
        ("Claude native generation output violates its protocol [reason=notice_shape;event_index=16385]", None),
        ("Claude native generation output violates its protocol [reason=notice_shape;event_index=1] PRIVATE", None),
    ],
)
def test_native_notice_protocol_diagnostic_decoder_is_closed_and_bounded(message, expected):
    from clink.parsers.claude_native_generation import protocol_diagnostic

    assert protocol_diagnostic(message) == expected


@pytest.mark.parametrize(
    "count,delta,extra",
    [
        (0, 0, {}),
        (-MAX_METADATA_NUMBER, -MAX_METADATA_NUMBER, {}),
        (MAX_METADATA_NUMBER, MAX_METADATA_NUMBER, {"user_message_uuid": ""}),
        (8, 3, {"user_message_uuid": "PRIVATE arbitrary string"}),
    ],
)
def test_thinking_tokens_preserve_only_final_text_and_init_model(count, delta, extra):
    notice = native_notice("thinking_tokens", estimated_tokens=count, estimated_tokens_delta=delta, **extra)
    notice.update(model="PRIVATE metadata model", result="PRIVATE metadata answer")
    records = events()
    records.insert(1, notice)
    parsed = ClaudeNativeGenerationParser().parse(stream(records))
    assert parsed.content == FINAL
    assert parsed.metadata == {"actual_model": MODEL, "tool_error_count": 0, "permission_denial_count": 0}
    assert "PRIVATE" not in json.dumps(parsed.metadata) and SESSION not in json.dumps(parsed.metadata)


@pytest.mark.parametrize(
    "field,value,missing",
    [
        *[
            pytest.param(field, None, True, id=f"missing_{field}")
            for field in ("estimated_tokens", "estimated_tokens_delta")
        ],
        *[
            (field, value, False)
            for field in ("estimated_tokens", "estimated_tokens_delta")
            for value in (True, -MAX_METADATA_NUMBER - 1, MAX_METADATA_NUMBER + 1, 0.5)
        ],
        ("user_message_uuid", [], False),
    ],
)
def test_thinking_tokens_require_bounded_integer_counts_and_optional_string(field, value, missing):
    from clink.parsers.claude_native_generation import protocol_diagnostic

    notice = native_notice("thinking_tokens")
    if missing:
        notice.pop(field)
    else:
        notice[field] = value
    records = events()
    records.insert(1, notice)
    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse(stream(records))
    assert caught.value.code == "protocol_error"
    assert protocol_diagnostic(str(caught.value)) == {"reason": "notice_shape", "event_index": 2}
    assert "PRIVATE" not in str(caught.value) and SESSION not in str(caught.value)


@pytest.mark.parametrize(
    "case,changes,reason,index,code",
    [
        ("failure", {"is_error": True}, "native_failure", 2, "protocol_error"),
        ("failure", {"status": "failed"}, "native_failure", 2, "protocol_error"),
        ("failure", {"errors": ["authentication_required"]}, "native_failure", 2, "authentication_required"),
        ("failure", {"error": "rate_limit"}, "native_failure", 2, "rate_limited"),
        ("failure", {"permission_denials": [{"tool_name": "PRIVATE"}]}, "permission_denied", 2, "permission_needed"),
        ("missing_session", {}, "session_invalid", 2, "protocol_error"),
        ("failure", {"session_id": []}, "session_invalid", 2, "protocol_error"),
        ("failure", {"session_id": "another-session"}, "session_mismatch", 2, "protocol_error"),
        ("preinit", {}, "init_required", 1, "protocol_error"),
        ("post_terminal", {}, "post_terminal", 4, "protocol_error"),
        ("missing_terminal", {"result": "PRIVATE metadata answer"}, "terminal_missing", None, "protocol_error"),
    ],
)
def test_thinking_tokens_keep_native_failure_session_and_terminal_guards(case, changes, reason, index, code):
    from clink.parsers.claude_native_generation import protocol_diagnostic

    notice = native_notice("thinking_tokens", **changes)
    records = events()
    if case == "missing_session":
        notice.pop("session_id")
    if case == "preinit":
        records.insert(0, notice)
    elif case == "post_terminal":
        records.append(notice)
    else:
        records.insert(1, notice)
        if case == "missing_terminal":
            records.pop()
    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse(stream(records))
    assert caught.value.code == code
    assert protocol_diagnostic(str(caught.value)) == {"reason": reason, "event_index": index}
    assert "PRIVATE" not in str(caught.value) and SESSION not in str(caught.value)


def test_parser_enforces_event_and_capture_bounds(monkeypatch):
    import clink.parsers.claude_native_generation as parser

    monkeypatch.setattr(parser, "MAX_EVENTS", 2)
    with pytest.raises(ParserError):
        ClaudeNativeGenerationParser().parse(stream(events()))
    monkeypatch.setattr(parser, "MAX_STREAM_BYTES", 8)
    with pytest.raises(ParserError):
        ClaudeNativeGenerationParser().parse(stream(events()))


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)

    def make(text=FINAL, records=None, sleep=False):
        executable = tmp_path / "synthetic-claude"
        capture = tmp_path / "capture.json"
        code = (
            "import json, os, sys, time\nfrom pathlib import Path\n"
            + f"Path({str(capture)!r}).write_text(json.dumps({{'argv':sys.argv[1:],'cwd':os.getcwd(),'stdin':sys.stdin.read(),'home':os.environ.get('HOME'),'config':os.environ.get('CLAUDE_CONFIG_DIR')}}))\n"
            + "\n".join(
                f"print({json.dumps(event, ensure_ascii=False)!r},flush=True)"
                for event in (events(text) if records is None else records)
            )
            + ("\ntime.sleep(30)\n" if sleep else "\n")
        )
        executable.write_text("#!" + sys.executable + "\n" + code)
        executable.chmod(0o700)
        evidence = tmp_path / "candidate.json"
        evidence.write_text('{"synthetic":true}')
        client = SimpleNamespace(
            adapter="claude",
            executable=(str(executable),),
            config_args=(),
            env={},
            allowed_roots=(tmp_path,),
            allowed_roots_explicit=True,
            configuration_scope="explicit",
            generation_native=None,
        )
        admission = NativeGenerationAdmission(
            version="synthetic",
            verified=True,
            executable_sha256=executable_digest(str(executable)),
            recipe_sha256=claude_generation_recipe_digest(),
            parser_sha256=parser_contract_digest(),
            evidence_path=str(evidence),
            evidence_sha256=sha256(evidence.read_bytes()),
        )
        service = NativeGenerationService(
            SimpleNamespace(
                project_dir=None, get_client=lambda name: client if name == "claude" else pytest.fail("wrong client")
            ),
            claude_admission=admission,
        )
        return service, client, capture

    return make


@pytest.mark.asyncio
async def test_claude_uses_canonical_project_exact_model_stdin_and_native_environment(candidate, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/synthetic/native-home")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/synthetic/native-config")
    service, _, capture = candidate()
    request = parse_native_request(canonical_json(body(tmp_path)))
    result = await service.run(request)
    observed = json.loads(capture.read_text())
    assert result["status"] == "completed" and result["output"] == FINAL
    assert observed["argv"] == [*claude_generation_arguments(), "--model", MODEL]
    assert observed["cwd"] == str(tmp_path) and observed["stdin"].encode() == compose_native_prompt(request)
    assert observed["home"] == "/synthetic/native-home" and observed["config"] == "/synthetic/native-config"
    assert result["execution"]["actual_model"] == MODEL and result["execution"]["side_effects_possible"] is True
    assert not service.pool._children and service.pool.running == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["executable_sha256", "recipe_sha256", "parser_sha256", "evidence_sha256"])
async def test_claude_admission_cannot_reuse_native_tasks_codex_or_changed_evidence(candidate, tmp_path, field):
    service, _, capture = candidate()
    service.claude_admission = replace(service.claude_admission, **{field: "0" * 64})
    result = await service.run(parse_native_request(canonical_json(body(tmp_path))))
    assert result["error"]["code"] == "policy_unverified" and not capture.exists()
    assert DEFAULT_CLAUDE_NATIVE_ADMISSION is not DEFAULT_NATIVE_ADMISSION
    assert DEFAULT_NATIVE_ADMISSION.verified is True
    assert claude_generation_recipe_digest() != native_recipe_digest("claude")


@pytest.fixture
def disabled_claude_admission():
    return NativeGenerationAdmission()


@pytest.mark.asyncio
async def test_disabled_claude_admission_refuses_before_registry_or_spawn(
    tmp_path, monkeypatch, disabled_claude_admission
):
    import clink.native_generation as generation

    monkeypatch.setattr(generation, "ClinkRegistry", lambda: pytest.fail("unverified registry"))
    service = NativeGenerationService(claude_admission=disabled_claude_admission)
    result = await service.run(parse_native_request(canonical_json(body(tmp_path))))
    assert result["error"]["code"] == "policy_unverified" and result["output"] is None
    assert result["execution"]["side_effects_possible"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["limit", "mismatch", "unapproved", "cancel", "timeout"])
async def test_claude_lifecycle_output_root_and_model_fail_closed(candidate, tmp_path, case):
    records = events("x" * (OUTPUT_BYTES + 1) if case == "limit" else FINAL)
    if case == "mismatch":
        records[0]["model"] = "claude-sonnet-4-6"
    service, client, capture = candidate(records=records, sleep=case in {"cancel", "timeout"})
    if case == "unapproved":
        other = tmp_path / "unapproved"
        other.mkdir()
        client.allowed_roots = (other,)
    request = parse_native_request(canonical_json(body(tmp_path, timeout_seconds=1 if case == "timeout" else 5)))
    task = asyncio.create_task(service.run(request))
    if case == "cancel":
        for _ in range(100):
            if capture.exists():
                break
            await asyncio.sleep(0.01)
        assert capture.exists()
        task.cancel()
    result = await task
    if case == "limit":
        assert result["status"] == "incomplete" and result["truncated"] is True
        assert result["output"] == "x" * OUTPUT_BYTES
    else:
        assert result["output"] is None
        assert (
            result["error"]["code"]
            == {
                "mismatch": "unsupported_model",
                "unapproved": "path_not_allowed",
                "cancel": "cancelled",
                "timeout": "timeout",
            }[case]
        )
    assert not service.pool._children and service.pool.running == 0


def test_cli_v2_claude_refuses_unadmitted_synthetic_binary_without_spawning(tmp_path):
    root = Path(__file__).resolve().parents[1]
    executable = tmp_path / "synthetic-claude"
    sentinel = tmp_path / "native-spawned"
    executable.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(sentinel)!r}).touch()\n")
    executable.chmod(0o700)
    manifest = tmp_path / "claude.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "claude",
                "adapter": "claude",
                "command": str(executable),
                "working_dir": str(tmp_path),
                "allowed_roots": [str(tmp_path)],
                "roles": {"default": {"prompt_path": str(root / "systemprompts/clink/default.txt")}},
                "generation_native": {},
            }
        )
    )
    completed = subprocess.run(
        [sys.executable, "-m", "clink.cli", "generate", "--request-json", "-"],
        input=canonical_json(body(tmp_path)),
        capture_output=True,
        cwd=root,
        timeout=5,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLI_CLIENTS_CONFIG_PATH": str(manifest)},
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 1 and len(completed.stdout.splitlines()) == 1
    assert result["error"]["code"] == "policy_unverified" and result["output"] is None
    assert result["execution"]["cli"] == "claude" and result["execution"]["side_effects_possible"] is False
    assert not sentinel.exists()
    assert b"PRIVATE" not in completed.stdout + completed.stderr


def test_retry_metadata_error_exception_does_not_apply_to_assistant_blocks():
    records = events()
    records[1]["message"]["content"] = [native_notice("api_retry")]
    with pytest.raises(ParserError) as caught:
        ClaudeNativeGenerationParser().parse(stream(records))
    assert caught.value.code == "rate_limited"
    assert "PRIVATE" not in str(caught.value)
