"""Separate synthetic Grok/Agy generation contracts; never invoke a native CLI."""

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from clink.contracts import BackendError
from clink.native_generation_families import (
    DEFAULT_AGY_NATIVE_ADMISSION,
    DEFAULT_GROK_NATIVE_ADMISSION,
    FAMILY_PROMPT_FILE,
    family_generation_arguments,
    family_generation_parser,
    family_generation_parser_digest,
    family_generation_prompt,
    family_generation_recipe,
    family_generation_recipe_digest,
    prepare_family_generation_launch,
)
from clink.native_generation_policy import NativeGenerationAdmission
from clink.native_policy import native_recipe_digest
from clink.parsers.base import MAX_METADATA_NUMBER, ParserError

FINAL = " \nFinal e\u0301 and \u96ea\n "
GROK_SESSION = "11111111-2222-4333-8444-555555555555"


def grok(**changes):
    return {"text": FINAL, "sessionId": GROK_SESSION, "stopReason": "end_turn", **changes}


def agy_init(**changes):
    return {"event": "init", "conversation_id": "session-1", "init": {"tools": [], **changes}}


def agy_result(**changes):
    return {
        "event": "result",
        "result": {"conversation_id": "session-1", "status": "SUCCESS", "response": FINAL, **changes},
    }


def agy_step(**changes):
    return {
        "event": "step_update",
        "step_update": {
            "conversation_id": "session-1",
            "step_index": 1,
            "state": "DONE",
            "text_delta": "PRIVATE_INTERMEDIATE",
            **changes,
        },
    }


def stream(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events)


@pytest.mark.parametrize("cli", ["grok", "agy"])
def test_separate_native_default_recipe_binds_its_parser_without_selecting_a_model(cli):
    import clink.native_generation_families as families

    recipe = family_generation_recipe(cli)
    assert recipe["adapter"] == cli and recipe["profile"] == "generation_native_v1"
    assert recipe["model_selection"] == "native_default"
    assert "--model" not in recipe["argv"]
    assert recipe["prompt"] == family_generation_prompt(cli)
    assert recipe["parser_sha256"] == family_generation_parser_digest(cli)
    assert recipe["transport_sha256"] == hashlib.sha256(Path(families.__file__).read_bytes()).hexdigest()
    assert family_generation_recipe_digest(cli) != native_recipe_digest(cli)
    assert len(family_generation_parser_digest(cli)) == len(family_generation_recipe_digest(cli)) == 64


def test_native_default_generation_admission_promotes_only_verified_grok():
    admission = DEFAULT_GROK_NATIVE_ADMISSION
    assert admission.verified is True
    assert admission.evidence_path == "clink/resources/native-generation-grok-1.0.70-admission.json"
    assert admission.recipe_sha256 == family_generation_recipe_digest("grok")
    assert admission.parser_sha256 == family_generation_parser_digest("grok")
    assert DEFAULT_AGY_NATIVE_ADMISSION == NativeGenerationAdmission()


def test_grok_private_prompt_has_empty_stdin_exact_native_flags_and_cleanup():
    content = "PRIVATE \u96ea\ntext".encode()
    with prepare_family_generation_launch("grok", ("/synthetic/grok",), content) as launch:
        assert launch.command[0] == "/synthetic/grok"
        path = Path(launch.command[2])
        assert launch.command == ("/synthetic/grok", "--prompt-file", str(path), "--output-format", "json")
        assert family_generation_arguments("grok") == ("--prompt-file", FAMILY_PROMPT_FILE, "--output-format", "json")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert path.read_bytes() == content and launch.stdin == b""
        launch.validate_input()
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("mutation", ["content", "mode", "symlink", "directory", "hardlink", "mtime"])
def test_grok_input_identity_or_content_changes_refuse_before_launch(mutation, tmp_path):
    with prepare_family_generation_launch("grok", ("/synthetic/grok",), b"PRIVATE") as launch:
        path = Path(launch.command[2])
        if mutation == "content":
            path.write_bytes(b"CHANGED")
        elif mutation == "mode":
            path.chmod(0o644)
        elif mutation == "symlink":
            alternate = tmp_path / "alternate"
            alternate.write_bytes(b"PRIVATE")
            path.unlink()
            path.symlink_to(alternate)
        elif mutation == "directory":
            path.parent.chmod(0o755)
        elif mutation == "hardlink":
            os.link(path, tmp_path / "hardlink")
        else:
            observed = path.stat()
            os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns + 1000000))
        with pytest.raises(BackendError) as caught:
            launch.validate_input()
        assert caught.value.code == "invalid_config" and "PRIVATE" not in str(caught.value)
    assert not path.parent.exists()


