"""Native generation wire identity and intake boundaries without CLI execution."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from clink import generation_contracts as v1
from clink import native_generation_contracts as native

FIXTURES = Path(__file__).parent / "fixtures" / "generation_v2"


def payload(tmp_path, **changes):
    value = json.loads((FIXTURES / "request.json").read_bytes())
    value["project_dir"] = str(tmp_path.resolve(strict=True))
    value.update(changes)
    return value


def encode(tmp_path, **changes):
    return json.dumps(payload(tmp_path, **changes), ensure_ascii=False).encode()


def golden_request(data, timeout_ms):
    """Hash the cross-language fixture independently of its example directory's host."""
    return native.NativeGenerationRequest(
        request_id=data["request_id"],
        cli=data["cli"],
        model=data["model"],
        instructions=data["instructions"],
        input=data["input"],
        timeout_ms=timeout_ms,
        started_at=12.5,
        project_dir=data["project_dir"],
        execution_mode=data["execution_mode"],
    )


def test_cross_language_golden_preserves_exact_unicode_whitespace_and_hashes():
    data = json.loads((FIXTURES / "request.json").read_bytes())
    request = golden_request(data, 120125)
    result = json.loads((FIXTURES / "completed.json").read_bytes())
    original = json.loads((FIXTURES.parent / "generation_v1" / "request.json").read_bytes())
    assert native.canonical_json(request.effective_payload()) == (FIXTURES / "canonical-request.json").read_bytes()
    assert request.request_sha256 == result["request_sha256"]
    assert request.instructions_sha256 == result["instructions_sha256"]
    assert request.input_sha256 == result["input_sha256"]
    assert request.instructions == original["instructions"] and request.instructions.endswith("\r\n")
    assert request.input == original["input"] and request.input.startswith("  ") and request.input.endswith("\n")
    assert request.project_dir == "/private/tmp" and request.execution_mode == "native"
    assert request.deadline == 132.625
    assert set(request.effective_payload()) == {
        "schema_version",
        "execution_mode",
        "project_dir",
        "cli",
        "model",
        "instructions",
        "input",
        "timeout_ms",
    }
    assert result["execution"]["actual_model"] is None
    assert result["execution"]["catalog_sha256"] is None
    assert result["execution"]["side_effects_possible"] is True
    assert result["execution"]["fulfillment_verified"] is False


@pytest.mark.parametrize(
    "vector", json.loads((FIXTURES / "canonical-vectors.json").read_bytes()), ids=lambda vector: vector["name"]
)
def test_shared_canonical_json_vectors(vector, tmp_path):
    request = golden_request(vector["request"], vector["timeout_ms"])
    assert native.canonical_json(request.effective_payload()).hex() == vector["canonical_utf8_hex"]
    assert request.request_sha256 == vector["request_sha256"]
    # Exercise intake too, while keeping the literal golden independent of the test host.
    wire = {**vector["request"], "project_dir": str(tmp_path.resolve(strict=True))}
    assert native.parse_native_request(native.canonical_json(wire)).timeout_ms == vector["timeout_ms"]


def test_parsed_request_is_frozen_and_preserves_original_strings(tmp_path):
    data = payload(tmp_path)
    request = native.parse_native_request(native.canonical_json(data), started_at=12.5)
    assert isinstance(request, v1.GenerationRequest)
    assert request.project_dir == data["project_dir"]
    assert request.instructions == data["instructions"]
    assert request.input == data["input"]
    assert request.timeout_ms == 120125 and request.deadline == 132.625
    with pytest.raises(FrozenInstanceError):
        request.project_dir = "/"


def test_request_id_is_excluded_but_native_project_is_bound_to_hash(tmp_path):
    first = native.parse_native_request(encode(tmp_path))
    second = native.parse_native_request(encode(tmp_path, request_id="00000000-0000-0000-0000-000000000001"))
    assert first.request_sha256 == second.request_sha256
    other = tmp_path / "other"
    other.mkdir()
    third = native.parse_native_request(encode(tmp_path, project_dir=str(other)))
    assert first.request_sha256 != third.request_sha256
    common = payload(tmp_path)
    common.pop("project_dir")
    common.pop("execution_mode")
    common["schema_version"] = 1
    assert first.request_sha256 != v1.parse_request(native.canonical_json(common)).request_sha256


@pytest.mark.parametrize("timeout,expected", [(1, 1000), (1.0001, 1001), (2.007, 2008), (1800, 1800000)])
def test_timeout_reuses_binary64_then_ceil(timeout, expected, tmp_path):
    assert native.parse_native_request(encode(tmp_path, timeout_seconds=timeout)).timeout_ms == expected


