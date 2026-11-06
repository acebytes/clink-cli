"""Canonical replay keeps path references without storing source snapshots."""

import uuid

from utils.conversation_memory import (
    ConversationTurn,
    ThreadContext,
    build_conversation_history,
    get_conversation_file_list,
    get_conversation_image_list,
)


def context(turns):
    return ThreadContext(
        thread_id=str(uuid.uuid4()),
        created_at="now",
        last_updated_at="now",
        tool_name="chat",
        initial_context={},
        turns=turns,
    )


def turn(content, files=None, images=None, tool="chat"):
    return ConversationTurn(role="user", content=content, timestamp="now", files=files, images=images, tool_name=tool)


def test_newest_file_and_image_references_win_with_stable_order():
    thread = context(
        [
            turn("first", files=["a.py", "b.py"], images=["a.png", "b.png"]),
            turn("second", files=["c.py"]),
            turn("third", files=["a.py", "d.py"], images=["a.png", "c.png"]),
        ]
    )
    assert get_conversation_file_list(thread) == ["a.py", "d.py", "c.py", "b.py"]
    assert get_conversation_image_list(thread) == ["a.png", "c.png", "b.png"]


def test_cross_tool_replay_preserves_references_and_does_not_read_sources(tmp_path):
    path = tmp_path / "source.py"
    path.write_text("PRIVATE_SOURCE_NOT_A_SNAPSHOT")
    image = tmp_path / "diagram.png"
    thread = context(
        [
            turn("chat question", files=[str(path)]),
            turn("debug findings", images=[str(image)], tool="debug"),
        ]
    )
    history, _ = build_conversation_history(thread)
    assert str(path) in history and str(image) in history
    assert "chat question" in history and "debug findings" in history
    assert "using chat" in history and "using debug" in history
    assert "PRIVATE_SOURCE_NOT_A_SNAPSHOT" not in history
    assert "reloaded at invocation time" in history


def test_empty_context_has_no_references_or_replay():
    thread = context([])
    assert get_conversation_file_list(thread) == []
    assert get_conversation_image_list(thread) == []
    assert build_conversation_history(thread) == ("", 0)


def test_directories_are_references_without_recursive_expansion(tmp_path):
    (tmp_path / "nested.py").write_text("MUST_NOT_BE_EMBEDDED")
    history, _ = build_conversation_history(context([turn("inspect directory", files=[str(tmp_path)])]))
    assert str(tmp_path) in history
    assert "nested.py" not in history
    assert "MUST_NOT_BE_EMBEDDED" not in history
