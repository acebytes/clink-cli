"""Project setup uses bundled templates and preserves operator-owned configuration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import clink.setup as project_setup
from clink.constants import CONFIG_DIR, PROJECT_ROOT
from clink.models import CLIClientConfig
from clink.setup import MAX_SETUP_FILE_BYTES, SetupError, main, setup_project


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project with spaces"
    root.mkdir()
    return root


def _tree(root: Path) -> dict[str, bytes | str]:
    result: dict[str, bytes | str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = f"symlink:{os.readlink(path)}"
        elif path.is_dir():
            result[relative] = "directory"
        else:
            result[relative] = path.read_bytes()
    return result


def _registration(project: Path) -> dict:
    return {
        "command": os.path.abspath(sys.executable),
        "args": [str(PROJECT_ROOT / "server.py"), "--project", str(project.resolve())],
    }


def test_fresh_setup_copies_complete_bundled_clients_and_prompts(project: Path) -> None:
    result = setup_project(project)

    bundled_paths = sorted(CONFIG_DIR.glob("*.json"))
    assert {path.name for path in (project / ".clink" / "cli_clients").glob("*.json")} == {
        path.name for path in bundled_paths
    }
    for source in bundled_paths:
        bundled = json.loads(source.read_text())
        generated = json.loads((project / ".clink" / "cli_clients" / source.name).read_text())
        expected = json.loads(source.read_text())
        expected["working_dir"] = "../.."
        expected["allowed_roots"] = ["../.."]
        for section in ("roles", "native_task"):
            if section not in bundled:
                continue
            expected_roles = expected[section] if section == "roles" else expected[section]["roles"]
            bundled_roles = bundled[section] if section == "roles" else bundled[section]["roles"]
            for name, role in bundled_roles.items():
                original = (source.parent / role["prompt_path"]).resolve()
                expected_roles[name]["prompt_path"] = f"../prompts/{original.name}"
                copied = project / ".clink" / "prompts" / original.name
                assert copied.read_bytes() == original.read_bytes()
        assert generated == expected
        CLIClientConfig.model_validate(generated)
    assert json.loads((project / ".mcp.json").read_text()) == {"mcpServers": {"clink": _registration(project)}}
    assert result.project_dir == project.resolve()
    assert not result.unchanged
    assert result.created[-1] == project.resolve() / ".mcp.json"


def test_select_agy_preserves_native_profile_and_never_probes_clients(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("setup must not launch external processes")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(shutil, "which", forbidden)
    setup_project(project, cli_names=["agy", "agy"])

    manifests = list((project / ".clink" / "cli_clients").glob("*.json"))
    assert [path.name for path in manifests] == ["agy.json"]
    generated = json.loads(manifests[0].read_text())
    assert generated["native_task"]["enabled"] is True
    assert set(generated["roles"]) == {"default", "planner", "codereviewer"}
    assert set(generated["native_task"]["roles"]) == {"default", "planner", "codereviewer"}
    assert len(list((project / ".clink" / "prompts").glob("*.txt"))) == 6


def test_relocated_generated_paths_still_resolve_to_relocated_project(project: Path, tmp_path: Path) -> None:
    setup_project(project)
    relocated = tmp_path / "relocated project"
    project.rename(relocated)

    for path in (relocated / ".clink" / "cli_clients").glob("*.json"):
        client = CLIClientConfig.model_validate_json(path.read_bytes())
        assert (path.parent / client.working_dir).resolve(strict=True) == relocated.resolve()
        assert [(path.parent / root).resolve(strict=True) for root in client.allowed_roots] == [relocated.resolve()]
        role_maps = [client.roles]
        if client.native_task is not None:
            role_maps.append(client.native_task.roles)
        for roles in role_maps:
            for role in roles.values():
                prompt = (path.parent / role.prompt_path).resolve(strict=True)
                assert prompt.is_relative_to(relocated / ".clink" / "prompts")
                assert prompt.read_text()
    # Registration is deliberately absolute and requires explicit operator re-registration after moving.
    assert json.loads((relocated / ".mcp.json").read_text())["mcpServers"]["clink"]["args"][-1] == str(project)


def test_repeat_setup_is_byte_and_metadata_identical(project: Path) -> None:
    first = setup_project(project)
    before = _tree(project)
    metadata = {path: path.stat() for path in first.created}

    second = setup_project(project)

    assert not second.created
    assert set(second.unchanged) == set(first.created)
    assert _tree(project) == before
    for path, info in metadata.items():
        assert path.stat().st_ino == info.st_ino
        assert path.stat().st_mtime_ns == info.st_mtime_ns


def test_additional_selection_retains_custom_unselected_clients(project: Path) -> None:
    clients = project / ".clink" / "cli_clients"
    clients.mkdir(parents=True)
    custom = b'{"operator":"not read or copied"}\n'
    (clients / "custom.json").write_bytes(custom)
    (clients / "codex.json").write_bytes(custom)

    setup_project(project, cli_names=["agy"])

    assert (clients / "custom.json").read_bytes() == custom
    assert (clients / "codex.json").read_bytes() == custom
    assert (clients / "agy.json").is_file()


@pytest.mark.parametrize("relative", [".clink/cli_clients/agy.json", ".clink/prompts/native_default.txt"])
def test_conflicting_generated_file_fails_without_mutation(project: Path, relative: str) -> None:
    target = project / relative
    target.parent.mkdir(parents=True)
    target.write_text("operator content\n")
    before = _tree(project)

    with pytest.raises(SetupError, match="Conflicting project file"):
        setup_project(project, cli_names=["agy"])

    assert _tree(project) == before


def test_existing_unrelated_mcp_entries_and_fields_are_preserved(project: Path) -> None:
    original = {
        "operatorField": [1, {"nested": True}],
        "mcpServers": {"other": {"command": "other-mcp", "args": ["space value"], "env": {"TOKEN": "synthetic"}}},
    }
    (project / ".mcp.json").write_text(json.dumps(original))

    setup_project(project, cli_names=["agy"])

    after = json.loads((project / ".mcp.json").read_text())
    assert after == {**original, "mcpServers": {**original["mcpServers"], "clink": _registration(project)}}


def test_identical_registration_preserves_its_original_bytes(project: Path) -> None:
    content = json.dumps({"other": "keep", "mcpServers": {"clink": _registration(project)}}, separators=(",", ":"))
    (project / ".mcp.json").write_text(content)

    result = setup_project(project, cli_names=["agy"])

    assert (project / ".mcp.json").read_text() == content
    assert project / ".mcp.json" in result.unchanged


def test_conflicting_registration_fails_before_creating_clink_directory(project: Path) -> None:
    content = b'{"mcpServers":{"clink":{"command":"operator choice"}}}\n'
    (project / ".mcp.json").write_bytes(content)
    before = _tree(project)

    with pytest.raises(SetupError, match="Conflicting Clink registration"):
        setup_project(project)

    assert _tree(project) == before
    assert not (project / ".clink").exists()


@pytest.mark.parametrize(
    "content",
    [
        b'{"private":"SECRET_EXCERPT",',
        b'{"mcpServers":{},"mcpServers":{}}',
        b'{"other":{"duplicate":1,"duplicate":2}}',
        b'{"mcpServers":[]}',
        b'{"mcpServers":null}',
        b"[]",
        b'{"notJSON":NaN}',
        b"\xff",
        b" " * (MAX_SETUP_FILE_BYTES + 1),
    ],
)
def test_invalid_or_oversized_mcp_fails_without_mutation_or_content_disclosure(project: Path, content: bytes) -> None:
    (project / ".mcp.json").write_bytes(content)
    before = _tree(project)

    with pytest.raises(SetupError) as caught:
        setup_project(project, cli_names=["agy"])

    assert str(project / ".mcp.json") in str(caught.value)
    assert "SECRET_EXCERPT" not in str(caught.value)
    assert _tree(project) == before


@pytest.mark.parametrize(
    "relative,is_directory",
    [
        (".clink", True),
        (".clink/cli_clients", True),
        (".clink/prompts", True),
        (".clink/cli_clients/agy.json", False),
        (".clink/prompts/native_default.txt", False),
        (".mcp.json", False),
    ],
)
def test_symlink_destinations_are_rejected_without_following_them(
    project: Path, tmp_path: Path, relative: str, is_directory: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = outside if is_directory else outside / "operator.txt"
    if not is_directory:
        destination.write_text("operator content")
    link = project / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(destination, target_is_directory=is_directory)
    project_before = _tree(project)
    outside_before = _tree(outside)

    with pytest.raises(SetupError):
        setup_project(project, cli_names=["agy"])

    assert _tree(project) == project_before
    assert _tree(outside) == outside_before


def test_broken_symlink_is_not_treated_as_missing(project: Path, tmp_path: Path) -> None:
    (project / ".mcp.json").symlink_to(tmp_path / "missing")

    with pytest.raises(SetupError):
        setup_project(project)

    assert not (project / ".clink").exists()
    assert (project / ".mcp.json").is_symlink()


def test_nonregular_destination_is_rejected_without_blocking(project: Path) -> None:
    os.mkfifo(project / ".mcp.json")

    with pytest.raises(SetupError, match="regular destination file"):
        setup_project(project)

    assert not (project / ".clink").exists()


def test_missing_and_file_project_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="existing directory"):
        setup_project(tmp_path / "missing")
    file = tmp_path / "file"
    file.write_text("keep")
    with pytest.raises(SetupError, match="existing directory"):
        setup_project(file)
    assert file.read_text() == "keep"


def test_cli_relative_project_and_default_current_directory(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project.parent)
    assert main(["--project", project.name, "--cli", "agy"]) == 0
    monkeypatch.chdir(project)
    assert main(["--cli", "agy"]) == 0
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["clink"] == _registration(project)


def test_interpreter_registration_keeps_virtualenv_symlink(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interpreter = tmp_path / "venv with spaces" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(interpreter))

    setup_project(project, cli_names=["agy"])

    command = json.loads((project / ".mcp.json").read_text())["mcpServers"]["clink"]["command"]
    assert command == str(interpreter)
    assert command != str(interpreter.resolve())


def test_unknown_client_fails_without_creating_files(project: Path) -> None:
    with pytest.raises(SetupError, match="Unknown bundled CLI"):
        setup_project(project, cli_names=["../credentials"])
    assert _tree(project) == {}


def test_setup_import_does_not_read_env_or_import_server() -> None:
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import clink.setup; assert 'utils.env' not in sys.modules; assert 'server' not in sys.modules",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr


def test_detected_registration_edit_is_preserved_and_registration_is_last(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = project / ".mcp.json"
    target.write_text('{"mcpServers":{"other":{"command":"keep"}}}')
    edited = b'{"mcpServers":{"operator":{"command":"edited concurrently"}}}\n'
    original_publish = project_setup._publish
    published: list[tuple[str, ...]] = []

    def edit_before_registration(root_fd, root, planned):
        published.append(planned.parts)
        if planned.parts == (".mcp.json",):
            target.write_bytes(edited)
        original_publish(root_fd, root, planned)

    monkeypatch.setattr(project_setup, "_publish", edit_before_registration)

    with pytest.raises(SetupError, match="Destination changed during setup"):
        setup_project(project, cli_names=["agy"])

    assert target.read_bytes() == edited
    assert published[-1] == (".mcp.json",)
    assert (project / ".clink" / "cli_clients" / "agy.json").is_file()
    assert not list(project.rglob(".clink-setup-*"))


def test_concurrent_new_destination_is_not_overwritten(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_link = os.link
    concurrent = b"operator appeared during publication\n"

    def create_before_link(source, destination, **kwargs):
        if destination == "agy.json":
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=kwargs["dst_dir_fd"])
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(concurrent)
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", create_before_link)

    with pytest.raises(SetupError, match="Destination appeared during setup"):
        setup_project(project, cli_names=["agy"])

    assert (project / ".clink" / "cli_clients" / "agy.json").read_bytes() == concurrent
    assert not (project / ".mcp.json").exists()
    assert not list(project.rglob(".clink-setup-*"))


def test_replaced_destination_parent_is_detected_before_publication(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_check = project_setup._check_unchanged
    parent = project / ".clink" / "prompts"
    moved = project / "operator-moved-prompts"

    def replace_parent_after_file_check(root_fd, root, planned):
        original_check(root_fd, root, planned)
        if parent.is_dir() and list(parent.glob(".clink-setup-*")) and not moved.exists():
            parent.rename(moved)
            parent.mkdir()

    monkeypatch.setattr(project_setup, "_check_unchanged", replace_parent_after_file_check)

    with pytest.raises(SetupError, match="Destination directory changed during setup"):
        setup_project(project, cli_names=["agy"])

    assert not list(parent.iterdir())
    assert not list(moved.iterdir())
    assert not (project / ".mcp.json").exists()


def test_project_directory_lock_prevents_concurrent_setup(project: Path) -> None:
    import fcntl

    descriptor = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SetupError, match="Another setup is running"):
            setup_project(project, cli_names=["agy"])
        assert _tree(project) == {}
    finally:
        os.close(descriptor)


def test_user_manifests_and_explicit_config_are_never_read(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "synthetic-home"
    home.mkdir()
    operator_manifest = home / "operator.json"
    operator_manifest.write_text('{"credential":"SYNTHETIC_PRIVATE_VALUE"}')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", str(operator_manifest))
    original_open = Path.open

    def no_home_reads(path, *args, **kwargs):
        if path.is_relative_to(home):
            pytest.fail("setup must not read user manifests or credentials")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_home_reads)

    setup_project(project, cli_names=["agy"])

    assert not any(b"SYNTHETIC_PRIVATE_VALUE" in value for value in _tree(project).values() if isinstance(value, bytes))


def test_interrupted_setup_can_resume_without_changing_published_files(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_publish = project_setup._publish
    count = 0

    def interrupt(root_fd, root, planned):
        nonlocal count
        count += 1
        if count == 3:
            raise KeyboardInterrupt
        return original_publish(root_fd, root, planned)

    monkeypatch.setattr(project_setup, "_publish", interrupt)
    with pytest.raises(KeyboardInterrupt):
        setup_project(project, cli_names=["agy"])
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    assert not (project / ".mcp.json").exists()
    monkeypatch.setattr(project_setup, "_publish", original_publish)

    result = setup_project(project, cli_names=["agy"])

    assert result.created[-1] == project / ".mcp.json"
    assert result.unchanged
    assert all(path.read_bytes() == content for path, content in before.items())


def test_cli_error_is_actionable_without_traceback(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / ".mcp.json").write_text("invalid secret JSON value")

    assert main(["--project", str(project), "--cli", "agy"]) == 1

    captured = capsys.readouterr()
    assert not captured.out
    assert str(project / ".mcp.json") in captured.err
    assert "secret JSON value" not in captured.err
    assert "Traceback" not in captured.err
