"""Exercise Codex canaries offline without native login, configuration or model work."""

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts import native_codex_fixture as fixture
from scripts.verify_native_cli import SENTINEL_NAME, SENTINEL_VALUE

try:
    import tomllib
except ImportError:
    tomllib = None


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "synthetic project"
    root.mkdir()
    (root / ".clink-native-verification-fixture").write_text("synthetic-native-adapter-fixture-v1\n")
    return root


def hook(root, event, *, check=True):
    return subprocess.run(
        [sys.executable, str(root / "codex_hook.py")],
        cwd=root,
        input=json.dumps({"cwd": str(root), **event}) if isinstance(event, dict) else event,
        text=True,
        capture_output=True,
        check=check,
        timeout=5,
    )


def stream(*, command=fixture.PERMISSION_COMMAND, status="declined", exit_code=-1, output=None):
    if output is None:
        output = f"`{command}` rejected: policy forbids commands starting with `{fixture.PERMISSION_COMMAND}`"
    events = [
        {"type": "thread.started", "thread_id": "11111111-1111-4111-8111-111111111111"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": command,
                "aggregated_output": output,
                "status": status,
                "exit_code": exit_code,
            },
        },
        {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "Stopped."}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},
    ]
    return "\n".join(json.dumps(event) for event in events)


def test_fixture_has_documented_native_configuration_and_preserves_host_environment(project, tmp_path, monkeypatch):
    if tomllib is None:
        pytest.skip("Verification configuration inspection requires Python 3.11+")
    native_home = tmp_path / "existing-native-home"
    native_home.mkdir()
    private = native_home / "config.toml"
    private.write_text("PRIVATE_EXISTING_NATIVE_CONFIG\n")
    monkeypatch.setenv("HOME", str(native_home))
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    before_environment = dict(os.environ)

    paths = fixture.create_codex_fixture(project)

    assert paths == sorted(paths)
    assert all((project / name).is_file() and not (project / name).is_symlink() for name in paths)
    assert dict(os.environ) == before_environment
    assert private.read_text() == "PRIVATE_EXISTING_NATIVE_CONFIG\n"
    config = tomllib.loads((project / ".codex/config.toml").read_text())
    assert set(config) == {"mcp_servers", "agents"}
    mcp = config["mcp_servers"]["native_fixture"]
    assert mcp["command"] == sys.executable
    assert mcp["args"] == [str(project / "codex_mcp_server.py")]
    assert mcp["env_vars"] == [SENTINEL_NAME]
    assert mcp["enabled"] is mcp["required"] is True
    role = config["agents"][fixture.SPECIALIST_ROLE]
    assert role["description"]
    assert role["config_file"] == "agents/fixture-specialist.toml"
    specialist = tomllib.loads((project / ".codex/agents/fixture-specialist.toml").read_text())
    assert set(specialist) == {"developer_instructions"}
    assert str(project / "codex-specialist.txt") in specialist["developer_instructions"]
    rules = (project / ".codex/rules/native-fixture.rules").read_text()
    assert rules == (
        'prefix_rule(pattern = ["/usr/bin/printf", "CLINK_NATIVE_POLICY_CANARY"], decision = "forbidden")\n'
    )
    assert "justification" not in rules
    hooks = json.loads((project / ".codex/hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop"}
    assert "--dangerously" not in json.dumps(hooks)
    assert SENTINEL_VALUE not in (project / ".codex/config.toml").read_text()
    assert fixture.codex_fixture_gaps(project) == [
        "codex_project_trust_unverified",
        "codex_project_hook_trust_unverified",
        "codex_mcp_environment_unverified",
        "codex_named_subagent_unverified",
        "codex_permission_policy_unverified",
    ]


def test_fixture_requires_canonical_marked_project_and_refuses_partial_overwrite(project, tmp_path):
    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    with pytest.raises(ValueError, match="marker"):
        fixture.create_codex_fixture(unmarked)
    with pytest.raises(ValueError, match="canonical"):
        fixture.create_codex_fixture(Path("relative-project"))
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical"):
        fixture.create_codex_fixture(alias)
    (project / "codex-specialist.txt").write_text("existing synthetic file")
    with pytest.raises(ValueError, match="existing generated"):
        fixture.create_codex_fixture(project)
    assert not (project / "codex_hook.py").exists()
    assert not (project / ".codex").exists()


def test_fixture_refuses_symlinked_and_non_directory_configuration(project, tmp_path):
    outside = tmp_path / "other"
    outside.mkdir()
    (project / ".codex").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe configuration"):
        fixture.create_codex_fixture(project)
    assert list(outside.iterdir()) == []
    (project / ".codex").unlink()
    (project / ".codex").write_text("not a directory")
    with pytest.raises(ValueError, match="unsafe configuration"):
        fixture.create_codex_fixture(project)
    assert not (project / "codex_hook.py").exists()


def test_passive_hook_observes_exact_permission_command_without_changing_authority(project):
    fixture.create_codex_fixture(project)
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": fixture.PERMISSION_COMMAND},
        "session_id": "PRIVATE_SESSION_ID",
        "tool_response": "PRIVATE_TOOL_RESPONSE",
    }
    assert json.loads(hook(project, event).stdout) == {}
    observed = fixture.codex_fixture_observations(project)
    assert observed["scope_hook_observed"] is observed["permission_hook_observed"] is True
    assert observed["permission_policy_declined_observed"] is False
    encoded = (project / "codex-hooks.jsonl").read_text()
    assert "PRIVATE_SESSION_ID" not in encoded
    assert "PRIVATE_TOOL_RESPONSE" not in encoded
    assert fixture.PERMISSION_COMMAND not in encoded


