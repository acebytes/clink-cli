"""Internal defaults and constants for clink."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_STREAM_LIMIT = 10 * 1024 * 1024

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_PROMPTS_DIR = PROJECT_ROOT / "systemprompts" / "clink"
CONFIG_DIR = PROJECT_ROOT / "conf" / "cli_clients"
LEGACY_USER_CONFIG_DIR = Path.home() / ".pal" / "cli_clients"
USER_CONFIG_DIR = Path.home() / ".clink" / "cli_clients"

CHILD_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
    }
)


@dataclass(frozen=True)
class CLIInternalDefaults:
    """Adapter-owned defaults, never an operator permission override mechanism."""

    parser: str
    additional_args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    default_role_prompt: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    runner: str | None = None


# Availability and the complete launch policy belong to the backend's verified adapter contract.
INTERNAL_DEFAULTS: dict[str, CLIInternalDefaults] = {
    "gemini": CLIInternalDefaults(
        parser="gemini_json",
        additional_args=("-o", "json"),
        default_role_prompt=str(BUILTIN_PROMPTS_DIR / "default.txt"),
        runner="gemini",
    ),
    "codex": CLIInternalDefaults(
        parser="codex_jsonl",
        additional_args=("exec",),
        default_role_prompt=str(BUILTIN_PROMPTS_DIR / "default.txt"),
        runner="codex",
    ),
    "claude": CLIInternalDefaults(
        parser="claude_json",
        additional_args=("--print", "--output-format", "json"),
        default_role_prompt=str(BUILTIN_PROMPTS_DIR / "default.txt"),
        runner="claude",
    ),
    "agy": CLIInternalDefaults(
        parser="plain_text",
        default_role_prompt=str(BUILTIN_PROMPTS_DIR / "default.txt"),
        runner="agy",
    ),
    "grok": CLIInternalDefaults(
        parser="plain_text",
        default_role_prompt=str(BUILTIN_PROMPTS_DIR / "default.txt"),
        runner="grok",
    ),
}
