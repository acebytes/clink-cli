"""Compatibility facade for Gemini; execution remains admission-gated."""

from .base import BaseCLIAgent


class GeminiAgent(BaseCLIAgent):
    """Never turn native CLI errors into successful content."""