def test_named_specialist_stop_requires_exact_child_role_and_exact_latest_token(project):
    fixture.create_codex_fixture(project)

    def child(kind, *, role=fixture.SPECIALIST_ROLE, text=None):
        return hook(
            project,
            {
                "hook_event_name": kind,
                "agent_id": "PRIVATE_CHILD_ID",
                "agent_type": role,
                "last_assistant_message": text,
            },
        )

    child("SubagentStart")
    child("SubagentStop", text=None)
    child("SubagentStop", role="unrelated", text=fixture.SPECIALIST_TOKEN)
    child("SubagentStop", text="prefix " + fixture.SPECIALIST_TOKEN)
    observed = fixture.codex_fixture_observations(project)
    assert observed["fixture_subagent_started"] is observed["fixture_subagent_stopped"] is True
    assert observed["fixture_subagent_canary_matches"] is False
    assert json.loads(child("SubagentStop", text=fixture.SPECIALIST_TOKEN + "\n").stdout) == {}
    assert fixture.codex_fixture_observations(project)["fixture_subagent_canary_matches"] is True
    encoded = (project / "codex-hooks.jsonl").read_text()
    assert "PRIVATE_CHILD_ID" not in encoded
    assert fixture.SPECIALIST_TOKEN not in encoded


def test_named_specialist_canary_requires_start_and_stop_of_the_same_child(project):
    fixture.create_codex_fixture(project)

    def child(kind, identity, text=None):
        hook(
            project,
            {
                "hook_event_name": kind,
                "agent_id": identity,
                "agent_type": fixture.SPECIALIST_ROLE,
                "last_assistant_message": text,
            },
        )

    child("SubagentStart", "PRIVATE_CHILD_A")
    child("SubagentStop", "PRIVATE_CHILD_B", fixture.SPECIALIST_TOKEN)
    observed = fixture.codex_fixture_observations(project)
    assert observed["fixture_subagent_started"] is True
    assert observed["fixture_subagent_stopped"] is observed["fixture_subagent_canary_matches"] is False
    child("SubagentStop", "PRIVATE_CHILD_A", "different token")
    child("SubagentStart", "PRIVATE_CHILD_B")
    observed = fixture.codex_fixture_observations(project)
    assert observed["fixture_subagent_stopped"] is True
    assert observed["fixture_subagent_canary_matches"] is False
    child("SubagentStop", "PRIVATE_CHILD_B", fixture.SPECIALIST_TOKEN)
    observed = fixture.codex_fixture_observations(project)
    assert observed["fixture_subagent_canary_matches"] is True
    encoded = (project / "codex-hooks.jsonl").read_text()
    assert "PRIVATE_CHILD_A" not in encoded
    assert "PRIVATE_CHILD_B" not in encoded
    assert hashlib.sha256(b"PRIVATE_CHILD_B").hexdigest() in encoded
    assert hashlib.sha256(b"PRIVATE_CHILD_B").hexdigest() not in json.dumps(observed)


@pytest.mark.parametrize(
    "event",
    [
        "not json",
        {"hook_event_name": "SessionStart", "cwd": "/unrelated"},
        {"hook_event_name": "SessionStart"},
        {"hook_event_name": []},
        {"hook_event_name": "SubagentStart", "agent_type": fixture.SPECIALIST_ROLE},
    ],
)
def test_bad_hook_input_cannot_establish_native_hook_observation(project, event):
    fixture.create_codex_fixture(project)
    assert json.loads(hook(project, event).stdout) == {}
    assert fixture.codex_fixture_observations(project)["scope_hook_observed"] is False


def test_hook_log_is_bounded_and_cannot_follow_a_symlink(project, tmp_path):
    fixture.create_codex_fixture(project)
    log = project / "codex-hooks.jsonl"
    log.write_bytes(b" " * fixture.MAX_OBSERVATION_BYTES)
    event = {"hook_event_name": "SessionStart", "source": "startup"}
    hook(project, event)
    assert log.stat().st_size == fixture.MAX_OBSERVATION_BYTES
    log.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text('{"private": true}\n')
    log.symlink_to(outside)
    assert hook(project, event, check=False).returncode != 0
    assert outside.read_text() == '{"private": true}\n'
    assert fixture.codex_fixture_observations(project)["scope_hook_observed"] is False


