"""Synthetic consumer isolation, explicit grants, and publication-state regressions."""

import asyncio
import errno
import fcntl
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import clink.allowed_roots as allowed_module
import clink.cli as cli_module
import clink.registry as registry_module
import clink.setup as project_setup
from clink.allowed_roots import EnrollmentError, approved, prepare_enrollment
from clink.registry import ClinkRegistry, RegistryLoadError
from tests.test_native_generation_service import native_factory as native_factory
from tests.test_native_generation_service import request as native_request


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    selected = tmp_path / "selected"
    selected.mkdir()
    config_dir = tmp_path / "application" / "cli_clients"
    config_dir.mkdir(parents=True, mode=0o700)
    executable = tmp_path / "bin" / "synthetic cli's executable"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)
    monkeypatch.setattr(registry_module, "CONFIG_DIR", tmp_path / "bundled")
    monkeypatch.setattr(registry_module, "LEGACY_USER_CONFIG_DIR", home / ".pal" / "cli_clients")
    monkeypatch.setattr(registry_module, "USER_CONFIG_DIR", home / ".clink" / "cli_clients")
    monkeypatch.setattr(registry_module, "get_env", lambda _name: None)
    return SimpleNamespace(home=home, target=selected, config_dir=config_dir, executable=executable)


def manifest(consumer, cli="codex", **changes):
    return {
        "schema_version": 1,
        "name": cli,
        "adapter": cli,
        "command": shlex.join([str(consumer.executable)]),
        "allowed_roots": [],
        "generation_native": {"inherit_env": [], "required_env": []},
        **changes,
    }


