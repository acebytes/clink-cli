"""Confine fixture trust to temporary homes and disposable standalone Git repositories."""

import fcntl
import importlib.util
import json
import os
import stat
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "native_fixture_trust", Path(__file__).resolve().parents[1] / "scripts" / "native_fixture_trust.py"
)
trust = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trust)


def git(project, *args):
    return subprocess.run(
        ["/usr/bin/git", "-C", str(project), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env={"PATH": "/usr/bin:/bin", "HOME": str(project), "GIT_CONFIG_NOSYSTEM": "1"},
    )


def create_repo(path):
    path.mkdir()
    git(path, "init", "--quiet")
    (path / ".clink-native-verification-fixture").write_text("Disposable offline fixture\n")
    return path.resolve(strict=True)


def folder_entry(project, trusted=True, decided_at=17):
    return (
        f"[folders.{json.dumps(str(project))}]\n"
        f"trusted = {'true' if trusted else 'false'}\n"
        f"decided_at = {decided_at}\n"
    ).encode()


@pytest.fixture
def fixture(tmp_path):
    project = create_repo(tmp_path / "fixture-repo")
    home = tmp_path / "synthetic-grok-home"
    home.mkdir(mode=0o700)
    return SimpleNamespace(
        project=project,
        home=home,
        store=home / "trusted_folders.toml",
        lock=home / "trusted_folders.toml.lock",
    )


def assert_no_temporary_publications(home):
    assert not list(home.glob(".clink-fixture-trust-*"))


@pytest.mark.parametrize("before", [b"", b"# original comment\n", b"# original comment without newline"])
def test_grant_adds_only_fixture_entry_and_cleanup_restores_exact_original_bytes(fixture, before):
    fixture.store.write_bytes(before)
    fixture.store.chmod(0o600)
    with trust.grok_fixture_trust(fixture.project, fixture.home) as receipt:
        granted = fixture.store.read_bytes()
        assert granted.startswith(before)
        document = trust._document(granted)
        assert set(document["folders"]) == {str(fixture.project)}
        assert document["folders"][str(fixture.project)]["trusted"] is True
        assert type(document["folders"][str(fixture.project)]["decided_at"]) is int
        assert receipt == {"fixture_trust_prepared": True, "fixture_trust_removed": False}
        assert stat.S_IMODE(fixture.store.stat().st_mode) == 0o600
        lock_identity = fixture.lock.stat().st_ino
    assert fixture.store.read_bytes() == before
    assert receipt == {"fixture_trust_prepared": True, "fixture_trust_removed": True}
    assert fixture.lock.is_file() and fixture.lock.stat().st_ino == lock_identity
    assert stat.S_IMODE(fixture.lock.stat().st_mode) == 0o600
    assert_no_temporary_publications(fixture.home)


def test_missing_store_grant_does_not_touch_authentication_or_other_configuration(fixture):
    auth = fixture.home / "authentication-sentinel.json"
    config = fixture.home / "settings.toml"
    auth.write_bytes(b"private authentication sentinel")
    config.write_bytes(b"private unrelated configuration")
    before = {auth.name: auth.read_bytes(), config.name: config.read_bytes()}
    with trust.grok_fixture_trust(fixture.project, fixture.home):
        assert trust._document(fixture.store.read_bytes())["folders"][str(fixture.project)]["trusted"]
    assert fixture.store.read_bytes() == b""
    assert {auth.name: auth.read_bytes(), config.name: config.read_bytes()} == before
    assert {path.name for path in fixture.home.iterdir()} == {
        auth.name,
        config.name,
        fixture.store.name,
        fixture.lock.name,
    }
    assert_no_temporary_publications(fixture.home)


def test_body_exception_restores_own_grant_and_preserves_original_exception(fixture):
    before = b'# retained comment\n[preferences]\ncolor = "blue"\n'
    fixture.store.write_bytes(before)
    with pytest.raises(RuntimeError, match="synthetic body failure"):
        with trust.grok_fixture_trust(fixture.project, fixture.home) as receipt:
            raise RuntimeError("synthetic body failure")
    assert fixture.store.read_bytes() == before
    assert receipt["fixture_trust_removed"] is True
    assert_no_temporary_publications(fixture.home)


def test_cleanup_preserves_concurrent_unrelated_entries_updates_and_adjacent_comments(fixture):
    before = (
        b"# initial trust comment\n"
        + folder_entry("/synthetic/existing-project", trusted=False)
        + b'\n[appearance]\nstyle = "unchanged private value"\n'
    )
    fixture.store.write_bytes(before)
    addition = (
        b"# concurrent unrelated comment\n" + folder_entry("/synthetic/concurrent-project") + b"# final comment\n"
    )
    with trust.grok_fixture_trust(fixture.project, fixture.home):
        # Native writers coordinate with the same persistent sidecar between our two phases.
        with fixture.lock.open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            updated = fixture.store.read_bytes().replace(b"trusted = false", b"trusted = true", 1) + addition
            fixture.store.write_bytes(updated)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    assert fixture.store.read_bytes() == before.replace(b"trusted = false", b"trusted = true", 1) + addition
    document = trust._document(fixture.store.read_bytes())
    assert set(document["folders"]) == {"/synthetic/existing-project", "/synthetic/concurrent-project"}
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize("change", ["trust_value", "extra_field", "formatting"])
def test_changed_own_grant_refuses_cleanup_without_replacing_any_bytes(fixture, change):
    fixture.store.write_bytes(b"# unrelated original comment\n")
    with pytest.raises(trust.FixtureTrustError, match="cleanup|formatting"):
        with trust.grok_fixture_trust(fixture.project, fixture.home) as receipt:
            changed = fixture.store.read_bytes()
            if change == "trust_value":
                changed = changed.replace(b"trusted = true", b"trusted = false")
            elif change == "extra_field":
                changed += b'new_owner_note = "preserve me"\n'
            else:
                changed = changed.replace(b"trusted = true", b"trusted=true")
            fixture.store.write_bytes(changed)
    assert fixture.store.read_bytes() == changed
    assert receipt == {"fixture_trust_prepared": True, "fixture_trust_removed": False}
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize("existing_trust", [True, False])
def test_existing_fixture_key_is_never_overwritten(fixture, existing_trust):
    before = b"# existing native decision\n" + folder_entry(fixture.project, trusted=existing_trust)
    fixture.store.write_bytes(before)
    with pytest.raises(trust.FixtureTrustError, match="already exists"):
        with trust.grok_fixture_trust(fixture.project, fixture.home):
            pytest.fail("Existing native trust decision was overwritten")
    assert fixture.store.read_bytes() == before
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize(
    "before", [b"private malformed value = [", b"\xffprivate invalid UTF8", b"folders = 1\n", b"folders = []\n"]
)
def test_malformed_store_is_refused_without_echoing_configuration(fixture, before):
    fixture.store.write_bytes(before)
    with pytest.raises(trust.FixtureTrustError) as caught:
        with trust.grok_fixture_trust(fixture.project, fixture.home):
            pytest.fail("Invalid native trust store was changed")
    assert fixture.store.read_bytes() == before
    assert "private" not in str(caught.value)
    assert str(fixture.project) not in str(caught.value)
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize("size", [trust.MAX_STORE_BYTES, trust.MAX_STORE_BYTES + 1])
def test_oversized_store_or_grant_is_rejected_before_any_publication(fixture, size):
    before = b"#" + b"x" * (size - 1)
    fixture.store.write_bytes(before)
    with pytest.raises(trust.FixtureTrustError, match="limit"):
        with trust.grok_fixture_trust(fixture.project, fixture.home):
            pytest.fail("Oversized native trust store was changed")
    assert fixture.store.read_bytes() == before
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize("target_name", ["store", "lock"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "group_writable", "foreign_owner"])
def test_unsafe_store_and_lock_identities_are_refused(fixture, tmp_path, monkeypatch, target_name, kind):
    fixture.store.write_bytes(b"# original store\n")
    target = getattr(fixture, target_name)
    if not target.exists():
        target.write_bytes(b"")
    target.chmod(0o600)
    original_store = fixture.store.read_bytes()
    sentinel = tmp_path / "unrelated-file"
    sentinel.write_bytes(b"# unrelated original bytes\n")
    if kind == "symlink":
        target.unlink()
        target.symlink_to(sentinel)
    elif kind == "hardlink":
        os.link(target, sentinel.with_name("additional-link"))
    elif kind == "group_writable":
        target.chmod(0o620)
    else:
        original_lstat = Path.lstat

        def foreign_lstat(path):
            observed = original_lstat(path)
            if path == target:
                fields = list(observed)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return observed

        monkeypatch.setattr(Path, "lstat", foreign_lstat)
    with pytest.raises(trust.FixtureTrustError):
        with trust.grok_fixture_trust(fixture.project, fixture.home):
            pytest.fail("Unsafe native trust file was admitted")
    assert sentinel.read_bytes() == b"# unrelated original bytes\n"
    if target_name != "store" or kind != "symlink":
        assert fixture.store.read_bytes() == original_store
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize("target_name", ["store", "lock"])
def test_descriptor_owner_change_after_path_validation_is_refused(fixture, monkeypatch, target_name):
    fixture.store.write_bytes(b"# unchanged original store\n")
    fixture.lock.write_bytes(b"")
    fixture.store.chmod(0o600)
    fixture.lock.chmod(0o600)
    target_ino = getattr(fixture, target_name).stat().st_ino
    original_fstat = os.fstat

    def changed_fstat(fd):
        observed = original_fstat(fd)
        if observed.st_ino == target_ino:
            fields = list(observed)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return observed

    monkeypatch.setattr(trust.os, "fstat", changed_fstat)
    with pytest.raises(trust.FixtureTrustError, match="identity changed"):
        with trust.grok_fixture_trust(fixture.project, fixture.home):
            pytest.fail("Changed native trust descriptor was admitted")
    assert fixture.store.read_bytes() == b"# unchanged original store\n"
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize("invalid", ["missing_marker", "nested_directory", "symlink_git", "worktree_git_file"])
def test_nonstandalone_or_unmarked_projects_are_refused(fixture, tmp_path, invalid):
    project = fixture.project
    if invalid == "missing_marker":
        (project / ".clink-native-verification-fixture").unlink()
    elif invalid == "nested_directory":
        project = project / "nested-project"
        project.mkdir()
        (project / ".clink-native-verification-fixture").write_text("Nested inside enclosing Git root")
    elif invalid == "symlink_git":
        project = tmp_path / "symlink-git-project"
        project.mkdir()
        (project / ".git").symlink_to(fixture.project / ".git", target_is_directory=True)
        (project / ".clink-native-verification-fixture").write_text("Symlink Git metadata")
    else:
        git(
            fixture.project,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "Synthetic root",
        )
        project = tmp_path / "linked-worktree"
        git(fixture.project, "worktree", "add", "--detach", "--quiet", str(project))
        (project / ".clink-native-verification-fixture").write_text("Linked Git worktree")
        assert (project / ".git").is_file()
    with pytest.raises(trust.FixtureTrustError):
        with trust.grok_fixture_trust(project, fixture.home):
            pytest.fail("An unmarked or enclosing repository was trusted")
    assert not fixture.store.exists()
    assert not fixture.lock.exists()


def test_lock_timeout_is_bounded_and_cannot_publish_a_grant(fixture, monkeypatch):
    fixture.store.write_bytes(b"# original\n")
    fixture.lock.write_bytes(b"")
    fixture.lock.chmod(0o600)
    ticks = iter(range(10))
    sleeps = []
    monkeypatch.setattr(
        trust, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=sleeps.append, time=time.time)
    )
    with fixture.lock.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(trust.FixtureTrustError, match="timed out"):
            with trust.grok_fixture_trust(fixture.project, fixture.home):
                pytest.fail("Held native lock allowed a trust grant")
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    assert sleeps and sum(sleeps) <= 0.1
    assert fixture.store.read_bytes() == b"# original\n"
    assert fixture.lock.exists()


