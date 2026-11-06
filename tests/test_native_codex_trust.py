"""Exercise scoped Codex trust offline with synthetic configuration and RPC responses."""

import copy
import hashlib
import json
import os
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

from scripts import native_codex_trust as trust

EVENTS = ("session_start", "pre_tool_use", "post_tool_use", "subagent_start", "subagent_stop")
PRIVATE = "PRIVATE_SYNTHETIC_INHERITED_HOOK_COMMAND"
USER_FILE = "/synthetic/native-codex/config.toml"


def key_path(parts):
    return ".".join(parts[:-1]) + "." + json.dumps(parts[-1])


class FakeRpc:
    """Model only the documented configuration methods, with a new version on each read."""

    def __init__(self, binding):
        self.binding = binding
        self.user = {"unrelated": {"private": PRIVATE}}
        self.configured = {
            "model": "synthetic-model",
            "model_reasoning_effort": "high",
            "approval_policy": "on-request",
            "approvals_reviewer": "user",
            "sandbox_mode": "workspace-write",
        }
        self.calls = []
        self.reads = 0
        self.last_read_version = None
        self.profile = None
        self.extra_layers = []
        self.hooks_visible_before_project_trust = False
        self.inventory_change = lambda rows: rows
        self.after_write = lambda: None
        self.write_failure = None
        self.failure_after_write = None
        self.requirements = None
        self.paths = {key_path(path): path for path in self.expected_entries}

    @property
    def expected_entries(self):
        return {
            ("projects", str(self.binding.root)): {"trust_level": "trusted"},
            **{("hooks", "state", key): {"trusted_hash": value} for key, value in self.binding.hook_hashes.items()},
        }

    @property
    def writes(self):
        return [params for method, params in self.calls if method == "config/batchWrite"]

    def get(self, path):
        current = self.user
        for part in path:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def set(self, path, value):
        current = self.user
        for part in path[:-1]:
            current = current.setdefault(part, {})
        if value is None:
            del current[path[-1]]
        else:
            current[path[-1]] = copy.deepcopy(value)

    def call(self, method, params):
        assert method in {"config/read", "configRequirements/read", "hooks/list", "config/batchWrite"}
        self.calls.append((method, copy.deepcopy(params)))
        if method == "configRequirements/read":
            assert params is None
            return {"requirements": copy.deepcopy(self.requirements), "supportsIndependentSpeedModes": True}
        if method == "config/read":
            assert params["cwd"] == str(self.binding.root)
            assert params["includeLayers"] is True
            self.reads += 1
            self.last_read_version = "sha256:" + hashlib.sha256(str(self.reads).encode()).hexdigest()
            return {
                "config": copy.deepcopy({**self.configured, **self.user}),
                "layers": [
                    {
                        "name": {"type": "user", "file": USER_FILE, "profile": self.profile},
                        "version": self.last_read_version,
                        "config": copy.deepcopy(self.user),
                    },
                    *copy.deepcopy(self.extra_layers),
                ],
            }
        if method == "hooks/list":
            project = self.get(("projects", str(self.binding.root)))
            visible = self.hooks_visible_before_project_trust or project == {"trust_level": "trusted"}
            rows = [
                {
                    "key": key,
                    "currentHash": value,
                    "sourcePath": str(self.binding.root / ".codex/hooks.json"),
                    "enabled": True,
                    "trustStatus": "trusted" if self.get(("hooks", "state", key)) else "untrusted",
                    "handlerType": "command",
                    "command": PRIVATE,
                }
                for key, value in self.binding.hook_hashes.items()
            ]
            return {
                "data": [
                    {
                        "cwd": str(self.binding.root),
                        "hooks": self.inventory_change(rows) if visible else [],
                        "warnings": [],
                        "errors": [],
                    }
                ]
            }
        assert params["expectedVersion"] == self.last_read_version
        assert params["expectedVersion"] is not None
        assert params["reloadUserConfig"] is False
        assert set(params) == {"edits", "filePath", "expectedVersion", "reloadUserConfig"}
        assert params["filePath"] == USER_FILE
        self.last_read_version = None
        if self.write_failure is not None:
            raise self.write_failure
        for edit in params["edits"]:
            assert set(edit) == {"keyPath", "value", "mergeStrategy"}
            assert edit["mergeStrategy"] == "replace"
            path = self.paths[edit["keyPath"]]
            assert edit["value"] is None or edit["value"] == self.expected_entries[path]
            self.set(path, edit["value"])
        self.after_write()
        if self.failure_after_write is not None:
            raise self.failure_after_write
        return {"status": "ok", "version": "sha256:" + "a" * 64, "filePath": USER_FILE}


@pytest.fixture
def prepared(tmp_path):
    root = (tmp_path / "synthetic fixture").resolve()
    root.mkdir()
    (root / ".clink-native-verification-fixture").write_text("synthetic-native-adapter-fixture-v1\n")
    hashes = {
        f"{root}/.codex/hooks.json:{event}:0:0": "sha256:" + hashlib.sha256(event.encode()).hexdigest()
        for event in EVENTS
    }
    binding = trust.PreparedBinding(root=root, file_hashes={}, hook_hashes=hashes)
    rpc = FakeRpc(binding)
    return binding, rpc, trust.TrustScope(rpc, binding)


def assert_private_values_not_exported(scope, *summaries):
    encoded = json.dumps([scope.receipt, *summaries], sort_keys=True)
    assert PRIVATE not in encoded
    assert USER_FILE not in encoded