def write_manifest(consumer, cli="codex", **changes):
    path = consumer.config_dir / f"{cli}.json"
    path.write_text(json.dumps(manifest(consumer, cli, **changes), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("cli", ["claude", "codex", "agy", "grok"])
def test_consumer_snapshot_has_no_implicit_working_directory_or_advisory_authority(consumer, cli, monkeypatch):
    write_manifest(consumer, cli)
    monkeypatch.setattr(ClinkRegistry, "_resolve_roles", lambda *_args: pytest.fail("consumer read advisory prompts"))
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    client = registry.get_client(cli)
    assert registry.consumer_config_dir == consumer.config_dir
    assert registry.project_dir is None
    assert client.configuration_scope == "consumer" and client.allowed_roots_explicit
    assert client.executable == (str(consumer.executable),)
    assert client.working_dir is None and client.allowed_roots == ()
    assert client.roles == {} and client.env == {} and client.config_args == ()
    assert client.native_task is None and client.default_model is None
    assert not approved(registry, cli, consumer.target, native=True)
    assert not approved(registry, cli, consumer.home, native=True)
    assert not approved(registry, cli, consumer.target)


def test_consumer_ignores_every_other_source_even_when_globally_malformed(consumer, monkeypatch):
    write_manifest(consumer)
    protected = []
    for directory in (
        registry_module.CONFIG_DIR,
        registry_module.LEGACY_USER_CONFIG_DIR,
        registry_module.USER_CONFIG_DIR,
        consumer.target / ".clink" / "cli_clients",
    ):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "broken.json"
        path.write_bytes(b'{"PRIVATE_INVALID_GLOBAL":')
        protected.append((path, path.read_bytes(), path.stat().st_mtime_ns))
    monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", str(consumer.home / "missing-explicit-config"))
    monkeypatch.setattr(registry_module, "get_env", lambda *_args: pytest.fail("consumer consulted environment source"))
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    assert registry.list_clients() == ["codex"]
    refreshed = prepare_enrollment(registry, "codex", str(consumer.target), native=True).persist()
    assert refreshed.consumer_config_dir == consumer.config_dir
    for path, content, modified in protected:
        assert (path.read_bytes(), path.stat().st_mtime_ns) == (content, modified)


@pytest.mark.parametrize(
    "field", ["schema_version", "name", "adapter", "command", "allowed_roots", "generation_native"]
)
def test_consumer_requires_every_manifest_field(consumer, field):
    data = manifest(consumer)
    del data[field]
    (consumer.config_dir / "codex.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RegistryLoadError):
        ClinkRegistry(consumer_config_dir=consumer.config_dir)


@pytest.mark.parametrize(
    "extra",
    [
        {"working_dir": None},
        {"roles": {}},
        {"native_task": None},
        {"additional_args": []},
        {"args": []},
        {"env": {}},
        {"default_model": None},
        {"timeout_seconds": 30},
        {"unknown": "PRIVATE_INVALID_VALUE"},
    ],
)
def test_consumer_rejects_fields_outside_closed_subset(consumer, extra):
    write_manifest(consumer, **extra)
    with pytest.raises(RegistryLoadError) as error:
        ClinkRegistry(consumer_config_dir=consumer.config_dir)
    assert "PRIVATE_INVALID_VALUE" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": "1"},
        {"schema_version": 2},
        {"name": "wrapper"},
        {"adapter": "gemini"},
        {"adapter": "claude"},
        {"command": "codex"},
        {"command": "./codex"},
        {"command": "/absolute/codex --verbose"},
        {"allowed_roots": None},
        {"generation_native": {"enabled": True}},
        {"generation_native": {"inherit_env": ["CLINK_PRIVATE"]}},
    ],
)
def test_consumer_rejects_invalid_identity_types_commands_and_profiles(consumer, changes):
    write_manifest(consumer, **changes)
    with pytest.raises(RegistryLoadError):
        ClinkRegistry(consumer_config_dir=consumer.config_dir)


@pytest.mark.parametrize("kind", ["malformed", "duplicate", "wrong-filename", "symlink", "directory", "fifo"])
def test_consumer_rejects_invalid_or_unconfined_manifest_source(consumer, kind):
    path = consumer.config_dir / "codex.json"
    if kind == "malformed":
        path.write_bytes(b'{"PRIVATE_INVALID_CONSUMER":')
    elif kind == "duplicate":
        path.write_text('{"name":"codex",' + json.dumps(manifest(consumer))[1:], encoding="utf-8")
    elif kind == "wrong-filename":
        (consumer.config_dir / "other.json").write_text(json.dumps(manifest(consumer)), encoding="utf-8")
    elif kind == "symlink":
        external = consumer.home / "outside.json"
        external.write_text(json.dumps(manifest(consumer)), encoding="utf-8")
        path.symlink_to(external)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path, 0o600)
    with pytest.raises(RegistryLoadError) as error:
        ClinkRegistry(consumer_config_dir=consumer.config_dir)
    assert "PRIVATE_INVALID_CONSUMER" not in str(error.value)


def test_empty_consumer_source_cannot_fall_back_to_bundled_configuration(consumer):
    with pytest.raises(RegistryLoadError):
        ClinkRegistry(consumer_config_dir=consumer.config_dir)


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "symlink"])
def test_consumer_directory_binding_requires_existing_absolute_canonical_directory(consumer, kind):
    if kind == "relative":
        selected = Path("../application/cli_clients")
    elif kind == "missing":
        selected = consumer.home / "missing"
    elif kind == "file":
        selected = consumer.home / "file"
        selected.touch()
    else:
        selected = consumer.home / "alias"
        selected.symlink_to(consumer.config_dir, target_is_directory=True)
    with pytest.raises(RegistryLoadError):
        ClinkRegistry(consumer_config_dir=selected)


def test_consumer_project_binding_is_rejected_and_consumer_binding_is_read_only(consumer, monkeypatch):
    write_manifest(consumer)
    with pytest.raises(RegistryLoadError):
        ClinkRegistry(project_dir=consumer.target, consumer_config_dir=consumer.config_dir)
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    with pytest.raises(AttributeError):
        registry.consumer_config_dir = consumer.home
    monkeypatch.chdir(consumer.target)
    monkeypatch.setenv("CLI_CLIENTS_CONFIG_PATH", str(consumer.home / "different-config"))
    registry.reload()
    assert registry.consumer_config_dir == consumer.config_dir
    assert registry.get_client("codex").allowed_roots == ()


