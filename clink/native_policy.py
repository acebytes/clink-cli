"""Protected native-task launch recipe, independent of advisory admission."""

from __future__ import annotations

import hashlib
import json

NATIVE_ADAPTERS = frozenset({"claude", "grok", "agy", "codex"})
NATIVE_PROMPT_FILE = "@CLINK_NATIVE_PROMPT_FILE@"

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
    return {
        "claude": ("--print", "--output-format", "stream-json", "--verbose", "--permission-prompts", "none"),
        "grok": ("--prompt-file", NATIVE_PROMPT_FILE, "--output-format", "json"),
        "agy": ("--input-format", "stream-json", "--output-format", "stream-json"),
        "codex": ("exec", "--json", "--color", "never"),
    }.get(adapter, ())


def native_environment_names(adapter: str) -> tuple[str, ...]:
    """Preserve each CLI's documented config location without broad inheritance."""
    return (*NATIVE_ENVIRONMENT_KEYS, "GROK_HOME") if adapter == "grok" else NATIVE_ENVIRONMENT_KEYS


def native_recipe(adapter: str = "claude") -> dict:
    recipe = {
        "adapter": adapter,
        "profile": "native_task_v1",
        "argv": list(native_arguments(adapter)),
        "optional_arguments": {"model": "--model", "native_session_resume": "--resume"},
        "environment_baseline_names": list(native_environment_names(adapter)),
        "environment_additions": "operator-owned inherited names; required names checked before launch",
        "fixed_environment": {"CLINK_DELEGATION_DEPTH": "1"},
        "configuration": "native user/project configuration and CLI-owned authentication",
        "permissions": "native policy; unattended prompts denied without bypass",
        "prompt_channel": "stdin",
        "session_persistence": "native",
    }
    # Preserve the admitted Claude recipe byte for byte; other transports have separate evidence.
    if adapter == "grok":
        recipe.update(
            prompt_channel="private_file",
            prompt_file={"directory_mode": "0700", "file_mode": "0600", "maximum_bytes": 1024 * 1024},
            stdin="empty",
            permissions="native policy; headless permission requests refused without bypass",
        )
    elif adapter == "agy":
        recipe.update(
            optional_arguments={"model": "--model", "native_session_resume": "--conversation"},
            prompt_channel="stdin_single_user_ndjson",
            input_envelope={"event": "user", "message": {"content": "@CLINK_NATIVE_PROMPT@"}},
            permissions="native policy; headless confirmation requests soft-denied without bypass",
        )
    elif adapter == "codex":
        recipe.update(
            optional_arguments={"model": "--model", "project": "--cd"},
            resume_argv=["exec", "--cd", "@CLINK_NATIVE_PROJECT@", "resume", "--json", "@NATIVE_SESSION_ID@", "-"],
            prompt_channel="stdin_explicit_dash",
            permissions="native exec approval semantics; interactive approvals unavailable, no wrapper bypass",
        )
    return recipe


def native_recipe_digest(adapter: str = "claude") -> str:
    return hashlib.sha256(
        json.dumps(native_recipe(adapter), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