@pytest.mark.parametrize("selection", ["default", "none", "original"])
def test_registered_binding_default_preserves_original_dedicated_pins(monkeypatch, selection):
    validated = []
    monkeypatch.setattr(trust.PreparedBinding, "validate", lambda binding: validated.append(binding))
    if selection == "default":
        binding = trust.approved_binding()
    else:
        binding = trust.approved_binding(trust.FIXTURE if selection == "original" else None)
    assert validated == [binding]
    assert binding.root == trust.PROJECT / "test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture"
    assert binding.file_hashes == {
        ".codex/config.toml": "7417eaf3e862467bdf2a53699c14c5e5df1359af2892713c01ce510a54304de2",
        ".codex/hooks.json": "a3b468eb9f3d6105481fc76410ca344085ddf3e02b25d78ceac67d2eea278cac",
        "codex_hook.py": "943f26e6fd756b9984749c5fe4b53ecdcbc8ae56a060a8f4f24810200e0f3a42",
        ".codex/rules/native-fixture.rules": "5e14c4ce793a0e520faf405ee6119955c5e84e549671c0d7ef2c170404843c08",
        ".codex/agents/fixture-specialist.toml": "eeb6c8d906cb5759b03121ae29fd66a8db93c74364a6cd36518a5d972456fbfe",
        "codex_mcp_server.py": "2424778ea57b8a9f8c3ef42b417074801a0ba4ac499c04df01d6a93aeca3cdcd",
    }
    assert binding.aggregate_files == trust.PREPARED_FILES
    assert len(binding.aggregate_files) == 12
    assert binding.aggregate_sha256 == "c6d7fe36c5c704719d2833318ed37e8d4a2cfa2abaa4bba33e9a8d7bd4067972"
    expected_hooks = dict(
        zip(
            EVENTS,
            (
                "9482eed34b1b9a959850d28ca3a05e784ebba9c04f1a9fe26353ee44fb106078",
                "37d003d178723374fd0153f1eb417f430faf9ed26a4910a880484d0af8c8b827",
                "086d522ad2f2ee0a6fe0578146529008bf5440f8a211ae9a590257281dfcdf55",
                "10dcd57fad2efbd7a7878861b49b276b258b00f3f76bd54c4263729554cfb1b8",
                "ae582946138d8e3cb02829c07096c2f26f182bed3d616a395c7bd6bb89005177",
            ),
            strict=True,
        )
    )
    assert binding.hook_hashes == {
        f"{binding.root}/.codex/hooks.json:{event}:0:0": "sha256:" + value for event, value in expected_hooks.items()
    }


def test_registered_revised_binding_uses_all_twelve_new_pins_and_exact_hook_roots(monkeypatch):
    validated = []
    monkeypatch.setattr(trust.PreparedBinding, "validate", lambda binding: validated.append(binding))
    binding = trust.approved_binding(trust.REVISED_FIXTURE)
    assert validated == [binding]
    assert binding.root == trust.PROJECT / "test_output/native-adapters/native-codex-0.166.0-attempt4-prepare.fixture"
    assert binding.file_hashes == trust.REVISED_FILE_HASHES
    assert set(binding.file_hashes) == set(binding.aggregate_files) == set(trust.PREPARED_FILES)
    assert len(binding.file_hashes) == 12
    expected_aggregate = "6d5b6637b4bcf88de20375b2de2417625f0aedaf3e045b4f7cb34d888b220f36"
    assert binding.aggregate_sha256 == expected_aggregate
    assert hashlib.sha256(json.dumps(binding.file_hashes, sort_keys=True).encode()).hexdigest() == expected_aggregate
    expected_hooks = dict(
        zip(
            EVENTS,
            (
                "c4db559651e7f5160654f0539ba01245065dc1cb646f43f20b7403d82b1bc020",
                "5fd023035dc0be87d1c956192e51c6c211d969bc6fd4211c6a8cc8038eddc3dc",
                "c6dc7017f9b85cf66802028280844bceebc0a8fdb37af62b1c046c8625d10c48",
                "ef657018c507d0c8f243d9612e0c2a09be608bd0d643f7d273bf21145cc7f484",
                "cfb71cc0bff905295ece329a10d3c97192a4333f5b63d79342fc07e30375b40c",
            ),
            strict=True,
        )
    )
    assert binding.hook_hashes == {
        f"{binding.root}/.codex/hooks.json:{event}:0:0": "sha256:" + value for event, value in expected_hooks.items()
    }


@pytest.mark.parametrize("selection", ["marked_unknown", "alias", "registered_child"])
def test_unregistered_binding_roots_are_refused_before_validation_or_alias_resolution(tmp_path, monkeypatch, selection):
    unknown = tmp_path / "marked-unknown-fixture"
    unknown.mkdir()
    (unknown / ".clink-native-verification-fixture").write_text("synthetic-native-adapter-fixture-v1\n")
    alias = tmp_path / "registered-fixture-alias"
    alias.symlink_to(trust.REVISED_FIXTURE, target_is_directory=True)
    requested = {
        "marked_unknown": unknown,
        "alias": alias,
        "registered_child": trust.REVISED_FIXTURE / "nested-fixture",
    }[selection]
    validated = []
    monkeypatch.setattr(trust.PreparedBinding, "validate", lambda binding: validated.append(binding))

    def forbidden_resolution(*args, **kwargs):
        pytest.fail("An unregistered root must be rejected without filesystem resolution")

    with monkeypatch.context() as guard:
        guard.setattr(Path, "resolve", forbidden_resolution)
        with pytest.raises(trust.TrustError, match="fixture_root_not_registered") as caught:
            trust.approved_binding(requested)
    assert str(caught.value) == "fixture_root_not_registered"
    assert validated == []


