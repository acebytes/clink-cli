"""Regression coverage for strict, atomically loaded CLI manifests."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import clink.registry as registry_module
from clink.registry import ClinkRegistry, RegistryLoadError


@dataclass
class RegistrySources:
    bundled: Path
    legacy: Path
    user: Path
    explicit: Path
    startup: Path
    env: dict[str, str] = field(default_factory=dict)

    def write(self, *, source: str = "bundled", filename: str = "client.json", **overrides: Any) -> Path:
        directory = getattr(self, source)
        directory.mkdir(parents=True, exist_ok=True)
        prompt_path = directory / "prompt.txt"
        prompt_path.write_text("Give concise, read-only advice.\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "name": "codex",
            "command": "codex",
            "roles": {"default": {"prompt_path": "prompt.txt"}},
            **overrides,
        }
        path = directory / filename
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path


@pytest.fixture
def sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RegistrySources:
    startup = tmp_path / "startup"
    startup.mkdir()
    sources = RegistrySources(
        bundled=tmp_path / "bundled",
        legacy=tmp_path / "legacy",
        user=tmp_path / "user",
        explicit=tmp_path / "explicit",
        startup=startup,
    )
    monkeypatch.setattr(registry_module, "CONFIG_DIR", sources.bundled)
    monkeypatch.setattr(registry_module, "LEGACY_USER_CONFIG_DIR", sources.legacy)
    monkeypatch.setattr(registry_module, "USER_CONFIG_DIR", sources.user)
    monkeypatch.setattr(registry_module, "get_env", lambda name, *args, **kwargs: sources.env.get(name))
    monkeypatch.chdir(startup)
    return sources


@pytest.mark.parametrize("name", ["codex", "claude", "gemini"])
def test_builtin_names_infer_the_registered_adapter(sources: RegistrySources, name: str) -> None:
    sources.write(name=name, command=name)

    client = ClinkRegistry().get_client(name.upper())

    assert client.adapter == name
    assert client.executable == (name,)
    assert client.working_dir == sources.startup.resolve()
    assert client.allowed_roots == (sources.startup.resolve(),)
    assert client.get_role(None).prompt_text == "Give concise, read-only advice.\n"


@pytest.mark.parametrize("adapter", ["codex", "claude", "gemini", "agy", "grok"])
def test_custom_client_selects_registered_adapter(sources: RegistrySources, adapter: str) -> None:
    sources.write(name="operator_wrapper", adapter=adapter, command="operator-wrapper", default_model="chosen-model")

    client = ClinkRegistry().get_client("operator_wrapper")

    assert client.adapter == adapter
    assert client.default_model == "chosen-model"


@pytest.mark.parametrize(
    "model",
    [
        "",
        " ",
        " leading-space",
        "trailing-space ",
        "--model=other",
        "-m",
        "model\x00name",
        "model\nname",
        "model\x7fname",
        "m" * 257,
    ],
)
@pytest.mark.parametrize("location", ["client", "role"])
def test_invalid_model_defaults_are_rejected(sources: RegistrySources, model: str, location: str) -> None:
    overrides = (
        {"default_model": model}
        if location == "client"
        else {"roles": {"default": {"prompt_path": "prompt.txt", "model": model}}}
    )
    sources.write(**overrides)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("adapter", [None, "unregistered", "clink.agents.codex:CodexAgent", 42])
def test_custom_client_requires_a_registered_adapter(sources: RegistrySources, adapter: Any) -> None:
    overrides = {"adapter": adapter} if adapter is not None else {}
    sources.write(name="operator_wrapper", **overrides)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_missing_schema_version_warns_and_accepts_legacy_manifest(
    sources: RegistrySources, caplog: pytest.LogCaptureFixture
) -> None:
    path = sources.write()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    del manifest["schema_version"]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="clink.registry"):
        loaded = ClinkRegistry()

    assert loaded.list_clients() == ["codex"]
    assert "schema_version" in caplog.text


@pytest.mark.parametrize("version", [0, 2, True, "1", 1.0, None])
def test_schema_version_is_strict_integer_one(sources: RegistrySources, version: Any) -> None:
    sources.write(schema_version=version)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("field_name", ["parser", "runner", "unknown", "output_to_file"])
def test_unknown_client_fields_are_rejected(sources: RegistrySources, field_name: str) -> None:
    sources.write(**{field_name: None})

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_unknown_role_fields_are_rejected(sources: RegistrySources) -> None:
    sources.write(roles={"default": {"prompt_path": "prompt.txt", "permission_mode": "acceptEdits"}})

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("name", ["", " bad", "bad name", "../codex", "a" * 65])
def test_client_names_must_be_safe_identifiers(sources: RegistrySources, name: str) -> None:
    sources.write(name=name, adapter="codex")

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("role_name", ["", "bad name", "../default", "a" * 65])
def test_role_names_must_be_safe_identifiers(sources: RegistrySources, role_name: str) -> None:
    sources.write(roles={role_name: {"prompt_path": "prompt.txt"}})

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("args", ["--verbose", [1], [True], [None], None, {"flag": "--verbose"}])
@pytest.mark.parametrize("location", ["client", "role"])
def test_argument_fields_require_lists_of_strings(sources: RegistrySources, args: Any, location: str) -> None:
    overrides = (
        {"additional_args": args}
        if location == "client"
        else {"roles": {"default": {"prompt_path": "prompt.txt", "role_args": args}}}
    )
    sources.write(name="claude", command="claude", **overrides)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize(
    "args",
    [
        ["--model", "other-model"],
        ["--model=other-model"],
        ["--permission-mode", "acceptEdits"],
        ["--dangerously-skip-permissions"],
        ["--output-format", "json"],
        ["--resume", "session-id"],
        ["--mcp-config", "other.json"],
        ["--settings", "other.json"],
        ["--unknown-flag"],
        ["positional-prompt"],
    ],
)
@pytest.mark.parametrize("location", ["client", "role", "command"])
def test_unsafe_launch_arguments_are_rejected(sources: RegistrySources, args: list[str], location: str) -> None:
    overrides: dict[str, Any] = {"name": "claude", "command": "claude"}
    if location == "client":
        overrides["additional_args"] = args
    elif location == "role":
        overrides["roles"] = {"default": {"prompt_path": "prompt.txt", "role_args": args}}
    else:
        overrides["command"] = " ".join(["claude", *args])
    sources.write(**overrides)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_safe_claude_verbose_arguments_are_preserved(sources: RegistrySources) -> None:
    sources.write(
        name="claude",
        command="claude --verbose",
        additional_args=["--verbose"],
        roles={"default": {"prompt_path": "prompt.txt", "role_args": ["--verbose"], "model": "role-model"}},
    )

    client = ClinkRegistry().get_client("claude")

    assert "--verbose" in [*client.executable, *client.config_args]
    assert client.roles["default"].role_args == ("--verbose",)
    assert client.roles["default"].model == "role-model"


@pytest.mark.parametrize("adapter", ["codex", "gemini", "agy", "grok"])
def test_other_adapters_do_not_accept_claude_arguments(sources: RegistrySources, adapter: str) -> None:
    sources.write(name="wrapper", adapter=adapter, additional_args=["--verbose"])

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_only_allowlisted_environment_values_are_preserved(sources: RegistrySources) -> None:
    environment = {
        "PATH": "/operator/bin:/usr/bin",
        "HOME": "/operator/home",
        "USER": "operator",
        "LOGNAME": "operator",
        "TMPDIR": "/operator/tmp",
        "TMP": "/operator/tmp",
        "TEMP": "/operator/tmp",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "LC_CTYPE": "UTF-8",
        "TZ": "UTC",
        "CODEX_HOME": "/operator/codex",
        "CLAUDE_CONFIG_DIR": "/operator/claude",
    }
    sources.write(env=environment)

    assert ClinkRegistry().get_client("codex").env == environment


@pytest.mark.parametrize(
    "key", ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NODE_OPTIONS", "PYTHONPATH", "LD_PRELOAD", "CLINK_DELEGATION_DEPTH"]
)
def test_environment_cannot_override_credentials_or_runtime_policy(sources: RegistrySources, key: str) -> None:
    sources.write(env={key: "operator-supplied-value"})

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("value", [1, True, None, ["/operator/bin"]])
def test_environment_values_must_be_strings(sources: RegistrySources, value: Any) -> None:
    sources.write(env={"PATH": value})

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_relative_paths_resolve_against_declaring_manifest(sources: RegistrySources) -> None:
    sources.bundled.mkdir()
    workspace = sources.bundled / "workspace"
    workspace.mkdir()
    sources.write(command="./bin/operator-cli", working_dir="workspace", allowed_roots=["."])

    client = ClinkRegistry().get_client("codex")

    assert client.executable == (str((sources.bundled / "bin" / "operator-cli").resolve()),)
    assert client.working_dir == workspace.resolve()
    assert client.allowed_roots == (sources.bundled.resolve(),)
    assert client.roles["default"].prompt_path == (sources.bundled / "prompt.txt").resolve()


def test_missing_source_relative_prompt_does_not_fall_back_to_project_root(sources: RegistrySources) -> None:
    sources.write(roles={"default": {"prompt_path": "systemprompts/clink/default.txt"}})

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_omitted_roots_allow_only_the_configured_working_directory(sources: RegistrySources) -> None:
    sources.bundled.mkdir()
    workspace = sources.bundled / "workspace"
    workspace.mkdir()
    sources.write(working_dir="workspace")

    client = ClinkRegistry().get_client("codex")

    assert client.working_dir == workspace.resolve()
    assert client.allowed_roots == (workspace.resolve(),)


def test_registry_captures_startup_directory_for_subsequent_loads(
    sources: RegistrySources, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources.write()
    loaded = ClinkRegistry()
    elsewhere = sources.startup.parent / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    loaded._load()

    assert loaded.get_client("codex").working_dir == sources.startup.resolve()
    assert loaded.get_client("codex").allowed_roots == (sources.startup.resolve(),)


@pytest.mark.parametrize("source_kind", ["directory", "file"])
def test_relative_explicit_config_source_stays_anchored_to_startup_directory(
    sources: RegistrySources, monkeypatch: pytest.MonkeyPatch, source_kind: str
) -> None:
    sources.explicit = sources.startup / "clients"
    manifest_path = sources.write(source="explicit", default_model="startup-model")
    source_path = sources.explicit if source_kind == "directory" else manifest_path
    sources.env[registry_module.CONFIG_ENV_VAR] = str(source_path.relative_to(sources.startup))
    loaded = ClinkRegistry()
    elsewhere = sources.startup.parent / "elsewhere"
    shadow_directory = elsewhere / "clients"
    shadow_directory.mkdir(parents=True)
    shadow_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shadow_manifest["default_model"] = "shadow-model"
    (shadow_directory / manifest_path.name).write_text(json.dumps(shadow_manifest), encoding="utf-8")
    (shadow_directory / "prompt.txt").write_text("Shadow instructions", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    loaded._load()

    client = loaded.get_client("codex")
    assert client.default_model == "startup-model"
    assert client.working_dir == sources.startup.resolve()
    assert client.roles["default"].prompt_path == (sources.explicit / "prompt.txt").resolve()
    assert client.roles["default"].prompt_text == "Give concise, read-only advice.\n"


@pytest.mark.parametrize("field_name", ["working_dir", "allowed_roots"])
@pytest.mark.parametrize("kind", ["missing", "file"])
def test_workspace_paths_must_be_existing_directories(sources: RegistrySources, field_name: str, kind: str) -> None:
    candidate = sources.startup / kind
    if kind == "file":
        candidate.write_text("not a directory", encoding="utf-8")
    value = str(candidate) if field_name == "working_dir" else [str(candidate)]
    sources.write(**{field_name: value})

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_working_directory_must_be_inside_an_allowed_root(sources: RegistrySources) -> None:
    other_root = sources.startup.parent / "other-root"
    other_root.mkdir()
    sources.write(allowed_roots=[str(other_root)])

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("source", ["user", "explicit"])
def test_documented_agy_manifest_allows_external_project_and_preserves_roles(
    sources: RegistrySources, source: str
) -> None:
    checkout = Path(__file__).resolve().parents[1]
    documentation = (checkout / "docs" / "cli-configuration.md").read_text(encoding="utf-8")
    example = documentation.split("## Antigravity projects outside the checkout\n", 1)[1]
    example = example.split("```json\n", 1)[1].split("```", 1)[0]
    project = sources.startup.parent / "external-project"
    project.mkdir()
    manifest = json.loads(
        example.replace("/absolute/path/to/clink-mcp-server", str(checkout)).replace(
            "/absolute/path/to/project", str(project)
        )
    )
    sources.write(name="agy", adapter="agy", command="agy")
    path = sources.write(source=source, **manifest)
    if source == "explicit":
        sources.env[registry_module.CONFIG_ENV_VAR] = str(path)

    client = ClinkRegistry().get_client("agy")

    assert client.working_dir == project.resolve()
    assert client.allowed_roots == (project.resolve(),)
    assert sources.startup.resolve() not in client.allowed_roots
    assert client.native_task.enabled is True
    assert set(client.roles) == set(client.native_task.roles) == {"default", "planner", "codereviewer"}
    for roles in (client.roles, client.native_task.roles):
        for role in roles.values():
            assert role.prompt_path.parent == checkout / "systemprompts" / "clink"
            assert role.prompt_text == role.prompt_path.read_text(encoding="utf-8")


def test_directory_symlinks_cannot_bypass_allowed_roots(sources: RegistrySources) -> None:
    root = sources.startup / "root"
    outside = sources.startup.parent / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    sources.write(working_dir=str(root / "escape"), allowed_roots=[str(root)])

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_prompt_text_is_read_once_and_retained_after_its_source_changes(
    sources: RegistrySources, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources.write()
    prompt = (sources.bundled / "prompt.txt").resolve()
    real_open = Path.open
    reads = []

    def record_open(path: Path, *args: Any, **kwargs: Any):
        if path.resolve() == prompt and (args[0] if args else kwargs.get("mode", "r")) in {"r", "rb"}:
            reads.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", record_open)
    loaded = ClinkRegistry()
    prompt.write_text("replacement content", encoding="utf-8")
    prompt.unlink()

    assert loaded.get_client("codex").get_role("default").prompt_text == "Give concise, read-only advice.\n"
    assert reads == [prompt]


def test_prompt_reads_are_bounded(sources: RegistrySources, monkeypatch: pytest.MonkeyPatch) -> None:
    sources.write()
    monkeypatch.setattr(registry_module, "MAX_PROMPT_BYTES", 32)
    (sources.bundled / "prompt.txt").write_bytes(b"a" * 33)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("kind", ["directory", "invalid_utf8"])
def test_unreadable_prompt_content_fails_configuration(sources: RegistrySources, kind: str) -> None:
    sources.write()
    prompt = sources.bundled / "prompt.txt"
    if kind == "directory":
        prompt.unlink()
        prompt.mkdir()
    else:
        prompt.write_bytes(b"\xff")

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_source_precedence_replaces_whole_client(sources: RegistrySources, caplog: pytest.LogCaptureFixture) -> None:
    sources.write(
        default_model="bundled",
        roles={"default": {"prompt_path": "prompt.txt"}, "planner": {"prompt_path": "prompt.txt"}},
    )
    legacy = sources.write(source="legacy", default_model="legacy")
    user = sources.write(source="user", default_model="user")
    explicit = sources.write(source="explicit", default_model="explicit")
    sources.env[registry_module.CONFIG_ENV_VAR] = str(sources.explicit)

    with caplog.at_level(logging.WARNING, logger="clink.registry"):
        client = ClinkRegistry().get_client("codex")

    assert client.default_model == "explicit"
    assert list(client.roles) == ["default"]
    assert "legacy" in caplog.text.lower() or "deprecated" in caplog.text.lower()
    sources.env.pop(registry_module.CONFIG_ENV_VAR)
    explicit.unlink()
    assert ClinkRegistry().get_client("codex").default_model == "user"
    user.unlink()
    assert ClinkRegistry().get_client("codex").default_model == "legacy"
    legacy.unlink()
    assert ClinkRegistry().get_client("codex").default_model == "bundled"


def test_explicit_manifest_file_has_highest_priority(sources: RegistrySources) -> None:
    sources.write(default_model="bundled")
    sources.write(source="user", default_model="user")
    explicit = sources.write(source="explicit", default_model="explicit")
    sources.env[registry_module.CONFIG_ENV_VAR] = str(explicit)

    assert ClinkRegistry().get_client("codex").default_model == "explicit"


@pytest.mark.parametrize("source", ["bundled", "legacy", "user", "explicit"])
def test_duplicate_clients_in_one_source_are_rejected(sources: RegistrySources, source: str) -> None:
    sources.write(source=source, filename="a.json", name="codex")
    sources.write(source=source, filename="b.json", name="CODEX")
    if source == "explicit":
        sources.env[registry_module.CONFIG_ENV_VAR] = str(sources.explicit)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_missing_optional_sources_are_harmless(sources: RegistrySources) -> None:
    sources.write()

    assert ClinkRegistry().list_clients() == ["codex"]


def test_missing_explicit_source_is_an_error(sources: RegistrySources) -> None:
    sources.write()
    sources.env[registry_module.CONFIG_ENV_VAR] = str(sources.explicit)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize("contents", ["{", "{}", "null", "[]"])
def test_invalid_manifests_fail_startup(sources: RegistrySources, contents: str) -> None:
    path = sources.write()
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


@pytest.mark.parametrize(
    "contents",
    [
        '{"schema_version": 1, "name": "codex", "name": "claude"}',
        '{"roles": {"default": {}, "default": {}}}',
        '{"env": {"PATH": "first", "PATH": "second"}}',
    ],
)
def test_duplicate_json_fields_are_rejected(sources: RegistrySources, contents: str) -> None:
    path = sources.write()
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(RegistryLoadError, match="Duplicate JSON field"):
        ClinkRegistry()


def test_manifest_reads_are_bounded(sources: RegistrySources, monkeypatch: pytest.MonkeyPatch) -> None:
    sources.write()
    monkeypatch.setattr(registry_module, "MAX_CONFIG_BYTES", 32)

    with pytest.raises(RegistryLoadError, match="exceeds"):
        ClinkRegistry()


@pytest.mark.parametrize("failure", ["environment", "unknown_field", "invalid_type", "invalid_json"])
def test_rejected_secret_values_are_absent_from_diagnostics(
    sources: RegistrySources, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    secret = "synthetic-credential-do-not-echo"
    overrides: dict[str, Any] = {}
    if failure == "environment":
        overrides["env"] = {"OPENAI_API_KEY": secret}
    elif failure == "unknown_field":
        overrides["credential"] = secret
    elif failure == "invalid_type":
        overrides["timeout_seconds"] = secret
    path = sources.write(**overrides)
    if failure == "invalid_json":
        path.write_text('{"credential": "' + secret + '",', encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="clink.registry"), pytest.raises(RegistryLoadError) as error:
        ClinkRegistry()

    assert secret not in str(error.value)
    assert secret not in caplog.text


@pytest.mark.parametrize("command", [None, "", "   ", "'unterminated"])
def test_executable_command_must_parse_to_nonempty_argv(sources: RegistrySources, command: Any) -> None:
    sources.write(command=command)

    with pytest.raises(RegistryLoadError):
        ClinkRegistry()


def test_missing_executables_do_not_trigger_discovery_or_launch_at_load(
    sources: RegistrySources, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources.write(command="certainly-not-an-installed-cli")

    def forbidden_probe(*args: Any, **kwargs: Any) -> None:
        pytest.fail("registry load must not discover or execute CLI programs")

    monkeypatch.setattr(shutil, "which", forbidden_probe)
    monkeypatch.setattr(subprocess, "run", forbidden_probe)
    monkeypatch.setattr(subprocess, "Popen", forbidden_probe)

    assert ClinkRegistry().get_client("codex").executable == ("certainly-not-an-installed-cli",)


def test_failed_load_preserves_all_previously_loaded_clients(sources: RegistrySources) -> None:
    sources.write(filename="a.json", default_model="original")
    sources.write(filename="b.json", name="claude", command="claude")
    loaded = ClinkRegistry()
    original_codex = loaded.get_client("codex")
    original_claude = loaded.get_client("claude")
    sources.write(filename="a.json", default_model="replacement")
    broken = sources.bundled / "b.json"
    broken.write_text("{", encoding="utf-8")

    with pytest.raises(RegistryLoadError):
        loaded._load()

    assert loaded.get_client("codex") is original_codex
    assert loaded.get_client("claude") is original_claude
    assert loaded.get_client("codex").default_model == "original"
    assert loaded.list_clients() == ["claude", "codex"]


def test_failed_initial_load_does_not_poison_singleton(
    sources: RegistrySources, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry_module, "_REGISTRY", None)
    path = sources.write(schema_version=2)

    with pytest.raises(RegistryLoadError):
        registry_module.get_registry()

    assert registry_module._REGISTRY is None
    path.unlink()
    sources.write()
    loaded = registry_module.get_registry()
    assert registry_module.get_registry() is loaded
    assert loaded.list_clients() == ["codex"]


def test_client_and_role_discovery_remain_sorted(sources: RegistrySources) -> None:
    sources.write(
        filename="z.json", roles={"reviewer": {"prompt_path": "prompt.txt"}, "default": {"prompt_path": "prompt.txt"}}
    )
    sources.write(filename="a.json", name="claude", command="claude")
    loaded = ClinkRegistry()

    assert loaded.list_clients() == ["claude", "codex"]
    assert loaded.list_roles("CODEX") == ["default", "reviewer"]
    with pytest.raises(KeyError, match="missing"):
        loaded.get_client("missing")
    with pytest.raises(KeyError, match="missing"):
        loaded.get_client("codex").get_role("missing")