def test_custom_grok_home_is_explicit_and_other_homes_remain_unchanged(fixture, tmp_path, monkeypatch):
    default_home = tmp_path / "unused-default-grok-home"
    default_home.mkdir()
    default_store = default_home / "trusted_folders.toml"
    default_store.write_bytes(b"# untouched alternate home\n")
    monkeypatch.setenv("GROK_HOME", str(fixture.home))
    with trust.grok_fixture_trust(fixture.project, Path(os.environ["GROK_HOME"])):
        assert str(fixture.project) in trust._document(fixture.store.read_bytes())["folders"]
        assert default_store.read_bytes() == b"# untouched alternate home\n"
    assert fixture.store.read_bytes() == b""
    assert default_store.read_bytes() == b"# untouched alternate home\n"
    assert {path.name for path in default_home.iterdir()} == {"trusted_folders.toml"}


def test_uncoordinated_update_before_publication_is_never_overwritten(fixture, monkeypatch):
    fixture.store.write_bytes(b"# initial store\n")
    concurrent = b"# unrelated concurrent decision\n" + folder_entry("/synthetic/concurrent-project", trusted=False)
    original_publish = trust._publish

    def race_before_publish(path, before, after):
        path.write_bytes(concurrent)
        original_publish(path, before, after)

    monkeypatch.setattr(trust, "_publish", race_before_publish)
    with pytest.raises(trust.FixtureTrustError, match="changed without"):
        with trust.grok_fixture_trust(fixture.project, fixture.home):
            pytest.fail("An uncoordinated native update was overwritten")
    assert fixture.store.read_bytes() == concurrent
    assert str(fixture.project) not in trust._document(concurrent)["folders"]
    assert_no_temporary_publications(fixture.home)


@pytest.mark.parametrize("invalid", ["symlink", "group_writable", "foreign_owner"])
def test_nonprivate_or_noncanonical_native_homes_are_refused(fixture, tmp_path, monkeypatch, invalid):
    home = fixture.home
    if invalid == "symlink":
        home = tmp_path / "home-symlink"
        home.symlink_to(fixture.home, target_is_directory=True)
    elif invalid == "group_writable":
        home.chmod(0o720)
    else:
        original_stat = Path.stat

        def foreign_stat(path, *args, **kwargs):
            observed = original_stat(path, *args, **kwargs)
            if path == home:
                fields = list(observed)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return observed

        monkeypatch.setattr(Path, "stat", foreign_stat)
    with pytest.raises(trust.FixtureTrustError):
        with trust.grok_fixture_trust(fixture.project, home):
            pytest.fail("An unsafe native configuration home was admitted")
    assert not fixture.store.exists()
    assert not fixture.lock.exists()