def test_project_first_then_exact_inventory_hashes_before_five_hook_records(prepared):
    binding, rpc, scope = prepared
    enrolled = scope.enroll()
    assert len(rpc.writes) == 2
    project_path = ("projects", str(binding.root))
    assert rpc.writes[0]["edits"] == [
        {"keyPath": key_path(project_path), "value": {"trust_level": "trusted"}, "mergeStrategy": "replace"}
    ]
    assert len(rpc.writes[1]["edits"]) == 5
    assert {edit["keyPath"] for edit in rpc.writes[1]["edits"]} == {
        key_path(("hooks", "state", key)) for key in binding.hook_hashes
    }
    first_write = next(i for i, (method, _) in enumerate(rpc.calls) if method == "config/batchWrite")
    second_write = max(i for i, (method, _) in enumerate(rpc.calls) if method == "config/batchWrite")
    assert any(method == "hooks/list" for method, _ in rpc.calls[first_write + 1 : second_write])
    assert scope.owned == rpc.expected_entries
    assert all(rpc.get(path) == value for path, value in rpc.expected_entries.items())
    assert_private_values_not_exported(scope, enrolled)

    cleaned = scope.cleanup()
    assert all(rpc.get(path) is None for path in rpc.expected_entries)
    assert rpc.user["unrelated"] == {"private": PRIVATE}
    assert len(rpc.writes[-1]["edits"]) == 6
    assert all(edit["value"] is None for edit in rpc.writes[-1]["edits"])
    assert {edit["keyPath"] for edit in rpc.writes[-1]["edits"]} == set(rpc.paths)
    assert_private_values_not_exported(scope, cleaned)


@pytest.mark.parametrize("serialization", ["omitted", "null", "empty_table"])
def test_absent_effective_roots_enroll_and_cleanup_without_masking_written_records(prepared, serialization):
    binding, rpc, scope = prepared
    original = rpc.call
    observed = []

    def source_shaped_read(method, params):
        result = original(method, params)
        if method == "config/read":
            for name in ("projects", "hooks"):
                if name not in rpc.user and serialization != "omitted":
                    result["config"][name] = None if serialization == "null" else {}
            observed.append(copy.deepcopy(result["config"]))
        return result

    rpc.call = source_shaped_read
    enrolled = scope.enroll()
    assert enrolled["enrolled_record_count"] == 6
    assert enrolled["native_fixture_hooks_trusted"] is True
    assert all(rpc.get(path) == value for path, value in rpc.expected_entries.items())
    assert any(
        isinstance(snapshot.get("projects"), dict)
        and snapshot["projects"].get(str(binding.root)) == {"trust_level": "trusted"}
        for snapshot in observed
    )
    assert any(
        isinstance(snapshot.get("hooks"), dict)
        and all(
            snapshot["hooks"].get("state", {}).get(key) == {"trusted_hash": value}
            for key, value in binding.hook_hashes.items()
        )
        for snapshot in observed
    )
    if serialization == "null":
        assert observed[0]["projects"] is None
        assert observed[0]["hooks"] is None
    cleaned = scope.cleanup()
    assert cleaned["cleanup_complete"] is True
    assert cleaned["cleanup_removed_count"] == 6
    assert all(rpc.get(path) is None for path in rpc.expected_entries)
    assert rpc.user["unrelated"] == {"private": PRIVATE}
    assert_private_values_not_exported(scope, enrolled, cleaned)


@pytest.mark.parametrize("root_name", ["projects", "hooks"])
@pytest.mark.parametrize("value", ["PRIVATE_SYNTHETIC_INVALID_ROOT", [], 1])
def test_effective_nonnull_scalar_or_list_roots_are_refused_before_any_write(prepared, root_name, value):
    _, rpc, scope = prepared
    rpc.configured[root_name] = value
    with pytest.raises(trust.TrustError, match="trust_parent_not_table"):
        scope.enroll()
    assert rpc.writes == []
    assert scope.owned == {}


@pytest.mark.parametrize("value", [None, "PRIVATE_SYNTHETIC_INVALID_STATE", [], 1])
def test_effective_nested_hook_state_is_never_treated_as_nullable_root(prepared, value):
    _, rpc, scope = prepared
    rpc.configured["hooks"] = {"state": value}
    with pytest.raises(trust.TrustError, match="trust_parent_not_table"):
        scope.enroll()
    assert rpc.writes == []
    assert scope.owned == {}


@pytest.mark.parametrize("location", ["raw_user", "lower_layer"])
@pytest.mark.parametrize("root_name", ["projects", "hooks"])
@pytest.mark.parametrize("value", [None, "PRIVATE_SYNTHETIC_INVALID_RAW_PARENT"])
def test_raw_user_and_layer_parents_remain_strict_even_when_effective_api_roots_are_nullable(
    prepared, location, root_name, value
):
    _, rpc, scope = prepared
    if location == "raw_user":
        rpc.user[root_name] = value
    else:
        rpc.extra_layers = [
            {
                "name": {"type": "system", "file": "/synthetic/system.toml"},
                "version": "sha256:" + "d" * 64,
                "config": {root_name: value},
            }
        ]
    with pytest.raises(trust.TrustError, match="trust_parent_not_table"):
        scope.enroll()
    assert rpc.writes == []
    assert scope.owned == {}


@pytest.mark.parametrize("entry", ["project", "hook"])
@pytest.mark.parametrize("value", [None, {}])
def test_effective_preexisting_whole_entry_is_refused_even_when_null_or_empty(prepared, entry, value):
    binding, rpc, scope = prepared
    if entry == "project":
        rpc.configured["projects"] = {str(binding.root): value}
    else:
        rpc.configured["hooks"] = {"state": {next(iter(binding.hook_hashes)): value}}
    with pytest.raises(trust.TrustError, match="trust_entry_preexisting"):
        scope.enroll()
    assert rpc.writes == []
    assert scope.owned == {}


