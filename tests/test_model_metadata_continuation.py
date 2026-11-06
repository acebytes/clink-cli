"""Requested and actual CLI identities remain distinct across canonical continuation."""

import json

import pytest

from server import handle_call_tool
from utils.conversation_memory import get_thread


async def chat(tmp_path, **overrides):
    arguments = {"prompt": "A question.", "working_directory_absolute_path": str(tmp_path)}
    arguments.update(overrides)
    return json.loads((await handle_call_tool("chat", arguments))[0].text)


@pytest.mark.asyncio
async def test_requested_model_is_recorded_without_claiming_actual(cli_backend, tmp_path):
    payload = await chat(tmp_path, model="native-exact")
    assert payload["metadata"]["requested_model"] == "native-exact"
    assert payload["metadata"]["actual_model"] is None
    thread = get_thread(payload["continuation_offer"]["continuation_id"])
    assert thread.turns[-1].model_metadata["requested_model"] == "native-exact"
    assert thread.turns[-1].model_metadata["actual_model"] is None


@pytest.mark.asyncio
async def test_omitted_model_on_continuation_uses_current_native_defaults(cli_backend, tmp_path):
    first = await chat(tmp_path, model="earlier-native-model")
    await chat(tmp_path, prompt="Follow-up.", continuation_id=first["continuation_offer"]["continuation_id"])
    assert cli_backend.resolutions[-1]["model"] is None
    assert cli_backend.requests[-1].history_turns


@pytest.mark.asyncio
async def test_explicit_continuation_model_is_opaque(cli_backend, tmp_path):
    first = await chat(tmp_path)
    payload = await chat(
        tmp_path,
        model="vendor/model:preview",
        prompt="Follow-up.",
        continuation_id=first["continuation_offer"]["continuation_id"],
    )
    assert cli_backend.resolutions[-1]["model"] == "vendor/model:preview"
    assert payload["metadata"]["requested_model"] == "vendor/model:preview"


@pytest.mark.asyncio
async def test_cli_switch_preserves_pal_uuid_and_attributes_new_result(cli_backend, tmp_path):
    first = await chat(tmp_path, cli_name="codex")
    identifier = first["continuation_offer"]["continuation_id"]
    second = await chat(tmp_path, cli_name="claude", prompt="Another perspective.", continuation_id=identifier)
    assert second["continuation_offer"]["continuation_id"] == identifier
    assert second["metadata"]["backend"]["cli_name"] == "claude"
    context = get_thread(identifier)
    assert context.turns[-1].model_metadata["backend"]["cli_name"] == "claude"
    assert context.successful_turns == 4


@pytest.mark.asyncio
async def test_auto_model_is_forwarded_as_a_native_default_request(cli_backend, tmp_path):
    payload = await chat(tmp_path, model="auto")
    assert cli_backend.resolutions[-1]["model"] == "auto"
    assert payload["metadata"]["actual_model"] is None


@pytest.mark.asyncio
async def test_native_session_identifiers_are_absent_from_continuation_metadata(cli_backend, tmp_path):
    payload = await chat(tmp_path)
    assert "session_id" not in payload["metadata"]
    assert "native_session_id" not in payload["metadata"]
    assert payload["continuation_offer"]["continuation_id"] != payload["metadata"]["request_id"]