def test_invalid_consumer_reload_retains_prior_snapshot_and_binding(consumer):
    path = write_manifest(consumer, allowed_roots=[str(consumer.target)])
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    previous = registry.get_client("codex")
    path.write_bytes(b'{"PRIVATE_BROKEN_RELOAD":')
    with pytest.raises(RegistryLoadError):
        registry.reload()
    assert registry.consumer_config_dir == consumer.config_dir
    assert registry.get_client("codex") is previous
    assert previous.allowed_roots == (consumer.target,)


@pytest.mark.parametrize("cli", ["claude", "codex", "agy", "grok"])
def test_first_consumer_grant_is_canonical_isolated_and_idempotent(consumer, cli):
    path = write_manifest(consumer, cli)
    original = json.loads(path.read_bytes())
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    plan = prepare_enrollment(registry, cli, str(consumer.target), native=True)
    assert not plan.already_allowed and plan.destination == path
    assert json.loads(path.read_bytes())["allowed_roots"] == []
    refreshed = plan.persist()
    assert refreshed.consumer_config_dir == consumer.config_dir
    assert refreshed.get_client(cli).allowed_roots == (consumer.target,)
    assert path.stat().st_mode & 0o777 == 0o600
    stored = json.loads(path.read_bytes())
    assert stored == {**original, "allowed_roots": [str(consumer.target)]}
    assert approved(refreshed, cli, consumer.target, native=True)
    assert not approved(refreshed, cli, consumer.target, native=False)
    before = path.read_bytes(), path.stat().st_mtime_ns
    repeated = prepare_enrollment(refreshed, cli, str(consumer.target), native=True)
    assert repeated.already_allowed
    assert repeated.persist() is refreshed
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    child = consumer.target / "child"
    child.mkdir()
    assert approved(refreshed, cli, child, native=True)


@pytest.mark.parametrize("change", ["source", "target"])
def test_consumer_compare_and_swap_refuses_changed_source_or_target(consumer, change):
    path = write_manifest(consumer)
    plan = prepare_enrollment(ClinkRegistry(consumer_config_dir=consumer.config_dir), "codex", str(consumer.target))
    if change == "source":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        consumer.target.rename(consumer.target.with_name("old-target"))
        consumer.target.mkdir()
    current = path.read_bytes()
    with pytest.raises(EnrollmentError) as error:
        plan.persist()
    assert error.value.grant_saved is False
    assert isinstance(error.value.error_code, str) and error.value.error_code
    assert path.read_bytes() == current
    assert json.loads(path.read_bytes())["allowed_roots"] == []


@pytest.mark.parametrize("timing", ["before-persist", "before-publication"])
def test_consumer_parent_replacement_never_receives_a_grant(consumer, monkeypatch, timing):
    path = write_manifest(consumer)
    original = path.read_bytes()
    plan = prepare_enrollment(ClinkRegistry(consumer_config_dir=consumer.config_dir), "codex", str(consumer.target))
    moved = consumer.home / "original-application"

    def replace_parent():
        consumer.config_dir.parent.rename(moved)
        consumer.config_dir.mkdir(parents=True)
        path.write_bytes(original)

    if timing == "before-persist":
        replace_parent()
    else:
        check = project_setup._check_unchanged

        def replace_after_check(*args, **kwargs):
            check(*args, **kwargs)
            replace_parent()

        monkeypatch.setattr(project_setup, "_check_unchanged", replace_after_check)
    with pytest.raises(EnrollmentError) as error:
        plan.persist()
    assert error.value.grant_saved is False
    assert path.read_bytes() == original
    assert (moved / "cli_clients" / "codex.json").read_bytes() == original
    assert not list(consumer.home.rglob(".clink-setup-*"))


