"""Configuration for clink-mcp-server, derived from PAL MCP Server (Apache-2.0)."""

from utils.env import get_env

__version__ = "0.2.1"
__updated__ = "2026-11-02"
__author__ = "acebytes; derived from Beehive Innovations / Fahad Gilani"
SOURCE_REPOSITORY = "https://github.com/BeehiveInnovations/pal-mcp-server"
SOURCE_COMMIT = "7afc7c1cc96e23992c8f105f960132c657883bb1"
FORK_REPOSITORY = "https://github.com/acebytes/clink-mcp-server"

# Compatibility constants used by existing workflow schemas. They no longer
# select a provider or configure model inference; unsupported controls warn.
DEFAULT_MODEL = "auto"
IS_AUTO_MODE = False
TEMPERATURE_ANALYTICAL = 1.0
TEMPERATURE_BALANCED = 1.0
TEMPERATURE_CREATIVE = 1.0
DEFAULT_THINKING_MODE_THINKDEEP = get_env("DEFAULT_THINKING_MODE_THINKDEEP", "high") or "high"
DEFAULT_CONSENSUS_TIMEOUT = 1800.0
DEFAULT_CONSENSUS_MAX_INSTANCES_PER_COMBINATION = 1
LOCALE = get_env("LOCALE", "") or ""

# Byte budgets, independent of a model provider's token allocation.
MCP_PROMPT_SIZE_LIMIT = 1024 * 1024


def _response_limit() -> int:
    raw = get_env("MAX_MCP_OUTPUT_TOKENS")
    try:
        configured = int(raw) if raw else 25000
    except (TypeError, ValueError):
        configured = 25000
    if configured <= 0:
        configured = 25000
    # This conservative byte estimate is an output budget, not tokenization.
    return min(256 * 1024, max(1024, configured * 4))


MCP_RESPONSE_SIZE_LIMIT = _response_limit()
