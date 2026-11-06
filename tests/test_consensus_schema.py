"""Public schema advertises native CLI participant defaults and bounded modes."""

from tools.consensus import ConsensusTool


def test_consensus_schema_uses_optional_native_targets_and_bounded_roster():
    schema = ConsensusTool().get_input_schema()
    fields = schema["properties"]
    assert fields["execution_mode"]["default"] == "sequential"
    assert fields["execution_mode"]["enum"] == ["sequential", "parallel"]
    assert fields["models"]["minItems"] == 2
    assert fields["models"]["maxItems"] == 8
    entry = fields["models"]["items"]
    assert "required" not in entry
    assert set(entry["properties"]) == {"cli_name", "role", "model", "stance", "stance_prompt"}
    assert entry["additionalProperties"] is False
    assert fields["model"]["deprecated"] is True
    assert "listmodels" in fields["models"]["description"]
    assert "temperature" not in fields
    assert "thinking_mode" not in fields
    assert set(schema["required"]) == {"step", "step_number", "total_steps", "next_step_required", "findings"}