@pytest.mark.parametrize("entry", ["project", "hook"])
def test_raw_preexisting_null_whole_entry_is_refused_without_a_write(prepared, entry):
    binding, rpc, scope = prepared
    if entry == "project":
        rpc.user["projects"] = {str(binding.root): None}
    else:
        rpc.user["hooks"] = {"state": {next(iter(binding.hook_hashes)): None}}
    with pytest.raises(trust.TrustError, match="trust_entry_preexisting"):
        scope.enroll()
    assert rpc.writes == []
    assert scope.owned == {}


@pytest.mark.parametrize("entry", ["project", "hook"])
@pytest.mark.parametrize("value", [{}, {"trust_level": "untrusted"}, {"trusted_hash": "sha256:" + "b" * 64}])
def test_any_preexisting_whole_entry_is_refused_without_writes(prepared, entry, value):
    binding, rpc, scope = prepared
    path = (
        ("projects", str(binding.root)) if entry == "project" else ("hooks", "state", next(iter(binding.hook_hashes)))
    )
    rpc.set(path, value)
    before = copy.deepcopy(rpc.user)
    with pytest.raises(trust.TrustError):
        scope.enroll()
    assert rpc.user == before
    assert rpc.writes == []
    assert scope.owned == {}
    assert_private_values_not_exported(scope)


def test_preexisting_matching_entries_are_not_claimed(prepared):
    _, rpc, scope = prepared
    for path, value in rpc.expected_entries.items():
        rpc.set(path, value)
    before = copy.deepcopy(rpc.user)
    with pytest.raises(trust.TrustError):
        scope.enroll()
    assert rpc.user == before
    assert rpc.writes == []
    assert scope.owned == {}


@pytest.mark.parametrize("mismatch", ["hash", "source", "disabled", "missing", "duplicate"])
def test_inventory_disagreement_stops_before_hook_grant_and_owned_project_can_be_cleaned(prepared, mismatch):
    binding, rpc, scope = prepared

    def change(rows):
        if mismatch == "hash":
            rows[0]["currentHash"] = "sha256:" + "f" * 64
        elif mismatch == "source":
            rows[0]["sourcePath"] = "/synthetic/unrelated/hooks.json"
        elif mismatch == "disabled":
            rows[0]["enabled"] = False
        elif mismatch == "missing":
            rows.pop()
        else:
            rows.append(copy.deepcopy(rows[0]))
        return rows

    rpc.inventory_change = change
    with pytest.raises(trust.TrustError):
        scope.enroll()
    assert len(rpc.writes) == 2
    assert all(rpc.get(("hooks", "state", key)) is None for key in binding.hook_hashes)
    assert rpc.get(("projects", str(binding.root))) is None
    assert rpc.writes[-1]["edits"] == [
        {"keyPath": key_path(("projects", str(binding.root))), "value": None, "mergeStrategy": "replace"}
    ]


def test_hooks_disabled_after_enrollment_cannot_establish_the_capability_gate(prepared):
    binding, rpc, scope = prepared

    def disable_after_grant(rows):
        if rpc.get(("hooks", "state", next(iter(binding.hook_hashes)))):
            rows[0]["enabled"] = False
        return rows

    rpc.inventory_change = disable_after_grant
    with pytest.raises(trust.TrustError):
        scope.enroll()
    assert all(rpc.get(path) is None for path in rpc.expected_entries)
    assert scope.receipt["native_fixture_hooks_enabled"] is False
    assert scope.receipt["cleanup_complete"] is True


@pytest.mark.parametrize("entry", ["project", "hook"])
def test_cleanup_preserves_changed_whole_owned_entries_and_removes_only_unchanged_entries(prepared, entry):
    binding, rpc, scope = prepared
    scope.enroll()
    path = (
        ("projects", str(binding.root)) if entry == "project" else ("hooks", "state", next(iter(binding.hook_hashes)))
    )
    changed = {**rpc.get(path), "concurrent_note": "preserve synthetic owner update"}
    rpc.set(path, changed)
    rpc.user["unrelated"]["concurrent"] = True
    cleaned = scope.cleanup()
    assert cleaned["cleanup_conflict_count"] == 1
    assert cleaned["cleanup_remaining_count"] == 1
    assert cleaned["cleanup_complete"] is False
    assert rpc.get(path) == changed
    assert all(rpc.get(other) is None for other in rpc.expected_entries if other != path)
    assert rpc.user["unrelated"] == {"private": PRIVATE, "concurrent": True}
    assert key_path(path) not in {edit["keyPath"] for edit in rpc.writes[-1]["edits"]}
    assert_private_values_not_exported(scope, cleaned)


def test_cleanup_is_idempotent_and_does_not_restore_configuration_snapshots(prepared):
    _, rpc, scope = prepared
    scope.enroll()
    rpc.user["native_normalized"] = {"trusted_content_hash": "sha256:" + "c" * 64}
    scope.cleanup()
    count = len(rpc.writes)
    scope.cleanup()
    assert len(rpc.writes) == count
    assert rpc.user["native_normalized"] == {"trusted_content_hash": "sha256:" + "c" * 64}
    assert rpc.user["unrelated"] == {"private": PRIVATE}


def test_active_user_profile_is_refused_without_a_write(prepared):
    _, rpc, scope = prepared
    rpc.profile = "synthetic-active-profile"
    with pytest.raises(trust.TrustError):
        scope.enroll()
    assert rpc.writes == []


