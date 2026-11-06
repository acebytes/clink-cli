"""Adapter-owned advisory arguments and content-addressed verification helpers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

# No operator/caller option can replace or weaken these arguments.
ENVIRONMENT_KEYS = (
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
CODEX_FEATURES_OFF = (
    "hooks",
    "plugin_hooks",
    "plugins",
    "apps",
    "multi_agent",
    "multi_agent_v2",
    "shell_tool",
    "unified_exec",
    "code_mode",
    "code_mode_only",
    "code_mode_host",
    "deferred_executor",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "in_app_browser",
    "image_generation",
    "remote_plugin",
    "plugin_discovery_prefetch",
    "skill_search",
    "skill_mcp_dependency_install",
    "workspace_dependencies",
    "request_permissions_tool",
)
CODEX_OVERRIDES = (
    'approval_policy="never"',
    'sandbox_mode="read-only"',
    "agents.enabled=false",
    "orchestrator.mcp.enabled=false",
    "skills.bundled.enabled=false",
    "skills.include_instructions=false",
    "cloud.skills.enabled=false",
    "project_doc_max_bytes=0",
    'web_search="live"',
    "suppress_unstable_features_warning=true",
    'model_catalog_json="@CLINK_CODEX_MODEL_CATALOG@"',
    "notify=[]",
)


CODEX_CATALOG_PATH = "clink/resources/codex-0.166.0-models-direct.json"
CODEX_CATALOG_SHA256 = "69d3e68d0e78a684c56b1818f1351149902fc61c398e4b69d371dfdea08e4e12"


def rendered_policy_arguments(adapter: str) -> tuple[str, ...]:
    """Resolve only the trusted bundled-resource placeholder, independently of caller cwd."""
    catalog = Path(__file__).resolve().parent.parent / CODEX_CATALOG_PATH
    return tuple(
        (
            "model_catalog_json=" + json.dumps(str(catalog))
            if argument == 'model_catalog_json="@CLINK_CODEX_MODEL_CATALOG@"'
            else argument
        )
        for argument in policy_arguments(adapter)
    )


def policy_arguments(adapter: str) -> tuple[str, ...]:
    if adapter == "codex":
        controls = []
        for feature in CODEX_FEATURES_OFF:
            controls.extend(("--disable", feature))
        controls.extend(("--enable", "skip_host_skill_discovery"))
        for override in CODEX_OVERRIDES:
            controls.extend(("-c", override))
        return (
            "--no-daemon",
            "--ask-for-approval",
            "never",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--strict-config",
            "--sandbox",
            "read-only",
            *controls,
            "--skip-git-repo-check",
            "--json",
        )
    if adapter == "claude":
        return (
            "--restricted",
            "--safe-mode",
            "--no-chrome",
            "--disable-slash-commands",
            "--tools",
            "Read,Glob,Grep,WebSearch,WebFetch",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--permission-prompts",
            "none",
            "--no-session-persistence",
            "--settings",
            '{"disableAllHooks":true}',
            "--print",
            "--output-format",
            "json",
        )
    return ()


def policy_recipe(adapter: str) -> dict:
    arguments = policy_arguments(adapter)
    result = {
        "adapter": adapter,
        "argv": [*arguments, "-"] if adapter == "codex" else list(arguments),
        "environment_allowlist": list(ENVIRONMENT_KEYS),
        "fixed_environment": {"CLINK_DELEGATION_DEPTH": "1"},
        "native_auth_location_preserved": True,
        "prompt_channel": "stdin",
    }
    if adapter == "codex":
        result["resources"] = [{"path": CODEX_CATALOG_PATH, "sha256": CODEX_CATALOG_SHA256}]
    return result


def recipe_digest(adapter: str) -> str:
    """Bind evidence to exact arguments, environment keys and operational guarantees."""
    return hashlib.sha256(
        json.dumps(policy_recipe(adapter), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def executable_digest(path: str) -> str:
    """Hash the actual executable without retaining its bytes or following a later path swap."""
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        initial = stream.fileno()
        if not stat.S_ISREG(os.fstat(initial).st_mode):
            return "not_a_regular_executable"
        before = os.fstat(initial)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(initial)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        return "changed_during_verification"
    current = Path(path).stat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ):
        return "changed_during_verification"
    return digest.hexdigest()