def test_optional_timeout_and_uppercase_uuid_reuse_v1_defaults(tmp_path):
    data = payload(tmp_path)
    data.pop("timeout_seconds")
    data["request_id"] = data["request_id"].upper()
    request = native.parse_native_request(native.canonical_json(data))
    assert request.timeout_ms == 120000
    assert request.request_id == data["request_id"].lower()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2.0),
        ("schema_version", "2"),
        ("schema_version", 1),
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
        ("timeout_seconds", 10**400),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", float("nan")),
        ("execution_mode", "consultation"),
        ("execution_mode", None),
        ("execution_mode", ["native"]),
        ("project_dir", ""),
        ("project_dir", None),
        ("project_dir", []),
        ("project_dir", "relative/project"),
        ("project_dir", "a\x00b"),
        ("role", "default"),
        ("files", []),
        ("history", []),
        ("inherit_env", ["SECRET"]),
    ],
)
def test_request_rejects_coercions_unknown_fields_and_invalid_values(field, value, tmp_path):
    with pytest.raises(v1.GenerationInputError):
        native.parse_native_request(encode(tmp_path, **{field: value}))


@pytest.mark.parametrize(
    "field", ["schema_version", "request_id", "cli", "model", "instructions", "input", "execution_mode", "project_dir"]
)
def test_native_authority_and_other_required_fields_are_never_defaulted(field, tmp_path):
    data = payload(tmp_path)
    data.pop(field)
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(native.canonical_json(data))
    assert caught.value.code == "invalid_request"


