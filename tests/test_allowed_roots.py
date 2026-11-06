"""Isolated operator enrollment and byte-preserving publication tests."""

import copy
import errno
import fcntl
import json
import os
import shlex
import stat

import pytest

import clink.allowed_roots as allowed_module
import clink.registry as registry_module
import clink.setup as project_setup
from clink.allowed_roots import EnrollmentError, prepare_enrollment
from clink.registry import ClinkRegistry, RegistryLoadError


@pytest.fixture
def roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(registry_module, "CONFIG_DIR", tmp_path / "bundled")
    monkeypatch.setattr(registry_module, "LEGACY_USER_CONFIG_DIR", home / ".pal/cli_clients")
    monkeypatch.setattr(registry_module, "USER_CONFIG_DIR", home / ".clink/cli_clients")
    monkeypatch.setattr(registry_module, "get_env", lambda name: None)
    monkeypatch.chdir(home)
    bundled = registry_module.CONFIG_DIR
    bundled.mkdir()
    prompt = bundled / "prompt.txt"
    prompt.write_text("Synthetic role")
    data = {
        "schema_version": 1,
        "name": "codex",
        "command": "codex",
        "roles": {"default": {"prompt_path": "prompt.txt"}},
    }
    (bundled / "arbitrary.json").write_text(json.dumps(data))
    target = tmp_path / "selected"
    target.mkdir()
    return home, target, data


@pytest.mark.parametrize("spelling", ["absolute", "relative", "symlink", "trailing", "tilde"])
def test_canonical_enrollment_and_repeat_noop(roots, spelling):
    home, target, _ = roots
    if spelling == "relative":
        value = "../selected"
    elif spelling == "symlink":
        (home / "alias").symlink_to(target, target_is_directory=True)
        value = "alias"
    elif spelling == "trailing":
        value = str(target) + "/"
    elif spelling == "tilde":
        (home / "alias").symlink_to(target, target_is_directory=True)
        value = "~/alias"
    else:
        value = str(target)
    plan = prepare_enrollment(ClinkRegistry(), "CODEX", value)
    assert plan.target == target.resolve()
    assert not plan.already_allowed
    assert not registry_module.USER_CONFIG_DIR.exists()
    refreshed = plan.persist()
    path = registry_module.USER_CONFIG_DIR / "codex.json"
    assert refreshed.get_client("codex").allowed_roots == (home, target)
    assert path.stat().st_mode & 0o777 == 0o600
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert prepare_enrollment(refreshed, "codex", value).already_allowed
    child = target / "child"
    child.mkdir()
    assert prepare_enrollment(refreshed, "codex", str(child)).already_allowed
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("bad", ["missing", "file", "loop"])
def test_invalid_directory_does_not_create_configuration(roots, bad):
    home, target, _ = roots
    value = home / bad
    if bad == "file":
        value.touch()
    if bad == "loop":
        value.symlink_to(value)
    with pytest.raises(EnrollmentError):
        prepare_enrollment(ClinkRegistry(), "codex", str(value))
    assert not registry_module.USER_CONFIG_DIR.exists()


@pytest.mark.parametrize("style", ["compact", "pretty", "crlf", "missing", "null"])
def test_only_top_level_roots_bytes_change_and_mode_is_preserved(roots, style):
    home, target, data = roots
    user = registry_module.USER_CONFIG_DIR
    user.mkdir(parents=True)
    data["roles"]["default"]["prompt_path"] = str(registry_module.CONFIG_DIR / "prompt.txt")
    data["working_dir"] = str(home)
    if style != "missing":
        data["allowed_roots"] = None if style == "null" else [str(home)]
    content = json.dumps(data, indent=2 if style in {"pretty", "crlf"} else None)
    if style == "crlf":
        content = content.replace("\n", "\r\n")
    path = user / "unrelated-name.json"
    path.write_bytes(content.encode())
    path.chmod(0o640)
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    expected = plan.content
    assert json.loads(expected)["allowed_roots"] == [str(home), str(target)]
    if style not in {"missing", "null"}:
        start = content.index("[", content.index('"allowed_roots"'))
        end = content.index("]", start) + 1
        replacement = json.dumps([str(home), str(target)])
        if style in {"pretty", "crlf"}:
            newline = "\r\n" if style == "crlf" else "\n"
            replacement = (
                "["
                + newline
                + "    "
                + json.dumps(str(home))
                + ","
                + newline
                + "    "
                + json.dumps(str(target))
                + newline
                + "  ]"
            )
        assert expected == (content[:start] + replacement + content[end:]).encode()
    mask = os.umask(0o077)
    try:
        plan.persist()
    finally:
        os.umask(mask)
    assert path.read_bytes() == expected
    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("race", ["target", "source", "new-winner"])
