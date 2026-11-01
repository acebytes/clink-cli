"""Legacy Claude callers must not create a parallel policy path."""

from unittest.mock import AsyncMock, Mock

import pytest

from clink.agents.base import CLIAgentError
from clink.agents.claude import ClaudeAgent
from clink.contracts import BackendError


@pytest.mark.asyncio
async def test_legacy_claude_agent_preserves_policy_failure(monkeypatch):
    backend = Mock()
    backend.run = AsyncMock(side_effect=BackendError("policy_unverified", "Policy unverified."))
    monkeypatch.setattr("clink.backend.get_backend", lambda: backend)
    agent = ClaudeAgent(Mock(name="client"))
    with pytest.raises(CLIAgentError) as caught:
        await agent.run(role=Mock(name="role"), prompt="request", system_prompt="secret prompt", files=[], images=[])
    assert caught.value.code == "policy_unverified"
    assert "secret prompt" not in str(caught.value)
    assert backend.run.call_args.args[0].system_prompt == "secret prompt"
