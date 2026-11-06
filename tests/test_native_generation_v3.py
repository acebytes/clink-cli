"""Closed schema-three selection without changing schema-two authority."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from clink.generation_contracts import GenerationInputError, canonical_json
from clink.native_generation_contracts import execution_native_identity, parse_native_request, terminal_native_result

FIXTURES = Path(__file__).parent / "fixtures" / "generation_v3"


def wire(project, cli="grok", selection=None, **changes):
    return {
        "schema_version": 3,
        "request_id": "66f96fc9-969d-4c0e-ab92-e355fa0e7588",
        "cli": cli,
        "model_selection": {"kind": "native_default"} if selection is None else selection,
        "instructions": "Keep Unicode café and whitespace\r\n",
        "input": "  source 🦉\n",
        "execution_mode": "native",
        "project_dir": str(project),
        **changes,
    }


@pytest.mark.parametrize("cli", ["grok", "agy", "codex", "claude"])
def test_schema_three_captures_immutable_selection_and_honest_identity(tmp_path, cli):
    selection = (
        {"kind": "explicit", "id": "claude-opus-5-5" if cli == "claude" else "gpt-6.1-sol"}
        if cli in {"claude", "codex"}
        else {"kind": "native_default"}
    )
    data = wire(tmp_path, cli, selection)
    request = parse_native_request(canonical_json(data), started_at=12.5)
    identity = execution_native_identity(request)
    assert request.schema_version == 3
    assert identity["model_selection"] == selection
    assert request.effective_payload()["model_selection"] == selection
    assert "model" not in request.effective_payload()
    assert identity["requested_model"] == selection.get("id")
    assert identity["effective_model"] == selection.get("id")
    assert identity["actual_model"] is None
    assert terminal_native_result(started_at=12.5, request=request)["schema_version"] == 3
    selection["kind"] = "mutated"
    assert request.model_selection.kind != "mutated"
    with pytest.raises(FrozenInstanceError):
        request.model_selection.kind = "explicit"


@pytest.mark.parametrize(
    "cli,selection",
    [
        ("grok", {}),
        ("grok", {"kind": "native_default", "id": "invented"}),
        ("grok", {"kind": "native_default", "extra": True}),
        ("grok", {"kind": "explicit", "id": "grok-4"}),
        ("agy", {"kind": "explicit", "id": "model"}),
        ("codex", {"kind": "native_default"}),
        ("claude", {"kind": "native_default"}),
        ("codex", {"kind": "explicit"}),
        ("codex", {"kind": "explicit", "id": "auto"}),
        ("codex", {"kind": "explicit", "id": "-flag"}),
        ("codex", {"kind": "explicit", "id": "a" * 129}),
        ("claude", {"kind": "explicit", "id": "opus"}),
        ("gemini", {"kind": "native_default"}),
        (["grok"], {"kind": "native_default"}),
        ("grok", "native_default"),
        ("grok", {"kind": ["native_default"]}),
    ],
)
def test_schema_three_rejects_unsupported_and_ambiguous_selection(tmp_path, cli, selection):
    with pytest.raises(GenerationInputError) as error:
        parse_native_request(canonical_json(wire(tmp_path, cli, selection)))
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("change", [{"model": "gpt-6.1-sol"}, {"extra": True}])
def test_schema_three_rejects_mixed_envelopes(tmp_path, change):
    with pytest.raises(GenerationInputError):
        parse_native_request(canonical_json(wire(tmp_path, **change)))


def test_schema_three_duplicate_selection_fields_are_rejected(tmp_path):
    data = json.dumps(wire(tmp_path)).replace(
        '"kind": "native_default"', '"kind":"native_default","kind":"native_default"'
    )
    with pytest.raises(GenerationInputError):
        parse_native_request(data.encode())


@pytest.mark.parametrize(
    "vector", json.loads((FIXTURES / "canonical-vectors.json").read_bytes()), ids=lambda row: row["name"]
)
def test_cross_language_schema_three_canonical_vectors(tmp_path, vector):
    data = vector["request"]
    request = parse_native_request(canonical_json({**data, "project_dir": str(tmp_path)}))
    effective = request.effective_payload()
    effective["project_dir"] = data["project_dir"]
    assert canonical_json(effective).hex() == vector["canonical_utf8_hex"]
    from clink.generation_contracts import sha256

    assert sha256(canonical_json(effective)) == vector["request_sha256"]


@pytest.mark.parametrize("cli", ["grok", "codex"])
def test_completed_goldens_carry_coherent_synthetic_admission_identity(cli):
    result = json.loads((FIXTURES / f"{cli}-completed.json").read_bytes())
    execution = result["execution"]
    assert result["status"] == "completed" and result["error"] is None
    assert execution["admission"] == "verified" and execution["side_effects_possible"] is True
    assert execution["executable_sha256"] == "a" * 64
    assert execution["recipe_sha256"] == "b" * 64