def test_observed_races_do_not_publish(roots, race):
    home, target, data = roots
    registry = ClinkRegistry()
    plan = prepare_enrollment(registry, "codex", str(target))
    if race == "target":
        target.rename(target.with_name("old-target"))
        target.mkdir()
    elif race == "source":
        source = registry_module.CONFIG_DIR / "arbitrary.json"
        source.write_bytes(source.read_bytes() + b" ")
    else:
        user = registry_module.USER_CONFIG_DIR
        user.mkdir(parents=True)
        data["roles"]["default"]["prompt_path"] = str(registry_module.CONFIG_DIR / "prompt.txt")
        (user / "other.json").write_text(json.dumps(data))
    with pytest.raises(EnrollmentError):
        plan.persist()
    assert not plan.destination.exists()


def _operator_manifest(path, home, data, *, model="operator"):
    manifest = copy.deepcopy(data)
    manifest["working_dir"] = str(home)
    manifest["allowed_roots"] = [str(home)]
    manifest["default_model"] = model
    manifest["roles"]["default"]["prompt_path"] = str(registry_module.CONFIG_DIR / "prompt.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


@pytest.mark.parametrize("winner", ["legacy_user", "user", "project", "explicit_file", "explicit_dir", "unbound"])
def test_enrollment_updates_only_effective_arbitrary_named_manifest(roots, monkeypatch, winner):
    home, target, data = roots
    project = home / "bound-project"
    project.mkdir()
    sources = {
        "legacy_user": registry_module.LEGACY_USER_CONFIG_DIR / "not-the-cli-name.json",
        "user": registry_module.USER_CONFIG_DIR / "also-not-the-cli-name.json",
        "project": project / ".clink" / "cli_clients" / "project-custom-name.json",
        "explicit": home / "explicit" / "last-custom-name.JSON",
    }
    active = ["legacy_user"]
    if winner != "legacy_user":
        active.append("user")
    if winner in {"project", "explicit_file", "explicit_dir", "unbound"}:
        active.append("project")
    if winner.startswith("explicit"):
        active.append("explicit")
    for scope in active:
        _operator_manifest(sources[scope], home, data, model=scope)
    if winner.startswith("explicit"):
        explicit = sources["explicit"] if winner == "explicit_file" else sources["explicit"].parent
        monkeypatch.setattr(registry_module, "get_env", lambda name: str(explicit))
    binding = project if winner in {"project", "explicit_file", "explicit_dir"} else None
    registry = ClinkRegistry(project_dir=binding)
    expected_scope = "explicit" if winner.startswith("explicit") else "user" if winner == "unbound" else winner
    destination = sources[expected_scope]
    originals = {path: path.read_bytes() for path in sources.values() if path.exists()}
    plan = prepare_enrollment(registry, "CoDeX", str(target))
    assert plan.destination == destination
    assert registry.manifest_source("codex").path == destination
    refreshed = plan.persist()
    assert refreshed.project_dir == binding
    assert refreshed.get_client("codex").configuration_scope == expected_scope
    assert refreshed.get_client("codex").allowed_roots == (home, target)
    assert refreshed.get_client("codex").default_model == expected_scope
    for path, content in originals.items():
        assert path.read_bytes() == (plan.content if path == destination else content)
    assert not (registry_module.USER_CONFIG_DIR / "codex.json").exists()


def test_bundled_override_retains_relative_path_meanings_and_native_profiles(roots):
    _, target, data = roots
    bundled = registry_module.CONFIG_DIR
    work = bundled / "workspace"
    work.mkdir()
    executable = bundled / "bin" / "synthetic executable"
    executable.parent.mkdir()
    executable.write_text("Never executed", encoding="utf-8")
    native_prompt = bundled / "native prompt.txt"
    native_prompt.write_text("Synthetic native instructions", encoding="utf-8")
    data.update(
        command=shlex.join(["./bin/synthetic executable"]),
        working_dir="workspace",
        allowed_roots=["."],
        default_model="advisory-model",
        generation_native={"inherit_env": ["SYNTHETIC_NAME"], "required_env": []},
        native_task={
            "enabled": True,
            "inherit_env": ["SYNTHETIC_NATIVE_NAME"],
            "required_env": ["SYNTHETIC_NATIVE_NAME"],
            "default_model": "native-model",
            "roles": {"default": {"prompt_path": "native prompt.txt", "description": "Native role"}},
        },
    )
    data["roles"]["reviewer"] = {"prompt_path": "prompt.txt", "model": "review-model"}
    source = bundled / "arbitrary.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    original = source.read_bytes()
    registry = ClinkRegistry()
    before = registry.get_client("codex")
    plan = prepare_enrollment(registry, "codex", str(target))
    refreshed = plan.persist()
    after = refreshed.get_client("codex")
    assert after.executable == (str(executable),)
    assert after.working_dir == work
    assert after.allowed_roots == (bundled, target)
    assert after.native_task == before.native_task
    assert after.generation_native == before.generation_native
    assert after.roles == before.roles
    assert after.model_dump(
        exclude={"allowed_roots", "allowed_roots_explicit", "configuration_scope"}
    ) == before.model_dump(exclude={"allowed_roots", "allowed_roots_explicit", "configuration_scope"})
    assert source.read_bytes() == original
    stored = json.loads(plan.destination.read_bytes())
    assert stored["command"] == shlex.join([str(executable)])
    assert stored["roles"]["reviewer"]["prompt_path"] == str(bundled / "prompt.txt")
    assert stored["native_task"]["roles"]["default"]["prompt_path"] == str(native_prompt)


@pytest.mark.parametrize(
    "invalid", ["malformed", "duplicate_field", "duplicate_nested_field", "nonfinite", "utf8", "duplicate_client"]
)
def test_invalid_or_ambiguous_new_configuration_cannot_be_masked_by_enrollment(roots, invalid):
    home, target, data = roots
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    user = registry_module.USER_CONFIG_DIR
    user.mkdir(parents=True)
    path = user / "concurrent-invalid.json"
    values = {
        "malformed": b"{",
        "duplicate_field": b'{"name":"codex","name":"CODEX"}',
        "duplicate_nested_field": b'{"roles":{"default":{"model":"a","model":"b"}}}',
        "nonfinite": b'{"timeout_seconds":NaN}',
        "utf8": b'{"name":"\xff"}',
    }
    if invalid == "duplicate_client":
        _operator_manifest(path, home, data)
        second = copy.deepcopy(data)
        second["name"] = "CODEX"
        _operator_manifest(user / "same-client-again.json", home, second)
    else:
        path.write_bytes(values[invalid])
    unchanged = {entry: entry.read_bytes() for entry in user.iterdir()}
    with pytest.raises(EnrollmentError, match="No grant saved"):
        plan.persist()
    assert not plan.destination.exists()
    assert {entry: entry.read_bytes() for entry in user.iterdir()} == unchanged


def test_cooperative_lock_contention_does_not_create_an_override(roots):
    _, target, _ = roots
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    plan.lock_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(plan.lock_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(EnrollmentError, match="Another directory enrollment is running") as error:
            plan.persist()
        assert "No grant saved" in str(error.value)
        assert not plan.destination.exists()
    finally:
        os.close(descriptor)
    assert plan.persist().get_client("codex").allowed_roots[-1] == target


@pytest.mark.parametrize("scope", ["user", "project"])
def test_same_manifest_shares_lock_when_also_selected_explicitly(roots, monkeypatch, scope):
    home, target, data = roots
    project = home / "project"
    project.mkdir()
    destination = (
        registry_module.USER_CONFIG_DIR / "custom.json"
        if scope == "user"
        else project / ".clink" / "cli_clients" / "custom.json"
    )
    _operator_manifest(destination, home, data)
    binding = project if scope == "project" else None
    ordinary = prepare_enrollment(ClinkRegistry(project_dir=binding), "codex", str(target))
    monkeypatch.setattr(registry_module, "get_env", lambda name: str(destination))
    explicit = prepare_enrollment(ClinkRegistry(project_dir=binding), "codex", str(target))
    assert ordinary.destination == explicit.destination
    before = destination.read_bytes()
    descriptor = os.open(ordinary.lock_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(EnrollmentError, match="Another directory enrollment is running"):
            explicit.persist()
    finally:
        os.close(descriptor)
    assert destination.read_bytes() == before


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["write", "file_fsync", "directory_fsync"])
def test_publication_failures_preserve_original_or_report_possible_saved_grant(roots, monkeypatch, existing, failure):
    home, target, data = roots
    if existing:
        _operator_manifest(registry_module.USER_CONFIG_DIR / "custom.json", home, data)
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    real_fdopen = os.fdopen
    real_fsync = os.fsync

    class FailedWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def write(self, content):
            raise OSError(errno.EIO, "synthetic write failure")

    def fdopen(descriptor, mode="r", *args, **kwargs):
        stream = real_fdopen(descriptor, mode, *args, **kwargs)
        return FailedWriter(stream) if failure == "write" and mode == "wb" else stream

    def fsync(descriptor):
        directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if (failure == "directory_fsync" and directory) or (failure == "file_fsync" and not directory):
            raise OSError(errno.EIO, "synthetic fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fdopen", fdopen)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(EnrollmentError) as error:
        plan.persist()
    message = str(error.value)
    assert str(plan.destination) in message
    assert plan.entry in message
    if failure == "directory_fsync":
        assert "Grant saved" in message
        assert plan.destination.read_bytes() == plan.content
    elif existing:
        assert "No grant saved" in message
        assert plan.destination.read_bytes() == plan.previous
    else:
        assert "No grant saved" in message
        assert not plan.destination.exists()
    assert not list(home.rglob(".clink-setup-*"))


def test_reload_failure_after_publication_reports_saved_configuration_without_rollback(roots, monkeypatch):
    _, target, _ = roots
    registry = ClinkRegistry()
    plan = prepare_enrollment(registry, "codex", str(target))
    original_roots = registry.get_client("codex").allowed_roots

    def fail_after_publication(*args, **kwargs):
        if plan.destination.exists() and plan.destination.read_bytes() == plan.content:
            raise RegistryLoadError("synthetic postpublication reload failure")
        return ClinkRegistry(*args, **kwargs)

    monkeypatch.setattr(allowed_module, "ClinkRegistry", fail_after_publication)
    with pytest.raises(EnrollmentError, match="Grant saved") as error:
        plan.persist()
    assert str(plan.destination) in str(error.value)
    assert plan.entry in str(error.value)
    assert plan.destination.read_bytes() == plan.content
    assert registry.get_client("codex").allowed_roots == original_roots
    assert ClinkRegistry().get_client("codex").allowed_roots[-1] == target


@pytest.mark.parametrize("timing", ["before_persist", "before_publish"])
def test_replaced_configuration_parent_never_receives_the_grant(roots, monkeypatch, timing):
    home, target, data = roots
    destination = _operator_manifest(registry_module.USER_CONFIG_DIR / "custom.json", home, data)
    original = destination.read_bytes()
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    parent = registry_module.USER_CONFIG_DIR.parent
    moved = home / "replaced-config"

    def replace_parent():
        parent.rename(moved)
        destination.parent.mkdir(parents=True)
        destination.write_bytes(original)

    if timing == "before_persist":
        replace_parent()
    else:
        real_check = project_setup._check_unchanged

        def replace_after_file_check(*args, **kwargs):
            real_check(*args, **kwargs)
            replace_parent()

        monkeypatch.setattr(project_setup, "_check_unchanged", replace_after_file_check)
    with pytest.raises(EnrollmentError):
        plan.persist()
    assert destination.read_bytes() == original
    assert (moved / "cli_clients" / "custom.json").read_bytes() == original
    assert not list(home.rglob(".clink-setup-*"))


def test_embedded_roots_text_does_not_change_byte_preserving_top_level_edit(roots):
    home, target, data = roots
    destination = _operator_manifest(registry_module.USER_CONFIG_DIR / "custom.json", home, data)
    manifest = json.loads(destination.read_bytes())
    manifest["roles"]["default"]["description"] = 'Unicode é and embedded {"allowed_roots": ["quoted \\"text"]}'
    original = json.dumps(manifest, indent=3, ensure_ascii=False).replace("\n", "\r\n").encode("utf-8")
    destination.write_bytes(original)
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    start = original.index(b'"allowed_roots": ') + len(b'"allowed_roots": ')
    end = original.index(b"]", start) + 1
    replacement = (
        "[\r\n      " + json.dumps(str(home)) + ",\r\n      " + json.dumps(str(target)) + "\r\n   ]"
    ).encode()
    expected = original[:start] + replacement + original[end:]
    assert plan.content == expected
    plan.persist()
    assert destination.read_bytes() == expected


@pytest.mark.parametrize("explicit", ["file", "directory"])
def test_explicitly_selected_bundled_manifest_is_never_edited(roots, monkeypatch, explicit):
    _, target, _ = roots
    source = registry_module.CONFIG_DIR / "arbitrary.json"
    original = source.read_bytes()
    selected = source if explicit == "file" else source.parent
    monkeypatch.setattr(registry_module, "get_env", lambda name: str(selected))
    with pytest.raises(EnrollmentError, match="bundled manifest") as error:
        prepare_enrollment(ClinkRegistry(), "codex", str(target))
    assert json.dumps(str(target)) in str(error.value)
    assert source.read_bytes() == original
    assert not registry_module.USER_CONFIG_DIR.exists()


def test_portable_override_preserves_disabled_profile_with_omitted_role_map(roots):
    _, target, data = roots
    data["native_task"] = {"enabled": False}
    source = registry_module.CONFIG_DIR / "arbitrary.json"
    source.write_text(json.dumps(data))
    original = ClinkRegistry().get_client("codex")
    refreshed = prepare_enrollment(ClinkRegistry(), "codex", str(target)).persist()
    assert refreshed.get_client("codex").native_task == original.native_task


@pytest.mark.parametrize("replacement", ["target", "source-inode"])
def test_identity_changes_after_temporary_write_prevent_publication(roots, monkeypatch, replacement):
    home, target, data = roots
    source = _operator_manifest(registry_module.USER_CONFIG_DIR / "custom.json", home, data)
    original = source.read_bytes()
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    check = project_setup._check_unchanged

    def replace_after_check(*args, **kwargs):
        check(*args, **kwargs)
        if replacement == "target":
            target.rename(target.with_name("replaced-target"))
            target.mkdir()
        else:
            source.rename(source.with_suffix(".previous"))
            source.write_bytes(original)

    monkeypatch.setattr(project_setup, "_check_unchanged", replace_after_check)
    with pytest.raises(EnrollmentError, match="No grant saved"):
        plan.persist()
    assert source.read_bytes() == original
    assert not list(home.rglob(".clink-setup-*"))


def test_registry_read_only_loading_does_not_require_posix_flags(roots, monkeypatch):
    from types import SimpleNamespace

    _, _, _ = roots
    portable_os = SimpleNamespace(name="nt", O_RDONLY=os.O_RDONLY, open=os.open, fdopen=os.fdopen, fstat=os.fstat)
    monkeypatch.setattr(registry_module, "os", portable_os)
    registry = ClinkRegistry()
    assert registry.get_client("codex").executable == ("codex",)
    assert registry.manifest_source("codex").content


@pytest.mark.parametrize("array", ["[]", "[ ]", "[\n  ]", "[\r\n  ]"])
def test_first_empty_root_append_preserves_surrounding_manifest_bytes(tmp_path, array):
    original = ('{ "name": "codex", "allowed_roots": ' + array + ', "unchanged": [1, 2] }\n').encode()
    roots = [str(tmp_path), str(tmp_path / "child")]
    result = allowed_module._replace_roots(original, roots, tmp_path / "manifest.json")
    assert json.loads(result)["allowed_roots"] == roots
    prefix, suffix = original.split(array.encode())
    assert result.startswith(prefix) and result.endswith(suffix)