@pytest.mark.parametrize(
    "raw",
    [
        b"{}{}",
        b"[]",
        b"null",
        b"\xff",
        b'{"request_id":"x","request_id":"x"}',
        b'{"nested":{"x":1,"x":2}}',
        b"{",
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_request_rejects_ambiguous_json(raw):
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(raw)
    assert caught.value.code == "invalid_request"
    assert caught.value.request_id is None


@pytest.mark.parametrize("field", ["timeout_seconds", "model", "instructions", "input"])
def test_overflowed_json_numbers_are_rejected_without_uncaught_serialization_errors(field, tmp_path):
    data = payload(tmp_path)
    data[field] = "overflow-placeholder"
    raw = native.canonical_json(data).replace(b'"overflow-placeholder"', b"1e309")
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(raw)
    assert caught.value.code == "invalid_request"
    assert caught.value.request_id == data["request_id"]


@pytest.mark.parametrize("field,limit", [("instructions", 65536), ("input", 262144)])
def test_text_limits_reuse_utf8_byte_counts(field, limit, tmp_path):
    value = "😀" * (limit // 4)
    assert getattr(native.parse_native_request(encode(tmp_path, **{field: value})), field) == value
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(encode(tmp_path, **{field: value + "x"}))
    assert caught.value.code == "input_limit"


@pytest.mark.parametrize("field", ["instructions", "input", "project_dir"])
def test_lone_surrogates_are_rejected(field, tmp_path):
    raw = json.dumps(payload(tmp_path, **{field: "\ud800"})).encode()
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(raw)
    assert caught.value.code == "invalid_request"
    assert caught.value.request_id == payload(tmp_path)["request_id"]


def test_raw_byte_limit_and_safe_validation_diagnostics(tmp_path):
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(b" " * (1024 * 1024 + 1))
    assert caught.value.code == "input_limit" and caught.value.request_id is None
    for changes in (
        {"input": {"secret": "credential-sentinel"}},
        {"project_dir": "/credential-sentinel/no-such-directory"},
    ):
        with pytest.raises(v1.GenerationInputError) as caught:
            native.parse_native_request(encode(tmp_path, **changes))
        assert "credential-sentinel" not in str(caught.value)
        assert caught.value.request_id == payload(tmp_path)["request_id"]


def test_directory_must_exist_and_have_exact_canonical_wire_spelling(tmp_path):
    project = tmp_path.resolve(strict=True)
    child = project / "child"
    child.mkdir()
    regular = project / "file"
    regular.write_text("synthetic")
    invalid = [
        str(project / "missing"),
        str(regular),
        str(project) + "/",
        str(project) + "/.",
        str(child) + "/..",
        " " + str(project),
        "//" + str(project).lstrip("/"),
    ]
    for wire in invalid:
        with pytest.raises(v1.GenerationInputError) as caught:
            native.parse_native_request(encode(tmp_path, project_dir=wire))
        assert caught.value.code == "invalid_request"
    assert native.parse_native_request(encode(tmp_path, project_dir=str(child))).project_dir == str(child)


def test_symlink_final_and_ancestor_wire_spellings_are_rejected(tmp_path):
    project = tmp_path.resolve(strict=True)
    target = project / "target"
    target.mkdir()
    child = target / "child"
    child.mkdir()
    link = project / "link"
    link.symlink_to(target, target_is_directory=True)
    for wire in (str(link), str(link / "child")):
        with pytest.raises(v1.GenerationInputError):
            native.parse_native_request(encode(tmp_path, project_dir=wire))
    assert native.parse_native_request(encode(tmp_path, project_dir=str(child))).project_dir == str(child)


def test_canonical_unicode_and_spaces_in_project_are_preserved(tmp_path):
    project = tmp_path.resolve(strict=True) / "café project 😀"
    project.mkdir()
    request = native.parse_native_request(encode(tmp_path, project_dir=str(project)))
    assert request.project_dir == str(project)
    assert request.effective_payload()["project_dir"] == str(project)


@pytest.mark.parametrize("wire", ["/" + "a" * 4096, "/" + "é" * 2048, "/\ud800"])
def test_invalid_project_unicode_or_excess_utf8_bytes_never_probe_filesystem(wire, tmp_path, monkeypatch):
    raw = json.dumps(payload(tmp_path, project_dir=wire)).encode()

    def unexpected_resolve(_path, *, strict):
        pytest.fail("Invalid project bytes must be rejected before filesystem resolution.")

    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(raw)
    assert caught.value.code == "invalid_request"
    assert caught.value.request_id == json.loads(raw)["request_id"]
    assert wire not in str(caught.value)


@pytest.mark.parametrize("wire", ["/" + "a" * 4095, "/" + "é" * 2047 + "x"])
def test_exact_project_utf8_byte_limit_reaches_directory_validation(wire, tmp_path, monkeypatch):
    assert len(wire.encode("utf-8")) == native.PROJECT_DIR_BYTES == 4096
    raw = encode(tmp_path, project_dir=wire)
    resolved = []

    def fail_resolve(path, *, strict):
        assert strict is True
        resolved.append(str(path))
        raise OSError("synthetic")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(raw)
    assert caught.value.code == "invalid_request"
    assert resolved == [wire]


@pytest.mark.parametrize("failure", [OSError("synthetic"), RuntimeError("synthetic"), ValueError("synthetic")])
def test_directory_resolution_failures_are_public_input_errors(failure, tmp_path, monkeypatch):
    raw = encode(tmp_path)

    def fail_resolve(_path, *, strict):
        assert strict is True
        raise failure

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(v1.GenerationInputError) as caught:
        native.parse_native_request(raw)
    assert caught.value.code == "invalid_request"
    assert "synthetic" not in str(caught.value)


@pytest.mark.parametrize(
    "status,code,exit_code",
    [
        ("completed", None, 0),
        ("failed", "policy_unverified", 1),
        ("failed", "permission_needed", 1),
        ("failed", "invalid_request", 2),
        ("failed", "input_limit", 2),
        ("failed", "invalid_invocation", 2),
        ("incomplete", "output_limit", 3),
        ("timed_out", "timeout", 124),
        ("cancelled", "cancelled", 130),
    ],
)
def test_terminal_exit_codes_and_exact_result_shape(status, code, exit_code):
    result = native.terminal_native_result(started_at=0, status=status, code=code)
    golden = json.loads((FIXTURES / "completed.json").read_bytes())
    assert set(result) == set(v1.terminal_result(started_at=0)) == set(golden)
    assert set(result["execution"]) == set(golden["execution"])
    assert result["schema_version"] == 2 and native.result_exit_code(result) == exit_code
    assert result["execution"] == {
        **v1.execution_identity(),
        "profile": "generation_native_v1",
        "execution_mode": "native",
        "project_dir": None,
        "side_effects_possible": False,
        "permission_observability": "limited",
        "fulfillment_verified": False,
    }
    assert result["request_sha256"] is None


def test_validated_request_and_explicit_launch_metadata_are_retained(tmp_path):
    request = native.parse_native_request(encode(tmp_path))
    execution = native.execution_native_identity(request)
    execution.update(side_effects_possible=True, actual_model="gpt-6.1-sol", admission="verified")
    result = native.terminal_native_result(
        started_at=request.started_at,
        request=request,
        status="failed",
        code="permission_needed",
        message="x" * 1100,
        retry_after_seconds=2,
        execution=execution,
    )
    assert result["execution"] is execution
    assert result["request_id"] == request.request_id
    assert result["request_sha256"] == request.request_sha256
    assert result["instructions_sha256"] == request.instructions_sha256
    assert result["input_sha256"] == request.input_sha256
    assert result["execution"]["project_dir"] == request.project_dir
    assert result["execution"]["catalog_sha256"] is None
    assert len(result["error"]["message"]) == 1024
    assert result["error"]["retry_after_seconds"] == 2
    assert result["duration_ms"] >= 0


def test_v1_constants_helpers_and_public_input_error_are_reused():
    for name in (
        "RAW_REQUEST_BYTES",
        "PROMPT_BYTES",
        "RESULT_BYTES",
        "INSTRUCTIONS_BYTES",
        "INPUT_BYTES",
        "OUTPUT_BYTES",
        "STDIN_SECONDS",
        "canonical_json",
        "sha256",
        "GenerationInputError",
        "result_exit_code",
    ):
        assert getattr(native, name) is getattr(v1, name)
