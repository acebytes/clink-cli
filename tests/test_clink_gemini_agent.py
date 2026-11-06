"""Unavailable adapters remain blocked through the legacy facade."""

from unittest.mock import AsyncMock, Mock

import pytest

from clink.agents.base import CLIAgentError
from clink.agents.gemini import GeminiAgent
from clink.contracts import BackendError


@pytest.mark.asyncio
async def test_legacy_gemini_agent_does_not_recover_errors(monkeypatch):
    backend = Mock()
    backend.run = AsyncMock(side_effect=BackendError("unsupported_capability", "Unavailable."))
    monkeypatch.setattr("clink.backend.get_backend", lambda: backend)
    with pytest.raises(CLIAgentError) as caught:
        await GeminiAgent(Mock(name="client")).run(role=Mock(name="role"), prompt="request", files=[], images=[])
    assert caught.value.code == "unsupported_capability"
