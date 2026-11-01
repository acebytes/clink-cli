"""Scope native Codex trust to the immutable, explicitly approved test fixture.

This is verification tooling, never production launch behavior. Native app-server
startup retains its normal catalog/auth/cache/plugin activity; only our RPCs are
limited to initialization and configuration/hook inventory, with opt-in writes.
No thread, turn, model, tool, or authentication RPC is submitted. Raw native
configuration, diagnostics, commands, and session identifiers stay in memory.

Pinned contracts: openai/codex 6ff1e1ca3b30fc8b914720681fcf5fe62e95f856,
app-server/src/config_manager_service.rs, app-server-protocol/src/protocol/v2/
{config,plugin}.rs. expectedVersion is checked before native reread/write and is
NOT an interprocess lock or final CAS. Native legacy normalization is preserved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT / "test_output/native-adapters/native-codex-0.166.0-attempt1-prepare.fixture"
EXECUTABLE_SHA256 = "39acf979729dd290f9a24ee38b2914e036c91b65952872e28ae2a6b88a7db7c1"
SOURCE_COMMIT = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
FILE_HASHES = {
    ".codex/config.toml": "7417eaf3e862467bdf2a53699c14c5e5df1359af2892713c01ce510a54304de2",
    ".codex/hooks.json": "a3b468eb9f3d6105481fc76410ca344085ddf3e02b25d78ceac67d2eea278cac",
    "codex_hook.py": "943f26e6fd756b9984749c5fe4b53ecdcbc8ae56a060a8f4f24810200e0f3a42",
    ".codex/rules/native-fixture.rules": "5e14c4ce793a0e520faf405ee6119955c5e84e549671c0d7ef2c170404843c08",
    ".codex/agents/fixture-specialist.toml": "eeb6c8d906cb5759b03121ae29fd66a8db93c74364a6cd36518a5d972456fbfe",
    "codex_mcp_server.py": "2424778ea57b8a9f8c3ef42b417074801a0ba4ac499c04df01d6a93aeca3cdcd",
}
PREPARED_FILES = (
    ".clink-native-verification-fixture",
    "AGENTS.md",
    "fixture.py",
    "mcp_server.py",
    "native-role.txt",
    ".codex/agents/fixture-specialist.toml",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".codex/rules/native-fixture.rules",
    "codex-specialist.txt",
    "codex_hook.py",
    "codex_mcp_server.py",
)
PREPARED_AGGREGATE = "c6d7fe36c5c704719d2833318ed37e8d4a2cfa2abaa4bba33e9a8d7bd4067972"
HOOK_HASHES = dict(
    zip(
        ("session_start", "pre_tool_use", "post_tool_use", "subagent_start", "subagent_stop"),
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
_MISSING = object()
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
CLEANUP_ERROR_CODES = frozenset(
    {
        "rpc_group_signal_failed",
        "rpc_process_signal_failed",
        "rpc_process_wait_failed",
        "rpc_cleanup_incomplete",
        "rpc_group_observation_failed",
        "rpc_selector_close_failed",
        "rpc_stream_close_failed",
    }
)


ERROR_CODES = frozenset(
    (
        "active_profile_write_target_unverified",
        "bound_file_changed",
        "bound_file_unavailable",
        "bound_file_unsafe",
        "config_inventory_invalid",
        "enrollment_and_cleanup_failed",
        "executable_identity_changed",
        "executable_unavailable",
        "fixture_aggregate_changed",
        "fixture_binding_invalid",
        "fixture_file_changed",
        "fixture_marker_changed",
        "fixture_path_changed",
        "fixture_unavailable",
        "hook_binding_invalid",
        "hook_inventory_binding_mismatch",
        "hook_inventory_invalid",
        "managed_requirements_changed",
        "managed_requirements_invalid",
        "native_hook_hash_gate_failed",
        "native_hook_trust_not_observed",
        "native_user_layer_invalid",
        "native_user_layer_unavailable",
        "native_write_not_confirmed",
        "native_write_overridden",
        "native_write_target_changed",
        "project_trust_not_observed",
        "rpc_initialize_repeated",
        "rpc_input_failed",
        "rpc_invalid_json",
        "rpc_invalid_message",
        "rpc_invalid_result",
        "rpc_method_not_allowed",
        "rpc_native_error",
        "rpc_output_bound",
        "rpc_process_ended",
        "rpc_process_unavailable",
        "rpc_request_bound",
        "rpc_response_mismatch",
        "rpc_timeout",
        "rpc_unsolicited_request",
        "scope_already_enrolled",
        "trust_entry_preexisting",
        "trust_parent_not_table",
        "trust_records_not_observed",
        "unexpected_unrelated_change",
    )
)


class TrustError(RuntimeError):
    """Expose only a fixed technical code, never a native response or diagnostic."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Rpc(Protocol):
    def call(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]: ...


