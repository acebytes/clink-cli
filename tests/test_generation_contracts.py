"""Cross-language request hashing and strict intake contracts without model execution."""

import json
from pathlib import Path

import pytest

from clink.generation import compose_prompt
from clink.generation_contracts import (
    GenerationInputError,
    canonical_json,
    parse_request,
    result_exit_code,
    terminal_result,
)
from clink.generation_policy import GENERATION_INSTRUCTIONS, generation_arguments

FIXTURES = Path(__file__).parent / "fixtures" / "generation_v1"


def payload(**changes):
    value = json.loads((FIXTURES / "request.json").read_bytes())
    value.update(changes)
    return value


def encode(**changes):
    return json.dumps(payload(**changes), ensure_ascii=False).encode()


def test_cross_language_golden_preserves_exact_unicode_whitespace_and_hashes():
    request = parse_request((FIXTURES / "request.json").read_bytes(), started_at=12.5)
    result = json.loads((FIXTURES / "completed.json").read_bytes())
    assert canonical_json(request.effective_payload()) == (FIXTURES / "canonical-request.json").read_bytes()
    assert request.request_sha256 == result["request_sha256"]
    assert request.instructions_sha256 == result["instructions_sha256"]
    assert request.input_sha256 == result["input_sha256"]
    assert request.instructions.endswith("\r\n")
    assert request.input.startswith("  ") and request.input.endswith("\n")
    assert request.timeout_ms == 120125 and request.deadline == 132.625
    prompt = compose_prompt(request)
    assert json.loads(prompt) == {"instructions": request.instructions, "input": request.input}
    assert "role" not in json.loads(prompt)
    override = "developer_instructions=" + json.dumps(GENERATION_INSTRUCTIONS, ensure_ascii=False)
    assert override in generation_arguments()


def test_request_id_does_not_change_effective_request_hash():
    first = parse_request(encode())
    second = parse_request(encode(request_id="00000000-0000-0000-0000-000000000001"))
    assert first.request_sha256 == second.request_sha256


@pytest.mark.parametrize(
    "vector", json.loads((FIXTURES / "canonical-vectors.json").read_bytes()), ids=lambda vector: vector["name"]
)
def test_shared_canonical_json_vectors(vector):
    request = parse_request(canonical_json(vector["request"]))
    assert request.timeout_ms == vector["timeout_ms"]
    assert canonical_json(request.effective_payload()).hex() == vector["canonical_utf8_hex"]
    assert request.request_sha256 == vector["request_sha256"]


@pytest.mark.parametrize("timeout,expected", [(1, 1000), (1.0001, 1001), (2.007, 2008), (1800, 1800000)])
def test_timeout_uses_binary64_then_ceil(timeout, expected):
    assert parse_request(encode(timeout_seconds=timeout)).timeout_ms == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("request_id", "not-a-uuid"),
        ("request_id", "66f96fc9969d4c0eab92e355fa0e7588"),
        ("cli", "claude"),
        ("cli", ["codex"]),
        ("model", "auto"),
        ("model", "-model"),
        ("model", "gpt 6"),
        ("model", None),
        ("instructions", " \n"),
        ("instructions", "a\x00b"),
        ("input", ""),
        ("input", []),
        ("input", "x\x00y"),
        ("timeout_seconds", True),
        ("timeout_seconds", "120"),
        ("timeout_seconds", None),
        ("timeout_seconds", 0.999),
        ("timeout_seconds", 1800.01),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", float("nan")),
        ("role", "default"),
        ("files", []),
        ("history", []),
    ],
)
def test_request_rejects_coercions_unknown_fields_and_invalid_values(field, value):
    with pytest.raises(GenerationInputError):
        parse_request(encode(**{field: value}))


@pytest.mark.parametrize("raw", [b"{}{}", b"[]", b"null", b"\xff", b'{"request_id":"x","request_id":"x"}', b"{"])
def test_request_rejects_ambiguous_json(raw):
    with pytest.raises(GenerationInputError) as caught:
        parse_request(raw)
    assert caught.value.code == "invalid_request"
    assert caught.value.request_id is None


@pytest.mark.parametrize("field,limit", [("instructions", 65536), ("input", 262144)])
def test_text_limits_count_utf8_bytes(field, limit):
    assert getattr(parse_request(encode(**{field: "é" * (limit // 2)})), field) == "é" * (limit // 2)
    with pytest.raises(GenerationInputError) as caught:
        parse_request(encode(**{field: "é" * (limit // 2) + "x"}))
    assert caught.value.code == "input_limit"


def test_lone_surrogates_rejected_and_unicode_separators_literal():
    raw = json.dumps(payload(input="\ud800")).encode()
    with pytest.raises(GenerationInputError) as caught:
        parse_request(raw)
    assert caught.value.code == "invalid_request"
    request = parse_request(encode(input="line\u2028next\u2029end / \\ \b\t\n\f\r\x01"))
    canonical = canonical_json(request.effective_payload())
    assert b"\\u2028" not in canonical and b"\\u2029" not in canonical and b"\\/" not in canonical
    assert b"\\b\\t\\n\\f\\r\\u0001" in canonical


def test_raw_byte_limit_and_safe_validation_diagnostics():
    raw = b" " * (1024 * 1024 + 1)
    with pytest.raises(GenerationInputError) as caught:
        parse_request(raw)
    assert caught.value.code == "input_limit"
    with pytest.raises(GenerationInputError) as caught:
        parse_request(encode(input={"secret": "credential-sentinel"}))
    assert "credential-sentinel" not in str(caught.value)
    assert caught.value.request_id == payload()["request_id"]


@pytest.mark.parametrize(
    "status,code,exit_code",
    [
        ("completed", None, 0),
        ("failed", "policy_unverified", 1),
        ("failed", "invalid_request", 2),
        ("failed", "input_limit", 2),
        ("incomplete", "output_limit", 3),
        ("timed_out", "timeout", 124),
        ("cancelled", "cancelled", 130),
    ],
)
def test_terminal_exit_codes_and_exact_result_shape(status, code, exit_code):
    result = terminal_result(started_at=0, status=status, code=code)
    golden = json.loads((FIXTURES / "completed.json").read_bytes())
    assert set(result) == set(golden)
    assert set(result["execution"]) == set(golden["execution"])
    assert result_exit_code(result) == exit_code
    assert result["execution"]["actual_model"] is None
