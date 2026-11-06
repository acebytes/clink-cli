"""Provider-independent conversation helper compatibility and replay tests."""

import importlib
import uuid

import pytest

from utils.conversation_memory import (
    CONVERSATION_TIMEOUT_SECONDS,
    MAX_CONVERSATION_TURNS,
    MAX_HISTORY_BYTES,
    add_turn,
    build_conversation_history,
    create_thread,
    get_thread,
    get_thread_chain,
)
from utils.storage_backend import InMemoryStorage


@pytest.fixture
def memory(monkeypatch):
    storage = InMemoryStorage()
    monkeypatch.setattr("utils.conversation_memory.get_storage", lambda: storage)
    yield storage
    storage.shutdown()


def test_create_and_retrieve_thread_preserves_uuid_and_initial_request(memory):
    thread_id = create_thread("chat", {"prompt": "Hello", "model": "native", "absolute_file_paths": ["/test.py"]})
    assert str(uuid.UUID(thread_id)) == thread_id
    context = get_thread(thread_id)
    assert context.thread_id == thread_id
    assert context.tool_name == "chat"
    assert context.initial_context == {"prompt": "Hello", "absolute_file_paths": ["/test.py"]}
    assert context.turns == []
    assert context.workflow_states == {}
    assert CONVERSATION_TIMEOUT_SECONDS == 3 * 3600
    assert MAX_CONVERSATION_TURNS == 50


@pytest.mark.parametrize("thread_id", ["invalid-uuid", "", None, str(uuid.uuid4())])
def test_unavailable_helper_lookup_is_compatible_none(memory, thread_id):
    assert get_thread(thread_id) is None


def test_legacy_turn_updates_keep_attribution_references_and_refresh_expiry(memory):
    now = [0.0]
    memory._clock = lambda: now[0]
    thread = create_thread("chat", {})
    now[0] = CONVERSATION_TIMEOUT_SECONDS - 1
    assert add_turn(thread, "user", "Question", files=["/source.py"], tool_name="chat")
    assert add_turn(thread, "assistant", "Answer", images=["/diagram.png"], tool_name="debug", model_name="native")
    now[0] += 2
    context = get_thread(thread)
    assert [turn.content for turn in context.turns] == ["Question", "Answer"]
    assert context.turns[0].files == ["/source.py"]
    assert context.turns[1].images == ["/diagram.png"]
    assert context.successful_turns == 2
    history, estimate = build_conversation_history(context)
    assert "Question" in history and "Answer" in history
    assert "native using debug" in history
    assert estimate > 0


def test_legacy_turn_limit_counts_trimmed_turns(memory, monkeypatch):
    monkeypatch.setattr("utils.conversation_memory.MAX_CONVERSATION_TURNS", 2)
    thread = create_thread("chat", {})
    assert add_turn(thread, "user", "a" * MAX_HISTORY_BYTES)
    assert add_turn(thread, "assistant", "answer")
    assert len(get_thread(thread).turns) == 1
    assert not add_turn(thread, "user", "over limit")


def test_parent_chain_is_chronological_and_stops_at_circular_reference(memory):
    parent = create_thread("chat", {})
    assert add_turn(parent, "user", "parent request")
    child = create_thread("debug", {}, parent_thread_id=parent)
    assert add_turn(child, "assistant", "child answer")
    assert [context.thread_id for context in get_thread_chain(child)] == [parent, child]
    history, _ = build_conversation_history(get_thread(child))
    assert history.index("parent request") < history.index("child answer")
    context = get_thread(parent)
    context.parent_thread_id = child
    memory.setex(f"thread:{parent}", 60, context.model_dump_json())
    assert len(get_thread_chain(child)) == 2


def test_empty_history_and_expiration_are_explicit_to_helpers(memory):
    thread = create_thread("chat", {})
    assert build_conversation_history(get_thread(thread)) == ("", 0)
    memory._store[f"thread:{thread}"] = (memory._store[f"thread:{thread}"][0], 0)
    assert get_thread(thread) is None
    assert not add_turn(thread, "user", "cannot resurrect")


@pytest.mark.parametrize(
    "setting,value,expected",
    [
        ("MAX_CONVERSATION_TURNS", "7", 7),
        ("MAX_CONVERSATION_TURNS", "0", 50),
        ("MAX_CONVERSATION_TURNS", "bad", 50),
        ("CONVERSATION_TIMEOUT_HOURS", "2", 2),
        ("CONVERSATION_TIMEOUT_HOURS", "-1", 3),
        ("CONVERSATION_TIMEOUT_HOURS", "bad", 3),
    ],
)
def test_retained_environment_corrections(setting, value, expected, monkeypatch):
    import utils.conversation_memory as memory_module

    monkeypatch.setenv(setting, value)
    importlib.reload(memory_module)
    assert getattr(memory_module, setting) == expected
