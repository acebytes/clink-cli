"""Explicit server project binding stays independent of cwd and environment selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import clink.registry as registry_module
from clink.registry import ClinkRegistry, RegistryLoadError, get_registry


@pytest.fixture
def isolated_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {name: tmp_path / name for name in ("bundled", "legacy", "user", "startup")}
    paths["startup"].mkdir()
    monkeypatch.chdir(paths["startup"])
    monkeypatch.setattr(registry_module, "CONFIG_DIR", paths["bundled"])
    monkeypatch.setattr(registry_module, "LEGACY_USER_CONFIG_DIR", paths["legacy"])
    monkeypatch.setattr(registry_module, "USER_CONFIG_DIR", paths["user"])
    monkeypatch.setattr(registry_module, "get_env", lambda name: None)
    monkeypatch.setattr(registry_module, "_REGISTRY", None)
    return paths


def write_manifest(directory: Path, *, model: str = "bundled", **overrides) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "prompt.txt").write_text(f"Advice for {model}.\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "name": "codex",
        "command": "codex",
        "default_model": model,
        "roles": {"default": {"prompt_path": "prompt.txt"}},
        **overrides,
    }
    path = directory / "codex.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_lazy_public_registry_exports_preserve_the_api():
    from clink import ClinkRegistry as exported_registry
    from clink import get_registry as exported_get_registry

    assert exported_registry is ClinkRegistry
    assert exported_get_registry is get_registry


def test_unknown_public_exports_raise_attribute_error():
    import clink

    with pytest.raises(AttributeError, match="module 'clink' has no attribute 'missing_helper'"):
        _ = clink.missing_helper


def test_distinct_projects_resolve_the_same_cli_and_role_names_independently(isolated_sources, tmp_path):
    write_manifest(isolated_sources["bundled"])
    registries = []
    for name in ("project one", "project two"):
        project = tmp_path / name
        write_manifest(project / ".clink" / "cli_clients", model=name)
        registries.append(ClinkRegistry(project_dir=project))

    for registry, name in zip(registries, ("project one", "project two"), strict=True):
        client = registry.get_client("codex")
        project = (tmp_path / name).resolve()
        assert registry.project_dir == project
        assert client.working_dir == project
        assert client.allowed_roots == (project,)
        assert client.default_model == name
        assert client.roles["default"].prompt_text == f"Advice for {name}.\n"


def test_project_source_has_whole_client_precedence_between_user_and_explicit(isolated_sources, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project_source = project / ".clink" / "cli_clients"
    for name in ("bundled", "legacy", "user"):
        write_manifest(
            isolated_sources[name],
            model=name,
            roles={"default": {"prompt_path": "prompt.txt"}, "reviewer": {"prompt_path": "prompt.txt"}},
        )
    write_manifest(project_source, model="project")
    explicit = isolated_sources["startup"] / "explicit"
    write_manifest(explicit, model="explicit")
    registry = ClinkRegistry(project_dir=project)

    assert registry.get_client("codex").default_model == "project"
    assert registry.list_roles("codex") == ["default"]
    monkeypatch.setattr(registry_module, "get_env", lambda name: "explicit")
    registry.reload()
    assert registry.get_client("codex").default_model == "explicit"
    assert [source for source, _, _ in registry._config_sources()] == [
        isolated_sources["bundled"],
        isolated_sources["legacy"],
        isolated_sources["user"],
        project_source,
        explicit,
    ]


def test_project_defaults_also_apply_to_bundled_clients_without_local_manifests(isolated_sources, tmp_path):
    write_manifest(isolated_sources["bundled"])
    project = tmp_path / "project"
    project.mkdir()

    client = ClinkRegistry(project_dir=project).get_client("codex")

    assert client.working_dir == project
    assert client.allowed_roots == (project,)


def test_manifest_relative_paths_keep_their_source_directory_anchor(isolated_sources, tmp_path):
    project = tmp_path / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    source = project / ".clink" / "cli_clients"
    write_manifest(source, command="./bin/wrapper", working_dir="../../nested", allowed_roots=["../.."])

    client = ClinkRegistry(project_dir=project).get_client("codex")

    assert client.executable == (str(source / "bin" / "wrapper"),)
    assert client.working_dir == nested
    assert client.allowed_roots == (project,)
    assert client.roles["default"].prompt_path == source / "prompt.txt"


def test_binding_and_explicit_config_anchor_survive_reload_after_cwd_changes(isolated_sources, tmp_path, monkeypatch):
    project = tmp_path / "project"
    write_manifest(project / ".clink" / "cli_clients", model="project")
    write_manifest(isolated_sources["startup"] / "explicit", model="explicit")
    monkeypatch.setattr(registry_module, "get_env", lambda name: "explicit")
    alias = tmp_path / "project-alias"
    alias.symlink_to(project, target_is_directory=True)
    registry = ClinkRegistry(project_dir=alias)
    other = tmp_path / "other"
    write_manifest(other / ".clink" / "cli_clients", model="other")
    write_manifest(other / "explicit", model="wrong-explicit")
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)
    monkeypatch.chdir(other)

    registry.reload()

    assert registry.project_dir == project
    assert registry.get_client("codex").working_dir == project
    assert registry.get_client("codex").default_model == "explicit"
    with pytest.raises(AttributeError):
        registry.project_dir = other


def test_no_project_autodetection_parent_walk_or_environment_binding(isolated_sources, tmp_path, monkeypatch):
    write_manifest(isolated_sources["bundled"])
    parent = tmp_path / "parent"
    write_manifest(parent / ".clink" / "cli_clients", model="parent")
    child = parent / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.setenv("CLINK_PROJECT_DIR", str(parent))
    monkeypatch.setenv("PROJECT_DIR", str(parent))
    monkeypatch.setattr(
        registry_module, "get_env", lambda name: str(parent) if name in {"CLINK_PROJECT_DIR", "PROJECT_DIR"} else None
    )

    unbound = ClinkRegistry()
    bound = ClinkRegistry(project_dir=child)

    assert unbound.project_dir is None
    assert unbound.get_client("codex").working_dir == child
    assert unbound.get_client("codex").default_model == "bundled"
    assert bound.get_client("codex").default_model == "bundled"


@pytest.mark.parametrize("contents", ["{", "{}", "[]"])
def test_invalid_project_manifests_preserve_the_previous_complete_snapshot(isolated_sources, tmp_path, contents):
    write_manifest(isolated_sources["bundled"])
    project = tmp_path / "project"
    manifest = write_manifest(project / ".clink" / "cli_clients", model="original")
    registry = ClinkRegistry(project_dir=project)
    original = registry.get_client("codex")
    manifest.write_text(contents, encoding="utf-8")

    with pytest.raises(RegistryLoadError):
        registry.reload()

    assert registry.project_dir == project
    assert registry.get_client("codex") is original


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_project_binding_does_not_cache_the_singleton(isolated_sources, tmp_path, kind):
    write_manifest(isolated_sources["bundled"])
    invalid = tmp_path / kind
    if kind == "file":
        invalid.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RegistryLoadError, match="project_dir"):
        get_registry(project_dir=invalid)

    assert registry_module._REGISTRY is None
    valid = tmp_path / "valid"
    valid.mkdir()
    assert get_registry(project_dir=valid).project_dir == valid


def test_invalid_initial_project_manifest_does_not_cache_the_singleton(isolated_sources, tmp_path):
    write_manifest(isolated_sources["bundled"])
    project = tmp_path / "project"
    manifest = write_manifest(project / ".clink" / "cli_clients")
    manifest.write_text("{", encoding="utf-8")

    with pytest.raises(RegistryLoadError):
        get_registry(project_dir=project)

    assert registry_module._REGISTRY is None
    write_manifest(manifest.parent)
    assert get_registry(project_dir=project).project_dir == project


def test_bound_singleton_allows_zero_argument_access_and_rejects_rebinding(isolated_sources, tmp_path):
    write_manifest(isolated_sources["bundled"])
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    alias = tmp_path / "first-alias"
    alias.symlink_to(first, target_is_directory=True)
    registry = get_registry(project_dir=first)

    assert get_registry() is registry
    assert get_registry(project_dir=alias) is registry
    with pytest.raises(RegistryLoadError, match="different project binding"):
        get_registry(project_dir=second)
    assert get_registry() is registry
    assert registry.get_client("codex").working_dir == first


def test_unbound_singleton_cannot_gain_project_sources_after_initialization(isolated_sources, tmp_path):
    write_manifest(isolated_sources["bundled"])
    registry = get_registry()
    project = tmp_path / "project"
    write_manifest(project / ".clink" / "cli_clients", model="late")

    with pytest.raises(RegistryLoadError, match="different project binding"):
        get_registry(project_dir=project)

    assert get_registry() is registry
    assert registry.get_client("codex").default_model == "bundled"


@pytest.mark.parametrize("console_argv", [False, True])
def test_server_run_binds_before_starting_async_main(isolated_sources, tmp_path, monkeypatch, console_argv):
    import server

    project = tmp_path / "project"
    write_manifest(project / ".clink" / "cli_clients", model="selected")
    calls = []

    async def main():
        registry = get_registry()
        calls.append(registry)
        assert registry.project_dir == project
        assert registry.get_client("codex").default_model == "selected"

    monkeypatch.setattr(server, "main", main)
    arguments = ["--project", str(project)]
    if console_argv:
        monkeypatch.setattr("sys.argv", ["clink-mcp-server", *arguments])
        server.run()
    else:
        server.run(arguments)

    assert calls == [get_registry()]


def test_server_run_without_options_keeps_the_startup_cwd(isolated_sources, monkeypatch):
    import server

    write_manifest(isolated_sources["bundled"])
    calls = []

    async def main():
        calls.append(get_registry().get_client("codex").working_dir)

    monkeypatch.setattr(server, "main", main)
    server.run([])

    assert calls == [isolated_sources["startup"]]
    assert get_registry().project_dir is None


@pytest.mark.parametrize(
    "arguments,message",
    [
        (["--project"], "expected one argument"),
        (["--project", ""], "nonempty directory path"),
        (["--project", "   "], "nonempty directory path"),
        (["--project", "missing"], "existing real directory"),
        (["--unknown"], "unrecognized arguments"),
    ],
)
def test_server_project_parse_errors_do_not_start_mcp(isolated_sources, monkeypatch, capsys, arguments, message):
    import server

    def forbidden_main():
        pytest.fail("parse errors must not start MCP")

    monkeypatch.setattr(server, "main", forbidden_main)
    with pytest.raises(SystemExit) as error:
        server.run(arguments)

    assert error.value.code == 2
    assert message in capsys.readouterr().err
    assert registry_module._REGISTRY is None


def test_server_help_does_not_load_configuration_or_start_mcp(isolated_sources, capsys):
    import server

    with pytest.raises(SystemExit) as error:
        server.run(["--help"])

    assert error.value.code == 0
    assert "--project EXISTING_DIR" in capsys.readouterr().out
    assert registry_module._REGISTRY is None