def _read_regular(path: Path, *, limit: int, executable: bool = False) -> bytes:
    """Read a bounded, owned, single-link regular file without following its final link."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        owners = {os.getuid(), 0} if executable else {os.getuid()}
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in owners
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            raise TrustError("bound_file_unsafe")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise TrustError("bound_file_changed")
        return bytes(data)
    except OSError:
        raise TrustError("bound_file_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@dataclass(frozen=True)
class PreparedBinding:
    root: Path
    file_hashes: dict[str, str]
    hook_hashes: dict[str, str]
    aggregate_files: tuple[str, ...] = ()
    aggregate_sha256: str | None = None

    def validate(self) -> None:
        try:
            if not self.root.is_absolute() or self.root.resolve(strict=True) != self.root:
                raise TrustError("fixture_path_changed")
            marker = self.root / ".clink-native-verification-fixture"
            if _read_regular(marker, limit=128) != b"synthetic-native-adapter-fixture-v1\n":
                raise TrustError("fixture_marker_changed")
            hashes = {}
            for name in set(self.file_hashes) | set(self.aggregate_files):
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise TrustError("fixture_binding_invalid")
                target = self.root / relative
                if target.resolve(strict=True) != target or not stat.S_ISREG(target.stat().st_mode):
                    raise TrustError("fixture_file_changed")
                hashes[name] = hashlib.sha256(_read_regular(target, limit=1024 * 1024)).hexdigest()
                if name in self.file_hashes and hashes[name] != self.file_hashes[name]:
                    raise TrustError("fixture_file_changed")
            if self.aggregate_sha256 is not None:
                actual = hashlib.sha256(
                    json.dumps({name: hashes[name] for name in sorted(self.aggregate_files)}, sort_keys=True).encode()
                ).hexdigest()
                if actual != self.aggregate_sha256:
                    raise TrustError("fixture_aggregate_changed")
        except OSError:
            raise TrustError("fixture_unavailable") from None
        if any(not _HASH.fullmatch(value) for value in self.hook_hashes.values()):
            raise TrustError("hook_binding_invalid")


def approved_binding() -> PreparedBinding:
    """Bind only the exact existing packet; never regenerate its files."""
    binding = PreparedBinding(
        FIXTURE,
        FILE_HASHES,
        {f"{FIXTURE}/.codex/hooks.json:{event}:0:0": "sha256:" + value for event, value in HOOK_HASHES.items()},
        PREPARED_FILES,
        PREPARED_AGGREGATE,
    )
    binding.validate()
    return binding


class NativeRpc:
    """Bound one native stdio control process and discard all raw output on close."""

    def __init__(
        self,
        executable: Path,
        root: Path,
        *,
        expected_sha256: str = EXECUTABLE_SHA256,
        allow_writes: bool = False,
        timeout: float = 60,
        output_limit: int = 8 * 1024 * 1024,
        max_lifetime: float = 1200,
    ):
        self.executable, self.root = executable, root
        self.expected_sha256, self.allow_writes = expected_sha256, allow_writes
        self.timeout, self.output_limit = timeout, output_limit
        self.max_lifetime = max_lifetime
        self.process: subprocess.Popen[bytes] | None = None
        self.selector = selectors.DefaultSelector()
        self.buffer = bytearray()
        self.total_bytes = 0
        self.sequence = 0
        self.deadline = 0.0
        self.closed = False
        self.cleanup_complete = False
        self.cleanup_failure: str | None = None

    def __enter__(self) -> NativeRpc:
        try:
            actual = hashlib.sha256(
                _read_regular(self.executable, limit=512 * 1024 * 1024, executable=True)
            ).hexdigest()
        except OSError:
            raise TrustError("executable_unavailable") from None
        if actual != self.expected_sha256:
            raise TrustError("executable_identity_changed")
        self.deadline = time.monotonic() + self.max_lifetime
        try:
            self.process = subprocess.Popen(
                [str(self.executable), "app-server", "--listen", "stdio://"],
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            for stream in (self.process.stdout, self.process.stderr):
                assert stream is not None
                os.set_blocking(stream.fileno(), False)
                self.selector.register(stream, selectors.EVENT_READ)
            self.call(
                "initialize",
                {
                    "clientInfo": {"name": "clink-scoped-trust-verification", "title": None, "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            self._send({"method": "initialized"})
            return self
        except BaseException:
            self.close()
            raise

    def _send(self, payload: dict[str, Any]) -> None:
        if self.closed or self.process is None or self.process.stdin is None:
            raise TrustError("rpc_process_unavailable")
        encoded = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > 65536:
            raise TrustError("rpc_request_bound")
        try:
            self.process.stdin.write(encoded)
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise TrustError("rpc_input_failed") from None

    def call(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        allowed = {"initialize", "config/read", "configRequirements/read", "hooks/list"}
        if self.allow_writes:
            allowed.add("config/batchWrite")
        if method not in allowed:
            raise TrustError("rpc_method_not_allowed")
        if method == "initialize" and self.sequence:
            raise TrustError("rpc_initialize_repeated")
        self.sequence += 1
        request_id = self.sequence
        request_deadline = min(self.deadline, time.monotonic() + self.timeout)
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            while b"\n" in self.buffer:
                line, _, remaining = self.buffer.partition(b"\n")
                self.buffer = bytearray(remaining)
                try:
                    response = json.loads(line)
                except (ValueError, UnicodeError):
                    raise TrustError("rpc_invalid_json") from None
                if not isinstance(response, dict):
                    raise TrustError("rpc_invalid_message")
                if "method" in response:
                    if "id" in response:
                        raise TrustError("rpc_unsolicited_request")
                    continue  # Native startup notifications remain private.
                if response.get("id") != request_id:
                    raise TrustError("rpc_response_mismatch")
                if "error" in response:
                    raise TrustError("rpc_native_error")
                if not isinstance(response.get("result"), dict):
                    raise TrustError("rpc_invalid_result")
                return response["result"]
            remaining_time = request_deadline - time.monotonic()
            if remaining_time <= 0:
                raise TrustError("rpc_timeout")
            events = self.selector.select(min(remaining_time, 0.2))
            for key, _ in events:
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    self.selector.unregister(key.fileobj)
                    continue
                self.total_bytes += len(data)
                if self.total_bytes > self.output_limit:
                    raise TrustError("rpc_output_bound")
                if self.process is not None and key.fileobj is self.process.stdout:
                    self.buffer.extend(data)
            if not self.selector.get_map():
                raise TrustError("rpc_process_ended")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        process = self.process
        try:
            if process is not None:
                # Buffered stdin.close() can flush into a dead reader, raising
                # or blocking. Reap our process group before closing any pipe.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                    except ProcessLookupError:
                        break
                    except OSError:
                        self.cleanup_failure = "rpc_group_signal_failed"
                        try:
                            process.kill()
                        except OSError:
                            self.cleanup_failure = "rpc_process_signal_failed"
                        break
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        continue
                    except OSError:
                        self.cleanup_failure = "rpc_process_wait_failed"
                    # Group descendants may survive the leader's exit.
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.cleanup_failure = "rpc_cleanup_incomplete"
                except OSError:
                    self.cleanup_failure = "rpc_process_wait_failed"
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    self.cleanup_complete = process.returncode is not None
                except OSError:
                    self.cleanup_failure = "rpc_group_observation_failed"
        finally:
            try:
                self.selector.close()
            except (OSError, ValueError):
                self.cleanup_failure = "rpc_selector_close_failed"
            self.buffer.clear()
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            self.cleanup_failure = "rpc_stream_close_failed"

    def __exit__(self, *args: Any) -> None:
        self.close()


@dataclass(repr=False)
class _Snapshot:
    config: dict[str, Any]
    user: dict[str, Any]
    version: str
    file: str
    profile: Any
    layers: list[dict[str, Any]]


def _get(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = document
    for segment in path:
        if not isinstance(value, dict):
            raise TrustError("trust_parent_not_table")
        if segment not in value:
            return _MISSING
        value = value[segment]
    return value


def _key(path: tuple[str, ...]) -> str:
    return ".".join(path[:-1]) + "." + json.dumps(path[-1])


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TrustScope:
    """Enroll from absence, observe native hashes, then remove unchanged own records."""

    def __init__(self, rpc: Rpc, binding: PreparedBinding):
        self.rpc, self.binding = rpc, binding
        self.project_path = ("projects", str(binding.root))
        self.records = {self.project_path: {"trust_level": "trusted"}}
        self.records.update(
            {("hooks", "state", key): {"trusted_hash": value} for key, value in binding.hook_hashes.items()}
        )
        self.owned: dict[tuple[str, ...], dict[str, Any]] = {}
        self._unconfirmed_paths: set[tuple[str, ...]] = set()
        self.receipt: dict[str, Any] = {
            "scope": "native_codex_scoped_fixture_trust",
            "source_commit": SOURCE_COMMIT,
            "top_level_model_submissions": 0,
            "runtime_permissions_resolved": False,
            "native_startup_side_effects_possible": True,
            "final_cas_or_interprocess_lock": False,
            "incidental_normalization_entries": 0,
            "unexpected_unrelated_change": False,
            "native_write_attempted": False,
            "native_write_confirmed": False,
        }

    def _read(self) -> _Snapshot:
        self.binding.validate()
        data = self.rpc.call("config/read", {"includeLayers": True, "cwd": str(self.binding.root)})
        layers, config = data.get("layers"), data.get("config")
        if not isinstance(layers, list) or not isinstance(config, dict):
            raise TrustError("config_inventory_invalid")
        users = [
            x
            for x in layers
            if isinstance(x, dict) and isinstance(x.get("name"), dict) and x["name"].get("type") == "user"
        ]
        if not users:
            raise TrustError("native_user_layer_unavailable")
        active = users[0]  # API order is high to low, matching get_active_user_layer.
        name, version, user = active["name"], active.get("version"), active.get("config")
        if (
            not isinstance(version, str)
            or not _HASH.fullmatch(version)
            or not isinstance(user, dict)
            or not isinstance(name.get("file"), str)
        ):
            raise TrustError("native_user_layer_invalid")
        return _Snapshot(config, user, version, name["file"], name.get("profile"), layers)

    def _settings(self, snapshot: _Snapshot) -> dict[str, Any]:
        # These are configured/managed-exact readouts, not ConfigBuilder defaults.
        fields = ("model", "model_reasoning_effort", "approval_policy", "approvals_reviewer", "sandbox_mode")
        result = {}
        for name in fields:
            value = snapshot.config.get(name)
            if value is None or isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value):
                result[name] = value
            elif name == "approval_policy" and isinstance(value, dict):
                result[name] = "structured_native_policy"
            else:
                result[name] = "unavailable"
        return result

    def _hooks(self) -> dict[str, Any]:
        data = self.rpc.call("hooks/list", {"cwds": [str(self.binding.root)]})
        rows = data.get("data")
        if not isinstance(rows, list):
            raise TrustError("hook_inventory_invalid")
        matches = [row for row in rows if isinstance(row, dict) and row.get("cwd") == str(self.binding.root)]
        if len(matches) != 1 or not isinstance(matches[0].get("hooks"), list):
            raise TrustError("hook_inventory_invalid")
        row = matches[0]
        found = {}
        for hook in row["hooks"]:
            if not isinstance(hook, dict) or hook.get("sourcePath") != str(self.binding.root / ".codex/hooks.json"):
                continue
            key = hook.get("key")
            if not isinstance(key, str) or key in found or key not in self.binding.hook_hashes:
                raise TrustError("hook_inventory_binding_mismatch")
            found[key] = hook
        exact = set(found) == set(self.binding.hook_hashes)
        hashes_match = exact and all(
            found[key].get("currentHash") == value for key, value in self.binding.hook_hashes.items()
        )
        enabled = exact and all(found[key].get("enabled") is True for key in found)
        return {
            "fixture_hook_count": len(found),
            "native_hook_hashes_match": hashes_match,
            "native_fixture_hooks_enabled": enabled,
            "native_fixture_hooks_trusted": exact and all(found[key].get("trustStatus") == "trusted" for key in found),
            "hook_inventory_errors_present": bool(row.get("errors")),
        }

    def _requirements(self) -> dict[str, Any]:
        # Option<()> accepts null, not an empty object. This global read loads
        # merged managed constraints, not a fixture-specific runtime profile.
        data = self.rpc.call("configRequirements/read", None)
        requirements = data.get("requirements", _MISSING)
        if requirements is not None and not isinstance(requirements, dict):
            raise TrustError("managed_requirements_invalid")
        summary: dict[str, Any] = {
            "managed_requirements_observed": True,
            "managed_requirements_present": requirements is not None,
            "managed_constraints": None,
        }
        if requirements is None:
            return summary
        selected = {}
        for field_name in ("allowedApprovalPolicies", "allowedApprovalsReviewers", "allowedSandboxModes"):
            value = requirements.get(field_name)
            if value is not None and not isinstance(value, list):
                raise TrustError("managed_requirements_invalid")
            labels = None
            if isinstance(value, list):
                labels = []
                for item in value:
                    if isinstance(item, str) and re.fullmatch(r"[a-zA-Z-]{1,64}", item):
                        labels.append(item)
                    elif field_name == "allowedApprovalPolicies" and isinstance(item, dict):
                        labels.append("structured_native_policy")
                    else:
                        raise TrustError("managed_requirements_invalid")
            selected[field_name] = labels
        profiles = requirements.get("allowedPermissionProfiles")
        if profiles is not None and not isinstance(profiles, dict):
            raise TrustError("managed_requirements_invalid")
        managed_only = requirements.get("allowManagedHooksOnly")
        if managed_only is not None and not isinstance(managed_only, bool):
            raise TrustError("managed_requirements_invalid")
        selected.update(
            {
                "allowedPermissionProfileCount": len(profiles) if isinstance(profiles, dict) else None,
                "defaultPermissionsConfigured": requirements.get("defaultPermissions") is not None,
                "allowManagedHooksOnly": managed_only,
            }
        )
        summary["managed_constraints"] = selected
        # Bind relevant raw constraints without exporting profile names or
        # structured policies. Provider definitions/instructions are excluded.
        summary["managed_constraints_sha256"] = _fingerprint(
            {
                key: requirements.get(key)
                for key in (
                    "allowedApprovalPolicies",
                    "allowedApprovalsReviewers",
                    "allowedSandboxModes",
                    "allowedPermissionProfiles",
                    "defaultPermissions",
                    "allowManagedHooksOnly",
                )
            }
        )
        return summary

    def _absent(self, snapshot: _Snapshot) -> None:
        for path in self.records:
            if _get(snapshot.user, path) is not _MISSING:
                raise TrustError("trust_entry_preexisting")
            # config/read serializes ConfigToml's optional projects/hooks as
            # null when absent. Only these effective API roots are nullable;
            # raw TOML layers and nested tables retain strict validation.
            root_is_null = path[0] in {"projects", "hooks"} and snapshot.config.get(path[0], _MISSING) is None
            if not root_is_null and _get(snapshot.config, path) is not _MISSING:
                raise TrustError("trust_entry_preexisting")
            for layer in snapshot.layers:
                if (
                    isinstance(layer, dict)
                    and isinstance(layer.get("config"), dict)
                    and _get(layer["config"], path) is not _MISSING
                ):
                    raise TrustError("trust_entry_preexisting")

    def inventory(self) -> dict[str, Any]:
        snapshot = self._read()
        self.receipt["configured_before"] = self._settings(snapshot)
        self.receipt["active_user_profile"] = snapshot.profile is not None
        self.receipt["preexisting_record_count"] = sum(
            _get(snapshot.user, path) is not _MISSING for path in self.records
        )
        self.receipt.update(self._hooks())
        self.receipt.update(self._requirements())
        return copy.deepcopy(self.receipt)

    def _unrelated(self, user: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(user)
        for path in self.records:
            parent = result
            for segment in path[:-1]:
                child = parent.get(segment)
                if not isinstance(child, dict):
                    break
                parent = child
            else:
                parent.pop(path[-1], None)
        # Native deletion can retain empty parent tables; these carry no grants.
        hooks = result.get("hooks")
        if isinstance(hooks, dict) and hooks.get("state") == {}:
            hooks.pop("state")
        for name in ("projects", "hooks"):
            if result.get(name) == {}:
                result.pop(name)
        return result

    def _write(self, snapshot: _Snapshot, edits: dict[tuple[str, ...], Any]) -> _Snapshot:
        if snapshot.profile is not None:
            raise TrustError("active_profile_write_target_unverified")
        before = self._unrelated(snapshot.user)
        self.receipt["native_write_attempted"] = True
        try:
            result = self.rpc.call(
                "config/batchWrite",
                {
                    "edits": [
                        {"keyPath": _key(path), "value": value, "mergeStrategy": "replace"}
                        for path, value in edits.items()
                    ],
                    "filePath": snapshot.file,
                    "expectedVersion": snapshot.version,
                    "reloadUserConfig": False,
                },
            )
        except BaseException:
            # A lost acknowledgement cannot prove ownership. Do not delete a
            # matching record that a concurrent writer might have created.
            self._unconfirmed_paths.update(edits)
            self.receipt["unconfirmed_write_record_count"] = len(self._unconfirmed_paths)
            raise
        if result.get("status") not in {"ok", "okOverridden"} or result.get("filePath") != snapshot.file:
            self._unconfirmed_paths.update(edits)
            self.receipt["unconfirmed_write_record_count"] = len(self._unconfirmed_paths)
            raise TrustError("native_write_not_confirmed")
        self._unconfirmed_paths.difference_update(edits)
        self.receipt["unconfirmed_write_record_count"] = len(self._unconfirmed_paths)
        self.owned.update({path: copy.deepcopy(value) for path, value in edits.items() if value is not None})
        self.receipt["native_write_confirmed"] = True
        if result["status"] != "ok":
            raise TrustError("native_write_overridden")
        after = self._read()
        if after.file != snapshot.file:
            raise TrustError("native_write_target_changed")
        expected = copy.deepcopy(before)
        states = expected.get("hooks", {}).get("state", {})
        normalized = 0
        if isinstance(states, dict):
            for state in states.values():
                value = state.get("trusted_hash") if isinstance(state, dict) else None
                if isinstance(value, str) and "/" in value:
                    state["trusted_hash"], state["trusted_content_hash"] = value.split("/", 1)
                    normalized += 1
        actual = self._unrelated(after.user)
        if actual == expected:
            self.receipt["incidental_normalization_entries"] += normalized
        self.receipt["unrelated_before_sha256"] = _fingerprint(before)
        self.receipt["unrelated_after_sha256"] = _fingerprint(actual)
        if actual != expected:
            self.receipt["unexpected_unrelated_change"] = True
            raise TrustError("unexpected_unrelated_change")
        return after

    def enroll(self) -> dict[str, Any]:
        if self.owned:
            raise TrustError("scope_already_enrolled")
        snapshot = self._read()
        self._absent(snapshot)
        if snapshot.profile is not None:
            raise TrustError("active_profile_write_target_unverified")
        self.receipt["configured_before"] = self._settings(snapshot)
        requirements_before = self._requirements()
        self.receipt.update(requirements_before)
        permissions_before = {
            name: snapshot.config.get(name) for name in ("approval_policy", "approvals_reviewer", "sandbox_mode")
        }
        project = {self.project_path: self.records[self.project_path]}
        try:
            snapshot = self._write(snapshot, project)
            if _get(snapshot.user, self.project_path) != project[self.project_path]:
                raise TrustError("project_trust_not_observed")
            self.receipt.update(self._hooks())
            if (
                not self.receipt["native_hook_hashes_match"]
                or not self.receipt["native_fixture_hooks_enabled"]
                or self.receipt["hook_inventory_errors_present"]
            ):
                raise TrustError("native_hook_hash_gate_failed")
            snapshot = self._read()  # Fresh version immediately before the next write.
            hooks = {path: value for path, value in self.records.items() if path != self.project_path}
            if any(_get(snapshot.user, path) is not _MISSING for path in hooks):
                raise TrustError("trust_entry_preexisting")
            snapshot = self._write(snapshot, hooks)
            if any(_get(snapshot.user, path) != value for path, value in self.records.items()):
                raise TrustError("trust_records_not_observed")
            self.receipt.update(self._hooks())
            if (
                not self.receipt["native_hook_hashes_match"]
                or not self.receipt["native_fixture_hooks_trusted"]
                or not self.receipt["native_fixture_hooks_enabled"]
                or self.receipt["hook_inventory_errors_present"]
            ):
                raise TrustError("native_hook_trust_not_observed")
            self.receipt["configured_after"] = self._settings(snapshot)
            self.receipt["configured_settings_changed"] = (
                self.receipt["configured_after"] != self.receipt["configured_before"]
            )
            self.receipt["configured_permission_values_changed"] = permissions_before != {
                name: snapshot.config.get(name) for name in permissions_before
            }
            requirements_after = self._requirements()
            self.receipt["managed_constraints_changed"] = requirements_after != requirements_before
            self.receipt.update(requirements_after)
            if self.receipt["managed_constraints_changed"]:
                raise TrustError("managed_requirements_changed")
            self.receipt["enrolled_record_count"] = len(self.owned)
            return copy.deepcopy(self.receipt)
        except BaseException as error:
            self.receipt["enrollment_failure"] = (
                error.code if isinstance(error, TrustError) else "unexpected_helper_failure"
            )
            try:
                self.cleanup()
            except BaseException as cleanup_error:
                self.receipt["cleanup_failure"] = (
                    cleanup_error.code if isinstance(cleanup_error, TrustError) else "unexpected_helper_failure"
                )
                self.receipt["cleanup_complete"] = False
                raise TrustError("enrollment_and_cleanup_failed") from None
            raise

    def cleanup(self) -> dict[str, Any]:
        if not self.owned:
            unconfirmed = self.receipt.get("unconfirmed_write_record_count", 0)
            self.receipt.setdefault("cleanup_removed_count", 0)
            self.receipt.setdefault("cleanup_conflict_count", 0)
            self.receipt.update(
                {
                    "cleanup_unconfirmed_write_record_count": unconfirmed,
                    "cleanup_complete": not unconfirmed,
                }
            )
            return copy.deepcopy(self.receipt)
        snapshot = self._read()
        removable = {}
        conflicts = 0
        for path, inserted in self.owned.items():
            current = _get(snapshot.user, path)
            if current is _MISSING:
                continue
            if current == inserted:
                removable[path] = None
            else:
                conflicts += 1
        if removable:
            snapshot = self._write(snapshot, removable)
        remaining = sum(_get(snapshot.user, path) is not _MISSING for path in self.owned)
        self.receipt.update(
            {
                "cleanup_removed_count": len(removable),
                "cleanup_conflict_count": conflicts,
                "cleanup_remaining_count": remaining,
                "cleanup_unconfirmed_write_record_count": self.receipt.get("unconfirmed_write_record_count", 0),
                "cleanup_complete": remaining == 0 and not self.receipt.get("unconfirmed_write_record_count", 0),
            }
        )
        if remaining == 0:
            self.owned.clear()
        return copy.deepcopy(self.receipt)
