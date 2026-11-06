"""Replay preserves unavailable paths for explicit backend admission diagnostics."""

from utils.conversation_memory import ConversationTurn, ThreadContext, build_conversation_history


def test_missing_reference_does_not_discard_prior_turn_or_claim_snapshot():
    thread = ThreadContext(
        thread_id="12345678-1234-1234-1234-123456789012",
        created_at="now",
        last_updated_at="now",
        tool_name="debug",
        initial_context={},
        turns=[
            ConversationTurn(
                role="user", timestamp="now", content="Investigate timeout", files=["/nonexistent/missing.py"]
            )
        ],
    )
    history, estimate = build_conversation_history(thread)
    assert "Investigate timeout" in history
    assert "/nonexistent/missing.py" in history
    assert "prior file contents are not retained snapshots" in history
    assert estimate > 0