def test_preexisting_entry_in_another_layer_is_refused_without_a_write(prepared):
    binding, rpc, scope = prepared
    rpc.extra_layers = [
        {
            "name": {"type": "system", "file": "/synthetic/system.toml"},
            "version": "sha256:" + "d" * 64,
            "config": {"projects": {str(binding.root): {}}},
        }
    ]
    with pytest.raises(trust.TrustError):
        scope.enroll()
    assert rpc.writes == []


def test_inventory_distinguishes_configured_fields_from_unresolved_runtime_permissions(prepared):
    _, rpc, scope = prepared
    inventory = scope.inventory()
    assert inventory["runtime_permissions_resolved"] is False
    assert inventory["configured_before"] == rpc.configured
    assert rpc.writes == []
    assert_private_values_not_exported(scope, inventory)


def test_version_conflict_is_not_retried_or_claimed_as_owned(prepared):
    _, rpc, scope = prepared
    rpc.write_failure = trust.TrustError("synthetic_version_conflict")
    with pytest.raises(trust.TrustError, match="synthetic_version_conflict"):
        scope.enroll()
    assert len(rpc.writes) == 1
    assert scope.owned == {}
    assert rpc.user == {"unrelated": {"private": PRIVATE}}


def test_lost_write_response_preserves_unconfirmed_record_without_claiming_ownership(prepared):
    binding, rpc, scope = prepared
    rpc.failure_after_write = trust.TrustError("rpc_process_ended")
    with pytest.raises(trust.TrustError, match="rpc_process_ended"):
        scope.enroll()
    assert len(rpc.writes) == 1
    assert scope.owned == {}
    assert scope.receipt["unconfirmed_write_record_count"] == 1
    assert rpc.get(("projects", str(binding.root))) == {"trust_level": "trusted"}
    cleaned = scope.cleanup()
    assert cleaned["cleanup_complete"] is False
    assert len(rpc.writes) == 1
    assert rpc.get(("projects", str(binding.root))) == {"trust_level": "trusted"}


def test_lost_second_write_response_preserves_five_unconfirmed_hooks_and_cleanup_evidence(prepared):
    binding, rpc, scope = prepared
    original = rpc.call

    def lose_second_acknowledgement(method, params):
        result = original(method, params)
        if method == "config/batchWrite" and len(rpc.writes) == 2:
            raise trust.TrustError("rpc_timeout")
        return result

    rpc.call = lose_second_acknowledgement
    with pytest.raises(trust.TrustError, match="rpc_timeout"):
        scope.enroll()
    assert len(rpc.writes) == 3
    assert rpc.get(("projects", str(binding.root))) is None
    assert all(
        rpc.get(("hooks", "state", key)) == {"trusted_hash": value} for key, value in binding.hook_hashes.items()
    )
    assert scope.owned == {}
    assert scope.receipt["unconfirmed_write_record_count"] == 5
    assert scope.receipt["cleanup_unconfirmed_write_record_count"] == 5
    assert scope.receipt["cleanup_removed_count"] == 1
    assert scope.receipt["cleanup_complete"] is False
    cleaned = scope.cleanup()
    assert len(rpc.writes) == 3
    assert cleaned["cleanup_unconfirmed_write_record_count"] == 5
    assert cleaned["cleanup_removed_count"] == 1
    assert cleaned["cleanup_complete"] is False
    assert all(
        rpc.get(("hooks", "state", key)) == {"trusted_hash": value} for key, value in binding.hook_hashes.items()
    )
    assert_private_values_not_exported(scope, cleaned)


def test_lost_cleanup_acknowledgement_retains_union_of_all_six_unconfirmed_paths(prepared):
    binding, rpc, scope = prepared
    original = rpc.call

    def lose_creation_and_cleanup_acknowledgements(method, params):
        result = original(method, params)
        if method == "config/batchWrite" and len(rpc.writes) in {2, 3}:
            raise trust.TrustError("rpc_timeout")
        return result

    rpc.call = lose_creation_and_cleanup_acknowledgements
    with pytest.raises(trust.TrustError, match="enrollment_and_cleanup_failed"):
        scope.enroll()
    assert len(rpc.writes) == 3
    assert rpc.get(("projects", str(binding.root))) is None
    assert all(
        rpc.get(("hooks", "state", key)) == {"trusted_hash": value} for key, value in binding.hook_hashes.items()
    )
    assert scope.receipt["unconfirmed_write_record_count"] == 6
    assert scope.receipt["cleanup_complete"] is False
    cleaned = scope.cleanup()
    assert len(rpc.writes) == 3
    assert cleaned["unconfirmed_write_record_count"] == 6
    assert cleaned["cleanup_unconfirmed_write_record_count"] == 6
    assert cleaned["cleanup_complete"] is False
    assert scope.owned == {}
    assert_private_values_not_exported(scope, cleaned)


@pytest.mark.parametrize("operation", ["inventory", "enroll"])
def test_managed_requirements_are_read_with_null_params_and_absence_is_explicit(prepared, operation):
    _, rpc, scope = prepared
    summary = {"inventory": scope.inventory, "enroll": scope.enroll}[operation]()
    assert ("configRequirements/read", None) in rpc.calls
    assert summary["managed_requirements_observed"] is True
    assert summary["managed_requirements_present"] is False
    assert summary["runtime_permissions_resolved"] is False
    assert_private_values_not_exported(scope, summary)
    scope.cleanup()


