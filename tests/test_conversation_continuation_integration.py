"""The initial CLI reply is committed with its user prompt in canonical history."""

import json

import pytest

from server import handle_call_tool
from utils.conversation_memory import get_thread


@pytest.mark.asyncio
async def test_first_response_persisted_in_conversation_history(cli_backend, tmp_path):
    cli_backend.content = "Here is the initial answer."
    result = await handle_call_tool(
        "chat", {"prompt": "First question?", "working_directory_absolute_path": str(tmp_path)}
    )
    payload = json.loads(result[0].text)
    context = get_thread(payload["continuation_offer"]["continuation_id"])
    assert [turn.role for turn in context.turns] == ["user", "assistant"]
    assert context.turns[0].content == "First question?"
    assert "Here is the initial answer." in context.turns[1].content
    assert context.successful_turns == 2