@pytest.mark.parametrize("directory_failure", [False, True])
def test_consumer_fsync_failure_reports_whether_grant_was_published(consumer, monkeypatch, directory_failure):
    path = write_manifest(consumer)
    original = path.read_bytes()
    plan = prepare_enrollment(ClinkRegistry(consumer_config_dir=consumer.config_dir), "codex", str(consumer.target))
    fsync = os.fsync

    def fail_fsync(descriptor):
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if is_directory is directory_failure:
            raise OSError(errno.EIO, "synthetic fsync failure")
        return fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(EnrollmentError) as error:
        plan.persist()
    assert error.value.grant_saved is directory_failure
    assert isinstance(error.value.error_code, str) and error.value.error_code
    assert path.read_bytes() == (plan.content if directory_failure else original)


def test_consumer_reload_failure_preserves_published_grant_and_original_snapshot(consumer, monkeypatch):
    path = write_manifest(consumer)
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    plan = prepare_enrollment(registry, "codex", str(consumer.target))

    def reload_registry(*args, **kwargs):
        assert kwargs["consumer_config_dir"] == consumer.config_dir
        if path.read_bytes() == plan.content:
            raise RegistryLoadError("synthetic saved reload failure")
        return ClinkRegistry(*args, **kwargs)

    monkeypatch.setattr(allowed_module, "ClinkRegistry", reload_registry)
    with pytest.raises(EnrollmentError) as error:
        plan.persist()
    assert error.value.grant_saved is True
    assert path.read_bytes() == plan.content
    assert registry.get_client("codex").allowed_roots == ()
    assert ClinkRegistry(consumer_config_dir=consumer.config_dir).get_client("codex").allowed_roots == (
        consumer.target,
    )


def test_consumer_allow_json_publishes_one_exact_bounded_result_and_repeats_without_writing(consumer, capsys):
    path = write_manifest(consumer)
    arguments = [
        "--consumer-config-dir",
        str(consumer.config_dir),
        "allow",
        str(consumer.target),
        "--cli",
        "codex",
        "--yes",
        "--json",
    ]
    assert cli_module.main(arguments) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1 and len(output.encode()) < 4096
    result = json.loads(output)
    assert set(result) == {"schema_version", "cli", "project_dir", "status", "grant_saved", "error_code"}
    assert result == {
        "schema_version": 1,
        "cli": "codex",
        "project_dir": str(consumer.target),
        "status": "allowed",
        "grant_saved": True,
        "error_code": None,
    }
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert cli_module.main(arguments) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert set(repeated) == set(result) and repeated["status"] == "already_allowed"
    assert type(repeated["grant_saved"]) is bool and repeated["error_code"] is None
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_consumer_allow_json_failure_after_publication_reports_saved_grant(consumer, monkeypatch, capsys):
    path = write_manifest(consumer)
    fsync = os.fsync

    def fail_directory_fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "synthetic directory fsync failure")
        return fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    assert (
        cli_module.main(
            [
                "--consumer-config-dir",
                str(consumer.config_dir),
                "allow",
                str(consumer.target),
                "--cli",
                "codex",
                "--yes",
                "--json",
            ]
        )
        != 0
    )
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    result = json.loads(output)
    assert set(result) == {"schema_version", "cli", "project_dir", "status", "grant_saved", "error_code"}
    assert result["schema_version"] == 1 and type(result["schema_version"]) is int
    assert result["cli"] == "codex" and result["project_dir"] == str(consumer.target)
    assert result["status"] == "failed" and result["grant_saved"] is True
    assert isinstance(result["error_code"], str) and result["error_code"]
    assert json.loads(path.read_bytes())["allowed_roots"] == [str(consumer.target)]


def test_invalid_consumer_directory_fails_before_reading_request_json(consumer, monkeypatch, capsys):
    async def forbidden_read(_descriptor):
        pytest.fail("invalid consumer binding read generation stdin")

    monkeypatch.setattr(cli_module, "read_request_fd", forbidden_read)
    assert cli_module.main(["--consumer-config-dir", "relative-config", "generate", "--request-json", "-"]) != 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["error"]["code"] == "invalid_config"