def test_managed_requirements_export_only_safe_constraint_metadata_and_fingerprint(prepared):
    _, rpc, scope = prepared
    private_profile = "/synthetic/private/permission-profile"
    rpc.requirements = {
        "allowedApprovalPolicies": ["on-request", {"reject": {"private_instruction": PRIVATE}}],
        "allowedApprovalsReviewers": ["user"],
        "allowedSandboxModes": ["read-only", "workspace-write"],
        "allowedPermissionProfiles": {private_profile: {"instructions": PRIVATE}},
        "defaultPermissions": private_profile,
        "allowManagedHooksOnly": True,
        "providers": {"private-provider": {"instructions": PRIVATE, "path": USER_FILE}},
        "instructions": PRIVATE,
        "network": {"allowedDomains": ["private.synthetic.invalid"]},
        "loginMethods": {"private-method": PRIVATE},
    }
    inventory = scope.inventory()
    assert inventory["managed_requirements_present"] is True
    assert inventory["managed_constraints"] == {
        "allowedApprovalPolicies": ["on-request", "structured_native_policy"],
        "allowedApprovalsReviewers": ["user"],
        "allowedSandboxModes": ["read-only", "workspace-write"],
        "allowedPermissionProfileCount": 1,
        "defaultPermissionsConfigured": True,
        "allowManagedHooksOnly": True,
    }
    raw_constraints = {
        key: rpc.requirements[key]
        for key in (
            "allowedApprovalPolicies",
            "allowedApprovalsReviewers",
            "allowedSandboxModes",
            "allowedPermissionProfiles",
            "defaultPermissions",
            "allowManagedHooksOnly",
        )
    }
    expected = hashlib.sha256(json.dumps(raw_constraints, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert inventory["managed_constraints_sha256"] == expected
    encoded = json.dumps(inventory, sort_keys=True)
    for private_value in (PRIVATE, USER_FILE, private_profile, "private-provider", "private.synthetic.invalid"):
        assert private_value not in encoded
    rpc.requirements["providers"] = {"different-private-provider": {"instructions": "changed private instructions"}}
    rpc.requirements["instructions"] = "changed private instructions"
    assert scope.inventory()["managed_constraints_sha256"] == expected
    assert rpc.writes == []


@pytest.mark.parametrize("result", [{}, {"requirements": []}, {"requirements": "PRIVATE_SYNTHETIC_INVALID"}])
def test_invalid_or_missing_managed_requirements_cannot_authorize_a_write(prepared, result):
    _, rpc, scope = prepared
    original = rpc.call

    def invalid_requirements(method, params):
        response = original(method, params)
        return result if method == "configRequirements/read" else response

    rpc.call = invalid_requirements
    with pytest.raises(trust.TrustError, match="managed_requirements_invalid"):
        scope.enroll()
    assert rpc.writes == []
    assert scope.owned == {}
    assert_private_values_not_exported(scope)


def test_changed_managed_constraints_stop_enrollment_and_clean_up_owned_records(prepared):
    _, rpc, scope = prepared
    rpc.requirements = {"allowedSandboxModes": ["workspace-write"]}

    def change_constraints_after_second_write():
        if len(rpc.writes) == 2:
            rpc.requirements["allowedSandboxModes"] = ["read-only"]

    rpc.after_write = change_constraints_after_second_write
    with pytest.raises(trust.TrustError, match="managed_requirements_changed"):
        scope.enroll()
    assert len(rpc.writes) == 3
    assert all(rpc.get(path) is None for path in rpc.expected_entries)
    assert scope.receipt["cleanup_complete"] is True
    assert scope.receipt["runtime_permissions_resolved"] is False
    assert_private_values_not_exported(scope)


@pytest.mark.parametrize("version", [None, "", "private malformed native version"])
def test_missing_or_malformed_native_version_cannot_authorize_any_write(prepared, version):
    _, rpc, scope = prepared
    original = rpc.call

    def invalid_version(method, params):
        result = original(method, params)
        if method == "config/read":
            result["layers"][0]["version"] = version
        return result

    rpc.call = invalid_version
    with pytest.raises(trust.TrustError, match="native_user_layer_invalid"):
        scope.enroll()
    assert rpc.writes == []
    assert scope.owned == {}


def test_binding_revalidation_stops_changed_fixture_before_a_write(prepared):
    binding, rpc, _ = prepared
    canary = binding.root / "synthetic-canary.txt"
    canary.write_bytes(b"original synthetic bytes\n")
    binding = trust.PreparedBinding(
        root=binding.root,
        file_hashes={"synthetic-canary.txt": hashlib.sha256(canary.read_bytes()).hexdigest()},
        hook_hashes=binding.hook_hashes,
    )
    scope = trust.TrustScope(rpc, binding)
    canary.write_bytes(b"changed synthetic bytes\n")
    with pytest.raises(trust.TrustError):
        scope.enroll()
    assert rpc.writes == []


def test_aggregate_binding_covers_files_outside_the_individual_hash_subset(prepared):
    binding, rpc, _ = prepared
    names = tuple(f"synthetic-file-{index:02}.txt" for index in range(12))
    hashes = {}
    for index, name in enumerate(names):
        content = f"synthetic prepared file {index}\n".encode()
        (binding.root / name).write_bytes(content)
        hashes[name] = hashlib.sha256(content).hexdigest()
    aggregate = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    complete = trust.PreparedBinding(
        root=binding.root,
        file_hashes={name: hashes[name] for name in names[:6]},
        hook_hashes=binding.hook_hashes,
        aggregate_files=names,
        aggregate_sha256=aggregate,
    )
    complete.validate()
    (binding.root / names[-1]).write_bytes(b"changed file outside individual hash subset\n")
    with pytest.raises(trust.TrustError, match="fixture_aggregate_changed"):
        trust.TrustScope(rpc, complete).enroll()
    assert rpc.writes == []


@pytest.mark.parametrize("target_name", ["marker", "bound_file"])
@pytest.mark.parametrize("unsafe", ["hardlink", "symlink"])
def test_binding_refuses_linked_marker_and_bound_files_before_rpc(prepared, tmp_path, target_name, unsafe):
    binding, rpc, _ = prepared
    bound = binding.root / "synthetic-bound.txt"
    bound.write_bytes(b"synthetic bound file\n")
    binding = trust.PreparedBinding(
        root=binding.root,
        file_hashes={bound.name: hashlib.sha256(bound.read_bytes()).hexdigest()},
        hook_hashes=binding.hook_hashes,
    )
    target = binding.root / ".clink-native-verification-fixture" if target_name == "marker" else bound
    alternate = tmp_path / "synthetic-linked-copy"
    if unsafe == "hardlink":
        os.link(target, alternate)
    else:
        target.rename(alternate)
        target.symlink_to(alternate)
    with pytest.raises(trust.TrustError):
        trust.TrustScope(rpc, binding).enroll()
    assert rpc.calls == []


def test_native_legacy_normalization_is_counted_and_preserved_during_cleanup(prepared):
    _, rpc, scope = prepared
    declaration = "sha256:" + "e" * 64
    content = "sha256:" + "f" * 64
    rpc.user["hooks"] = {"state": {"synthetic-plugin-hook": {"trusted_hash": declaration + "/" + content}}}

    def normalize():
        state = rpc.user["hooks"]["state"]["synthetic-plugin-hook"]
        if "/" in state["trusted_hash"]:
            state["trusted_hash"], state["trusted_content_hash"] = state["trusted_hash"].split("/", 1)

    rpc.after_write = normalize
    enrolled = scope.enroll()
    assert enrolled["incidental_normalization_entries"] == 1
    assert enrolled["unexpected_unrelated_change"] is False
    cleaned = scope.cleanup()
    assert cleaned["cleanup_complete"] is True
    assert rpc.user["hooks"]["state"] == {
        "synthetic-plugin-hook": {"trusted_hash": declaration, "trusted_content_hash": content}
    }
    assert_private_values_not_exported(scope, enrolled, cleaned)


def test_unexpected_unrelated_change_stops_enrollment_and_preserves_current_native_state(prepared):
    binding, rpc, scope = prepared
    rpc.after_write = lambda: rpc.user["unrelated"].update({"concurrent": True})
    with pytest.raises(trust.TrustError, match="unexpected_unrelated_change"):
        scope.enroll()
    assert scope.receipt["unexpected_unrelated_change"] is True
    assert rpc.get(("projects", str(binding.root))) is None
    assert all(rpc.get(("hooks", "state", key)) is None for key in binding.hook_hashes)
    assert rpc.user["unrelated"] == {"private": PRIVATE, "concurrent": True}
    assert all(len(write["edits"]) == 1 for write in rpc.writes)


def synthetic_executable(tmp_path, mode="valid"):
    """Create a bounded local Python peer; never start an installed native CLI."""
    script = tmp_path / "synthetic-control-peer"
    script.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f"""\
            import json
            import os
            import pathlib
            import sys
            import time

            mode = {mode!r}
            for line in sys.stdin:
                request = json.loads(line)
                if request['method'] == 'initialized':
                    if mode == 'closed_stdin':
                        os.close(0)
                        pathlib.Path('synthetic-stdin-closed.ready').write_text('ready')
                        time.sleep(30)
                    continue
                identity = request['id']
                if request['method'] == 'initialize':
                    print(json.dumps({{'id': identity, 'result': {{}}}}), flush=True)
                    continue
                if request['method'] == 'configRequirements/read':
                    assert request['params'] is None
                    print(json.dumps({{'id': identity, 'result': {{'requirements': None, 'supportsIndependentSpeedModes': True}}}}), flush=True)
                    continue
                if mode == 'timeout':
                    time.sleep(10)
                elif mode == 'ended':
                    break
                elif mode == 'stdout_bound':
                    print('x' * 8192, flush=True)
                elif mode == 'stderr_bound':
                    print('x' * 8192, file=sys.stderr, flush=True)
                    time.sleep(10)
                elif mode == 'invalid_json':
                    print('{{private malformed response', flush=True)
                elif mode == 'unsolicited_request':
                    print(json.dumps({{'id': 99, 'method': 'synthetic/request'}}), flush=True)
                elif mode == 'mismatched_id':
                    print(json.dumps({{'id': identity + 1, 'result': {{}}}}), flush=True)
                elif mode == 'native_error':
                    print(json.dumps({{'id': identity, 'error': {{'message': {PRIVATE!r}}}}}), flush=True)
                elif mode == 'invalid_result':
                    print(json.dumps({{'id': identity, 'result': []}}), flush=True)
                else:
                    print(json.dumps({{'method': 'synthetic/private', 'params': {{'command': {PRIVATE!r}}}}}), flush=True)
                    print(json.dumps({{'id': identity, 'result': {{'synthetic': True}}}}), flush=True)
            """))
    script.chmod(0o700)
    return script, hashlib.sha256(script.read_bytes()).hexdigest()