@pytest.mark.parametrize("mutation", ["replacement", "extra"])
def test_grok_cleanup_preserves_unowned_resources_and_reports_uncertainty(mutation, tmp_path):
    old_root = tmp_path / "moved-original"
    root = None
    try:
        with pytest.raises(BackendError) as caught:
            with prepare_family_generation_launch("grok", ("/synthetic/grok",), b"PRIVATE") as launch:
                path = Path(launch.command[2])
                root = path.parent
                if mutation == "replacement":
                    root.rename(old_root)
                    root.mkdir(mode=0o700)
                (root / "unowned.txt").write_text("preserve")
        assert caught.value.code == "cleanup_unconfirmed"
        assert (root / "unowned.txt").read_text() == "preserve"
        if mutation == "replacement":
            assert (old_root / "prompt.txt").read_bytes() == b"PRIVATE"
    finally:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(old_root, ignore_errors=True)


def test_agy_stdin_is_exactly_one_user_event_then_eof():
    content = "PRIVATE \u96ea\ntext".encode()
    with prepare_family_generation_launch("agy", ("/synthetic/agy", "static"), content) as launch:
        assert launch.command == (
            "/synthetic/agy",
            "static",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
        )
        assert len(launch.stdin.splitlines()) == 1 and launch.stdin.endswith(b"\n")
        assert json.loads(launch.stdin) == {"event": "user", "message": {"content": content.decode()}}
        launch.validate_input()


@pytest.mark.parametrize("cli", ["agy", "grok"])
@pytest.mark.parametrize("content", [b"x" * (1024 * 1024 + 1), b"\xff"])
def test_family_transport_rejects_invalid_or_oversized_prompt(cli, content):
    with pytest.raises(BackendError):
        with prepare_family_generation_launch(cli, ("/synthetic/cli",), content):
            pytest.fail("invalid input was prepared")


def test_agy_envelope_expansion_obeys_same_byte_limit():
    with pytest.raises(BackendError) as caught:
        with prepare_family_generation_launch("agy", ("/synthetic/agy",), b"\n" * (600 * 1024)):
            pytest.fail("oversized envelope was prepared")
    assert caught.value.code == "input_limit"


def test_grok_publishes_only_final_and_unambiguous_valid_model_usage():
    result = family_generation_parser("grok").parse(
        stream(grok(modelUsage={"grok-4.7-build": {"inputTokens": 1, "outputTokens": 2}}, secret="PRIVATE"))
    )
    assert result.content == FINAL
    assert result.metadata == {"actual_model": "grok-4.7-build", "tool_error_count": 0, "permission_denial_count": 0}
    assert "PRIVATE" not in json.dumps(result.metadata) and GROK_SESSION not in json.dumps(result.metadata)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"unknown": {"inputTokens": 1}},
        {"grok-a": {}, "grok-b": {}},
        {"PRIVATE invalid": {"inputTokens": 1}},
        {"grok-model": []},
        {"grok-model": {}},
        {"grok-model": {"inputTokens": True}},
        {"grok-model": {"inputTokens": -1}},
        {"grok-model": {"inputTokens": MAX_METADATA_NUMBER + 1}},
        {"grok-model": {"inputTokens": 1, "modelCalls": False}},
        {"grok-model": {"inputTokens": 1, "modelCalls": 0}},
        {"grok-model": {"inputTokens": 0, "outputTokens": 0}},
        {"grok-model": {"inputTokens": 1, "costUSD": -1}},
    ],
)
def test_grok_actual_model_stays_unknown_without_unambiguous_valid_usage(usage):
    result = family_generation_parser("grok").parse(stream(grok(modelUsage=usage)))
    assert result.content == FINAL and result.metadata["actual_model"] is None


def test_grok_incomplete_usage_cannot_establish_unambiguous_actual_model():
    parsed = family_generation_parser("grok").parse(
        stream(grok(modelUsage={"grok-model": {"inputTokens": 1}}, usage_is_incomplete=True))
    )
    assert parsed.content == FINAL and parsed.metadata["actual_model"] is None


def test_agy_publishes_only_final_and_never_treats_init_model_as_actual():
    result = family_generation_parser("agy").parse(
        stream(agy_init(model="echoed-override", cwd="PRIVATE"), agy_step(), agy_result(secret="PRIVATE"))
    )
    assert result.content == FINAL
    assert result.metadata == {"actual_model": None, "tool_error_count": 0, "permission_denial_count": 0}
    assert "PRIVATE" not in json.dumps(result.metadata) and "session-1" not in json.dumps(result.metadata)