def test_consumer_allow_without_json_preserves_text_default(consumer, capsys):
    write_manifest(consumer)
    assert (
        cli_module.main(
            [
                "--consumer-config-dir",
                str(consumer.config_dir),
                "allow",
                str(consumer.target),
                "--cli",
                "codex",
                "--yes",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == "" and "Added" in captured.err


@pytest.mark.parametrize("kind", ["missing", "malformed"])
def test_consumer_allow_configuration_failure_reports_exact_canonical_requested_directory(
    consumer, monkeypatch, capsys, kind
):
    if kind == "malformed":
        path = consumer.config_dir / "codex.json"
        path.write_bytes(b'{"PRIVATE_INVALID_CONFIG":')
    alias = consumer.home / "selected-alias"
    alias.symlink_to(consumer.target, target_is_directory=True)

    async def forbidden_confirmation(*_args, **_kwargs):
        pytest.fail("invalid consumer configuration asked for consent")

    monkeypatch.setattr(cli_module, "confirm_enrollment", forbidden_confirmation)
    assert (
        cli_module.main(
            [
                "--consumer-config-dir",
                str(consumer.config_dir),
                "allow",
                "selected-alias/",
                "--cli",
                "codex",
                "--yes",
                "--json",
            ]
        )
        != 0
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {
        "schema_version": 1,
        "cli": "codex",
        "project_dir": str(consumer.target),
        "status": "failed",
        "grant_saved": False,
        "error_code": "invalid_config",
    }
    assert "PRIVATE_INVALID_CONFIG" not in captured.out + captured.err
    assert "[y/N]" not in captured.err
    if kind == "malformed":
        assert path.read_bytes() == b'{"PRIVATE_INVALID_CONFIG":'
    else:
        assert list(consumer.config_dir.iterdir()) == []


@pytest.mark.parametrize("kind", ["empty", "malformed"])
def test_consumer_configuration_failure_exits_while_generation_request_pipe_remains_open(consumer, kind):
    if kind == "malformed":
        (consumer.config_dir / "codex.json").write_bytes(b'{"PRIVATE_INVALID_CONFIG":')
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "clink.cli",
            "--consumer-config-dir",
            str(consumer.config_dir),
            "generate",
            "--request-json",
            "-",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdin is not None and not process.stdin.closed
        assert process.wait(timeout=3) != 0
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        assert process.stdin is not None and not process.stdin.closed
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
    assert len(stdout.splitlines()) == 1
    result = json.loads(stdout)
    assert result["status"] == "failed" and result["error"]["code"] == "invalid_config"
    assert b"PRIVATE_INVALID_CONFIG" not in stdout + stderr


def test_busy_consumer_enrollment_lock_preserves_manifest_and_reports_no_saved_grant(consumer):
    path = write_manifest(consumer)
    original = path.read_bytes(), path.stat().st_mtime_ns
    plan = prepare_enrollment(
        ClinkRegistry(consumer_config_dir=consumer.config_dir), "codex", str(consumer.target), native=True
    )
    descriptor = os.open(consumer.config_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(EnrollmentError, match="Another directory enrollment is running") as error:
            plan.persist()
        assert error.value.grant_saved is False
        assert (path.read_bytes(), path.stat().st_mtime_ns) == original
        assert json.loads(path.read_bytes())["allowed_roots"] == []
    finally:
        os.close(descriptor)
    assert plan.persist().get_client("codex").allowed_roots == (consumer.target,)


@pytest.mark.asyncio
async def test_native_generation_consumer_registry_requires_explicit_grant_before_synthetic_execution(
    consumer, native_factory, monkeypatch
):
    expected = "synthetic consumer generation completed\n"
    service, _, executable, _, marker = native_factory(text=expected)
    write_manifest(consumer, command=shlex.join([str(executable)]))
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    service.registry = registry
    try:
        with monkeypatch.context() as before_grant:
            before_grant.setattr(
                asyncio,
                "create_subprocess_exec",
                lambda *_args, **_kwargs: pytest.fail("unapproved consumer project launched"),
            )
            refused = await service.run(native_request(consumer.target))
        assert refused["status"] == "failed" and refused["error"]["code"] == "path_not_allowed"
        assert refused["execution"]["side_effects_possible"] is False
        assert not marker.exists() and service.pool.running == 0 and not service.pool._children
        service.registry = prepare_enrollment(registry, "codex", str(consumer.target), native=True).persist()
        completed = await service.run(native_request(consumer.target))
        assert completed["status"] == "completed" and completed["output"] == expected
        assert completed["execution"]["admission"] == "verified"
        assert completed["execution"]["side_effects_possible"] is True
        assert service.registry.consumer_config_dir == consumer.config_dir
        assert marker.exists() and not Path(marker.read_text()).parent.exists()
        assert service.pool.running == 0 and not service.pool._children
    finally:
        await service.shutdown()


@pytest.mark.parametrize("operation", ["persist", "allow-json"])
def test_saved_consumer_grant_rejects_directory_replacement_during_fsync(consumer, monkeypatch, capsys, operation):
    path = write_manifest(consumer)
    registry = ClinkRegistry(consumer_config_dir=consumer.config_dir)
    plan = prepare_enrollment(registry, "codex", str(consumer.target))
    moved = consumer.config_dir.with_name("published-original")
    fsync = os.fsync
    replacements = []

    def replace_published_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not replacements:
            assert path.read_bytes() == plan.content
            consumer.config_dir.rename(moved)
            consumer.config_dir.mkdir()
            path.write_bytes(plan.content)
            replacements.append(path.stat().st_ino)
        return fsync(descriptor)

    monkeypatch.setattr(os, "fsync", replace_published_directory)
    if operation == "persist":
        with pytest.raises(EnrollmentError) as error:
            plan.persist()
        assert error.value.grant_saved is True
    else:
        assert (
            cli_module.main(
                [
                    "--consumer-config-dir",
                    str(consumer.config_dir),
                    "allow",
                    str(consumer.target),
                    "--cli",
                    "codex",
                    "--yes",
                    "--json",
                ]
            )
            == 1
        )
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "failed" and result["grant_saved"] is True
        assert result["project_dir"] == str(consumer.target) and result["error_code"]
    assert replacements
    assert (moved / "codex.json").read_bytes() == plan.content
    assert registry.get_client("codex").allowed_roots == ()
    with pytest.raises(RegistryLoadError):
        registry.reload()


@pytest.mark.parametrize("spelling", ["dot", "double-separator", "trailing-separator"])
def test_consumer_rejects_noncanonical_raw_directory_spelling(consumer, spelling):
    write_manifest(consumer)
    selected = {
        "dot": str(consumer.config_dir) + "/.",
        "double-separator": str(consumer.config_dir.parent) + "//" + consumer.config_dir.name,
        "trailing-separator": str(consumer.config_dir) + "/",
    }[spelling]
    with pytest.raises(RegistryLoadError, match="canonical directory"):
        ClinkRegistry(consumer_config_dir=selected)


@pytest.mark.parametrize("command", ["generate", "allow"])
@pytest.mark.parametrize("spelling", ["dot", "double-separator"])
def test_cli_rejects_noncanonical_raw_consumer_binding_before_io(consumer, monkeypatch, capsys, command, spelling):
    path = write_manifest(consumer)
    original = path.read_bytes()
    selected = (
        str(consumer.config_dir) + "/."
        if spelling == "dot"
        else str(consumer.config_dir.parent) + "//" + consumer.config_dir.name
    )

    async def forbidden_read(_descriptor):
        pytest.fail("noncanonical consumer binding read stdin")

    monkeypatch.setattr(cli_module, "read_request_fd", forbidden_read)
    args = ["--consumer-config-dir", selected, command]
    if command == "generate":
        args.extend(["--request-json", "-"])
    else:
        args.extend([str(consumer.target), "--cli", "codex", "--yes", "--json"])
    assert cli_module.main(args) == 1
    result = json.loads(capsys.readouterr().out)
    if command == "generate":
        assert result["error"]["code"] == "invalid_config"
    else:
        assert result["error_code"] == "invalid_config" and result["grant_saved"] is False
    assert path.read_bytes() == original
