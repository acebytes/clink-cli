"""Protected native-task launch recipe, independent of advisory admission."""

from __future__ import annotations

import hashlib
import json

# Keep this native baseline separate: changing it must not alter the Codex recipe.
NATIVE_ENVIRONMENT_KEYS = (
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
)


def native_arguments(adapter: str) -> tuple[str, ...]:
    """Retain native permissions/configuration while denying unattended prompts."""
    if adapter != "claude":
        return ()
    return ("--print", "--output-format", "stream-json", "--verbose", "--permission-prompts", "none")


def native_recipe(adapter: str = "claude") -> dict:
    return {
        "adapter": adapter,
        "profile": "native_task_v1",
        "argv": list(native_arguments(adapter)),
        "optional_arguments": {"model": "--model", "native_session_resume": "--resume"},
        "environment_baseline_names": list(NATIVE_ENVIRONMENT_KEYS),
        "environment_additions": "operator-owned inherited names; required names checked before launch",
        "fixed_environment": {"CLINK_DELEGATION_DEPTH": "1"},
        "configuration": "native user/project configuration and CLI-owned authentication",
        "permissions": "native policy; unattended prompts denied without bypass",
        "prompt_channel": "stdin",
        "session_persistence": "native",
    }


def native_recipe_digest(adapter: str = "claude") -> str:
    return hashlib.sha256(
        json.dumps(native_recipe(adapter), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