@pytest.mark.parametrize(
    "cli,output,code",
    [
        ("grok", stream(grok(stopReason="max_tokens")), "protocol_error"),
        ("grok", stream(grok(stopReason="max_turn_requests")), "protocol_error"),
        ("grok", stream(grok(stopReason="refusal")), "protocol_error"),
        ("grok", stream(grok(is_error=True)), "protocol_error"),
        ("grok", stream(grok(error="rate_limit")), "rate_limited"),
        ("grok", stream(grok(errors=["authentication_required"])), "authentication_required"),
        ("grok", stream(grok(), grok()), "protocol_error"),
        ("grok", stream(grok(text="")), "empty_response"),
        ("grok", stream(grok(text="x" * 131073)), "output_limit"),
        ("grok", stream(grok(text="\u96ea" * 43691)), "output_limit"),
        ("agy", stream(agy_init()), "protocol_error"),
        ("agy", stream(agy_result()), "protocol_error"),
        ("agy", stream(agy_init(), agy_init(), agy_result()), "protocol_error"),
        ("agy", stream(agy_init(), agy_result(), agy_result()), "protocol_error"),
        ("agy", stream(agy_init(), agy_result(), agy_step()), "protocol_error"),
        ("agy", stream(agy_init(), agy_result(status="RUNNING")), "protocol_error"),
        ("agy", stream(agy_init(), agy_result(status="ERROR", error="PRIVATE")), "protocol_error"),
        ("agy", stream(agy_init(), agy_result(finished_reason="print_timeout")), "protocol_error"),
        ("agy", stream(agy_init(), agy_result(error="rate_limit")), "rate_limited"),
        ("agy", stream(agy_init(), agy_result(conversation_id="session-2")), "protocol_error"),
        ("agy", stream(agy_init(), agy_step(conversation_id="session-2"), agy_result()), "protocol_error"),
        ("agy", stream(agy_init(), agy_result(response="")), "empty_response"),
        ("agy", stream(agy_init(), agy_result(response="x" * 131073)), "output_limit"),
        ("agy", stream(agy_init(), agy_result(denied_actions=[{"display_name": "PRIVATE"}])), "permission_needed"),
    ],
)
def test_incomplete_error_duplicate_missing_and_oversized_finals_refuse(cli, output, code):
    with pytest.raises(ParserError) as caught:
        family_generation_parser(cli).parse(output, "PRIVATE_STDERR")
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("cli", ["agy", "grok"])
@pytest.mark.parametrize(
    "output",
    ["", "PRIVATE_INVALID", "[]", "null", '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '{"x":"\\ud800"}', "\ud800"],
)
def test_invalid_utf8_json_and_wrong_dialects_refuse_without_echo(cli, output):
    with pytest.raises(ParserError) as caught:
        family_generation_parser(cli).parse(output, "PRIVATE_STDERR")
    assert caught.value.code == "protocol_error" and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("with_output", [False, True])
def test_grok_exact_source_supported_permission_refusal_blocks_publication(with_output):
    with pytest.raises(ParserError) as caught:
        family_generation_parser("grok").parse(
            stream(grok()) if with_output else "",
            "permission required: Edit PRIVATE.py was not run, as a headless run cannot ask for approval",
        )
    assert caught.value.code == "permission_needed" and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("cli", ["agy", "grok"])
def test_arbitrary_permission_prose_does_not_claim_native_refusal(cli):
    output = (
        stream(grok(text="Permission denied"))
        if cli == "grok"
        else stream(agy_init(), agy_result(response="Permission denied"))
    )
    assert family_generation_parser(cli).parse(output, "permission denied").content == "Permission denied"


def test_agy_recovered_tool_errors_are_counted_without_publishing_payloads():
    error_step = agy_step(state="ERROR", tool_info={"error": {"type": "exit", "message": "PRIVATE_ERROR"}})
    parsed = family_generation_parser("agy").parse(stream(agy_init(), error_step, error_step, agy_result()))
    assert parsed.content == FINAL and parsed.metadata["tool_error_count"] == 1
    assert "PRIVATE" not in json.dumps(parsed.metadata)


@pytest.mark.parametrize("cli", ["agy", "grok"])
def test_generation_parsers_enforce_capture_bounds(monkeypatch, cli):
    if cli == "agy":
        from clink.parsers import agy_native_generation as module

        output = stream(agy_init(), agy_step(), agy_result())
        monkeypatch.setattr(module, "MAX_EVENTS", 2)
        with pytest.raises(ParserError):
            family_generation_parser(cli).parse(output)
    else:
        from clink.parsers import grok_native_generation as module

        output = stream(grok())
    monkeypatch.setattr(module, "MAX_STREAM_BYTES", 8)
    with pytest.raises(ParserError):
        family_generation_parser(cli).parse(output)
