"""Legacy Codex callers must obey the common execution policy."""

from unittest.mock import AsyncMock, Mock

import pytest

from clink.agents.base import CLIAgentError
from clink.agents.codex import CodexAgent
from clink.contracts import BackendError


@pytest.mark.asyncio
async def test_legacy_codex_agent_preserves_nonzero_failure(monkeypatch):
    backend = Mock()
    backend.run = AsyncMock(side_effect=BackendError("nonzero_exit", "Delegation failed."))
    monkeypatch.setattr("clink.backend.get_backend", lambda: backend)
    agent = CodexAgent(Mock(name="client"))
    with pytest.raises(CLIAgentError) as caught:
        await agent.run(role=Mock(name="role"), prompt="private request", files=[], images=[])
    assert caught.value.code == "nonzero_exit"
    assert "private request" not in str(caught.value)
    backend.run.assert_awaited_once()