def test_synthetic_stdio_transport_uses_only_initialization_and_allowed_inventory(tmp_path):
    executable, digest = synthetic_executable(tmp_path)
    rpc = trust.NativeRpc(executable, tmp_path, expected_sha256=digest, timeout=3, output_limit=4096)
    with rpc:
        assert rpc.call("config/read", {}) == {"synthetic": True}
        assert rpc.call("configRequirements/read", None) == {
            "requirements": None,
            "supportsIndependentSpeedModes": True,
        }
        assert rpc.call("hooks/list", {}) == {"synthetic": True}
        for method in ("thread/start", "turn/start", "account/login/start", "config/batchWrite"):
            with pytest.raises(trust.TrustError, match="rpc_method_not_allowed"):
                rpc.call(method, {})
        with pytest.raises(trust.TrustError, match="rpc_initialize_repeated"):
            rpc.call("initialize", {})
    assert rpc.cleanup_complete is True
    assert rpc.process.poll() is not None
    assert rpc.buffer == bytearray()


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("timeout", "rpc_timeout"),
        ("ended", "rpc_process_ended"),
        ("stdout_bound", "rpc_output_bound"),
        ("stderr_bound", "rpc_output_bound"),
        ("invalid_json", "rpc_invalid_json"),
        ("unsolicited_request", "rpc_unsolicited_request"),
        ("mismatched_id", "rpc_response_mismatch"),
        ("native_error", "rpc_native_error"),
        ("invalid_result", "rpc_invalid_result"),
    ],
)
def test_synthetic_stdio_failures_are_bounded_redacted_and_cleaned(tmp_path, mode, code):
    executable, digest = synthetic_executable(tmp_path, mode)
    rpc = trust.NativeRpc(
        executable, tmp_path, expected_sha256=digest, timeout=0.5 if mode == "timeout" else 3, output_limit=4096
    )
    with pytest.raises(trust.TrustError, match=code) as caught:
        with rpc:
            rpc.call("config/read", {})
    assert str(caught.value) == code
    assert PRIVATE not in str(caught.value)
    if not rpc.cleanup_complete:
        assert rpc.cleanup_failure in {"rpc_group_signal_failed", "rpc_group_observation_failed"}
    assert rpc.process.poll() is not None
    assert rpc.buffer == bytearray()


