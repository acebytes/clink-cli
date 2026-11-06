"""Compatibility facade for Claude; policy lives in the shared backend."""

from .base import BaseCLIAgent


class ClaudeAgent(BaseCLIAgent):
    """Delegate through the shared backend without independent argv injection."""
