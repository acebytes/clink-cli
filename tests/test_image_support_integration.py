"""Preserve image references across CLI requests and canonical conversation turns."""

import json
import uuid

import pytest

from clink.backend import CLIBackend
from clink.contracts import BackendError, CLIRequest
from server import handle_call_tool
from tests.cli_helpers import context_target
from tools.chat import ChatTool
from tools.debug import DebugIssueTool
from utils.call_state import conversation_call
from utils.conversation_memory import ConversationTurn, ThreadContext, get_conversation_image_list, get_thread


def test_conversation_turn_includes_images():
    turn = ConversationTurn(
        role="user",
        content="Inspect diagram",
        timestamp="2026-11-01T00:00:00Z",
        files=["code.py"],
        images=["diagram.png"],
        tool_name="chat",
    )
    assert turn.images == ["diagram.png"]
    assert turn.files == ["code.py"]


def test_get_conversation_image_list_newest_first():
    context = ThreadContext(
        thread_id=str(uuid.uuid4()),
        created_at="2026-11-01T00:00:00Z",
        last_updated_at="2026-11-01T00:00:00Z",
        tool_name="chat",
        initial_context={},
        turns=[
            ConversationTurn(
                role="user", content="Old", timestamp="2026-11-01T00:00:00Z", images=["old.png", "shared.png"]
            ),
            ConversationTurn(
                role="user", content="New", timestamp="2026-11-01T00:01:00Z", images=["shared.png", "new.png"]
            ),
        ],
    )
    assert get_conversation_image_list(context) == ["shared.png", "new.png", "old.png"]


@pytest.mark.asyncio
async def test_atomic_turn_recording_preserves_images():
    async with conversation_call("chat", {"prompt": "Inspect diagram"}) as call:
        call.commit(
            {"status": "success"},
            user_content="Inspect diagram",
            assistant_content="Diagram inspected",
            images=["diagram.png"],
            files=["code.py"],
        )
        identifier = call.continuation_id
    context = get_thread(identifier)
    assert [turn.role for turn in context.turns] == ["user", "assistant"]
    assert all(turn.images == ["diagram.png"] for turn in context.turns)
    assert context.successful_turns == 2


@pytest.mark.parametrize("tool_class", [ChatTool, DebugIssueTool])
def test_tool_schema_includes_images(tool_class):
    field = tool_class().get_input_schema()["properties"]["images"]
    assert field["type"] == "array"
    assert field["items"]["type"] == "string"


def test_missing_image_is_not_silently_dropped(tmp_path):
    request = CLIRequest("Inspect diagram", context_target(tmp_path), images=(str(tmp_path / "missing.png"),))
    with pytest.raises(BackendError) as error:
        CLIBackend._context(request)
    assert error.value.code == "path_not_allowed"


@pytest.mark.parametrize(
    "name,data",
    [
        ("image.png", b"\x89PNG\r\n\x1a\nfixture"),
        ("image.jpg", b"\xff\xd8\xfffixture"),
        ("image.gif", b"GIF89afixture"),
        ("image.webp", b"RIFF1234WEBPfixture"),
    ],
)
def test_local_image_headers_are_validated(name, data, tmp_path):
    path = tmp_path / name
    path.write_bytes(data)
    request = CLIRequest("Inspect image", context_target(tmp_path), images=(str(path),))
    assert CLIBackend._context(request) == ((), (str(path),))


@pytest.mark.asyncio
async def test_chat_execution_forwards_images(cli_backend, tmp_path):
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    await handle_call_tool(
        "chat", {"prompt": "Inspect diagram", "working_directory_absolute_path": str(tmp_path), "images": [str(image)]}
    )
    assert cli_backend.requests[0].images == (str(image),)


@pytest.mark.asyncio
async def test_cross_tool_image_context_preservation(cli_backend, tmp_path):
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    source = tmp_path / "subject.py"
    source.write_text("value = 1\n")
    first = json.loads(
        (
            await handle_call_tool(
                "chat",
                {"prompt": "Inspect diagram", "working_directory_absolute_path": str(tmp_path), "images": [str(image)]},
            )
        )[0].text
    )
    await handle_call_tool(
        "debug",
        {
            "step": "Investigate the mismatch in the diagram.",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "findings": "The diagram and source disagree.",
            "relevant_files": [str(source)],
            "confidence": "medium",
            "continuation_id": first["continuation_offer"]["continuation_id"],
        },
    )
    assert cli_backend.requests[1].images == (str(image),)


def test_data_urls_are_rejected_by_cli_admission(tmp_path):
    request = CLIRequest("Inspect image", context_target(tmp_path), images=("data:image/png;base64,AAA",))
    with pytest.raises(BackendError) as error:
        CLIBackend._context(request)
    assert error.value.code == "path_not_allowed"