def test_synthetic_stdio_rejects_wrong_executable_hash_before_launch(tmp_path):
    executable, _ = synthetic_executable(tmp_path)
    rpc = trust.NativeRpc(executable, tmp_path, expected_sha256="0" * 64)
    with pytest.raises(trust.TrustError, match="executable_identity_changed"):
        with rpc:
            pytest.fail("A changed synthetic executable was launched")
    assert rpc.process is None


def test_broken_pipe_during_request_does_not_mask_error_or_skip_process_group_cleanup(tmp_path):
    executable, digest = synthetic_executable(tmp_path, "closed_stdin")
    rpc = trust.NativeRpc(executable, tmp_path, expected_sha256=digest, timeout=3)
    ready = tmp_path / "synthetic-stdin-closed.ready"
    reaped_by_rpc = False
    try:
        with pytest.raises(trust.TrustError, match="rpc_input_failed") as caught:
            with rpc:
                deadline = time.monotonic() + 3
                while not ready.exists():
                    assert time.monotonic() < deadline, "Synthetic peer did not close stdin within its bound"
                    time.sleep(0.01)
                rpc.call("config/read", {})
    finally:
        reaped_by_rpc = rpc.process is not None and rpc.process.poll() is not None
        if rpc.process is not None and not reaped_by_rpc:
            os.killpg(rpc.process.pid, signal.SIGKILL)
            rpc.process.wait(timeout=3)
    assert str(caught.value) == "rpc_input_failed"
    assert reaped_by_rpc is True
    assert rpc.process.poll() is not None
    assert rpc.cleanup_complete is True
    assert rpc.buffer == bytearray()
    assert all(stream.closed for stream in (rpc.process.stdin, rpc.process.stdout, rpc.process.stderr))
    with pytest.raises(ProcessLookupError):
        os.killpg(rpc.process.pid, 0)


@pytest.mark.parametrize("stream_name", ["stdin", "stdout", "stderr"])
def test_one_stream_close_error_does_not_skip_other_closes_or_mask_original_error(tmp_path, stream_name):
    executable, digest = synthetic_executable(tmp_path)
    rpc = trust.NativeRpc(executable, tmp_path, expected_sha256=digest, timeout=3)

    class FailingClose:
        def __init__(self, stream):
            self.stream = stream

        def close(self):
            self.stream.close()
            raise OSError("PRIVATE_SYNTHETIC_STREAM_CLOSE_FAILURE")

    with pytest.raises(RuntimeError, match="synthetic original failure"):
        with rpc:
            streams = {name: getattr(rpc.process, name) for name in ("stdin", "stdout", "stderr")}
            setattr(rpc.process, stream_name, FailingClose(streams[stream_name]))
            raise RuntimeError("synthetic original failure")
    assert rpc.process.poll() is not None
    assert rpc.cleanup_complete is True
    assert rpc.cleanup_failure == "rpc_stream_close_failed"
    assert all(stream.closed for stream in streams.values())


def test_cleanup_observation_failure_is_reported_without_masking_original_exception(tmp_path, monkeypatch):
    executable, digest = synthetic_executable(tmp_path)
    rpc = trust.NativeRpc(executable, tmp_path, expected_sha256=digest, timeout=3)

    def denied_signal(pid, signal):
        assert pid == rpc.process.pid
        raise PermissionError("synthetic process-group permission failure")

    with pytest.raises(RuntimeError, match="synthetic original failure"):
        with rpc:
            monkeypatch.setattr(trust.os, "killpg", denied_signal)
            raise RuntimeError("synthetic original failure")
    assert rpc.cleanup_complete is False
    assert rpc.cleanup_failure == "rpc_group_observation_failed"
    assert rpc.process.poll() is not None
