"""Compatibility facade for Codex; policy lives in the shared backend."""

from .base import BaseCLIAgent


class CodexAgent(BaseCLIAgent):
    """Delegate through the shared backend without nonzero-exit recovery."""