def test_concurrent_passive_hooks_keep_shared_observation_log_bounded(project):
    fixture.create_codex_fixture(project)
    log = project / "codex-hooks.jsonl"
    log.write_bytes(b" " * (fixture.MAX_OBSERVATION_BYTES - 220))
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _: hook(project, {"hook_event_name": "SessionStart", "source": "startup"}), range(8))
        )
    assert all(json.loads(result.stdout) == {} for result in results)
    assert log.stat().st_size <= fixture.MAX_OBSERVATION_BYTES


def test_exact_valid_policy_refusal_produces_only_boolean_evidence_and_replaces_stale_proof(project):
    fixture.create_codex_fixture(project)
    command = "/bin/zsh -lc '" + fixture.PERMISSION_COMMAND + "'"
    fixture.observe_codex_stream(project, stream(command=command))
    observed = fixture.codex_fixture_observations(project)
    assert observed["permission_policy_declined_observed"] is True
    assert "codex_project_trust_unverified" not in fixture.codex_fixture_gaps(project)
    assert "codex_project_hook_trust_unverified" in fixture.codex_fixture_gaps(project)
    stored = (project / "codex-stream-observed.json").read_text()
    assert json.loads(stored) == {"stream_valid": True, "permission_policy_declined_observed": True}
    assert "11111111" not in stored
    assert fixture.PERMISSION_COMMAND not in stored
    fixture.observe_codex_stream(project, stream(output="could not prepare approval action: synthetic setup failure"))
    assert fixture.codex_fixture_observations(project)["permission_policy_declined_observed"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed"},
        {"status": "completed", "exit_code": 0},
        {"output": "permission denied"},
        {"output": "`different command` rejected: policy forbids commands starting with `other prefix`"},
        {
            "output": "`different command` rejected: policy forbids commands starting with `"
            + fixture.PERMISSION_COMMAND
            + "`"
        },
    ],
)
def test_non_canary_failures_cannot_establish_permission_evidence(project, changes):
    fixture.create_codex_fixture(project)
    fixture.observe_codex_stream(project, stream(**changes))
    assert fixture.codex_fixture_observations(project)["permission_policy_declined_observed"] is False


@pytest.mark.parametrize("suffix", ["\nnot json", '\n{"type":"turn.started"}'])
def test_invalid_or_post_terminal_stream_cannot_establish_permission_evidence(project, suffix):
    fixture.create_codex_fixture(project)
    fixture.observe_codex_stream(project, stream() + suffix)
    assert json.loads((project / "codex-stream-observed.json").read_text()) == {
        "stream_valid": False,
        "permission_policy_declined_observed": False,
    }


@pytest.mark.parametrize("text", ["\ud800", " " * (fixture.MAX_STREAM_BYTES + 1)])
def test_unencodable_or_oversized_stream_cannot_establish_permission_evidence(project, text):
    fixture.create_codex_fixture(project)
    fixture.observe_codex_stream(project, text)
    assert json.loads((project / "codex-stream-observed.json").read_text()) == {
        "stream_valid": False,
        "permission_policy_declined_observed": False,
    }


def test_observation_files_reject_symlinks_and_hardlinks_without_modifying_external_data(project, tmp_path):
    fixture.create_codex_fixture(project)
    outside = tmp_path / "outside.json"
    outside.write_text('{"stream_valid": true, "permission_policy_declined_observed": true}\n')
    path = project / "codex-stream-observed.json"
    path.symlink_to(outside)
    assert fixture.codex_fixture_observations(project)["permission_policy_declined_observed"] is False
    with pytest.raises(OSError):
        fixture.observe_codex_stream(project, stream())
    path.unlink()
    os.link(outside, path)
    before = outside.read_bytes()
    with pytest.raises(ValueError, match="regular file"):
        fixture.observe_codex_stream(project, stream())
    assert outside.read_bytes() == before
    assert fixture.codex_fixture_observations(project)["permission_policy_declined_observed"] is False


def test_mcp_canary_observes_forwarded_sentinel_without_recording_value(project):
    fixture.create_codex_fixture(project)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "canary"}}
    result = subprocess.run(
        [sys.executable, str(project / "codex_mcp_server.py")],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        env={"PATH": os.environ.get("PATH", ""), SENTINEL_NAME: SENTINEL_VALUE},
        check=True,
        timeout=5,
    )
    assert json.loads(result.stdout)["result"]["isError"] is False
    assert fixture.codex_fixture_observations(project)["mcp_environment_canary_matches"] is True
    assert SENTINEL_VALUE not in (project / "mcp-called.json").read_text()


def test_oversized_or_malformed_observations_fail_closed(project):
    fixture.create_codex_fixture(project)
    path = project / "codex-hooks.jsonl"
    path.write_text('{"event":"session_start","payload_valid":true}\nnot json\n')
    assert fixture.codex_fixture_observations(project)["scope_hook_observed"] is False
    path.write_bytes(b" " * (fixture.MAX_OBSERVATION_BYTES + 1))
    assert fixture.codex_fixture_observations(project)["scope_hook_observed"] is False
