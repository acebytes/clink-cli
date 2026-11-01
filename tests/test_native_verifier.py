"""Exercise native verifier scope guards and evidence redaction without a model."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_native_cli", Path(__file__).resolve().parents[1] / "scripts" / "verify_native_cli.py"
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def hook(root, tool, data):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": data}
    result = subprocess.run(
        [sys.executable, str(root / "scope_hook.py")],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=True,
    )
    value = json.loads(result.stdout) if result.stdout else {}
    return {} if value.get("hookSpecificOutput", {}).get("permissionDecision") == "allow" else value


def test_fixture_hook_confines_edits_commands_and_agent(tmp_path):
    verifier.create_fixture(tmp_path)
    assert hook(tmp_path, "Edit", {"file_path": str(tmp_path / "arithmetic.py")}) == {}
    assert (
        hook(tmp_path, "Edit", {"file_path": str(tmp_path / "../outside.py")})["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )
    assert (
        hook(tmp_path, "Write", {"file_path": str(tmp_path / ".claude/settings.json")})["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )
    assert hook(tmp_path, "Bash", {"command": "python fixture.py"}) == {}
    assert hook(tmp_path, "Bash", {"command": verifier.fixture_command()}) == {}
    assert (
        hook(tmp_path, "Bash", {"command": "python fixture.py; echo unsafe"})["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )
    assert (
        hook(tmp_path, "Bash", {"command": "python fixture.py", "run_in_background": True})["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )
    assert hook(tmp_path, "Agent", {"subagent_type": "fixture-specialist"}) == {}
    assert hook(tmp_path, "Agent", {"subagent_type": "unrelated"})["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert hook(tmp_path, "mcp__native_fixture__canary", {}) == {}
    assert hook(tmp_path, "mcp__unrelated__mutate", {})["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        hook(tmp_path, "Write", {"file_path": str(tmp_path / "permission-needed.txt")})["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "ask"
    )


def test_fixture_mcp_requires_inherited_synthetic_environment(tmp_path):
    verifier.create_fixture(tmp_path)
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "canary"}})
    missing = subprocess.run(
        [sys.executable, str(tmp_path / "mcp_server.py")],
        input=request + "\n",
        text=True,
        capture_output=True,
        check=True,
        env={},
    )
    assert json.loads(missing.stdout)["result"]["isError"] is True
    inherited = subprocess.run(
        [sys.executable, str(tmp_path / "mcp_server.py")],
        input=request + "\n",
        text=True,
        capture_output=True,
        check=True,
        env={verifier.SENTINEL_NAME: verifier.SENTINEL_VALUE},
    )
    assert json.loads(inherited.stdout)["result"]["isError"] is False
    assert json.loads((tmp_path / "mcp-called.json").read_text())["inherited_sentinel_matches"] is True


def test_environment_preserves_native_locations_without_copying_secret_values():
    source = {
        "HOME": "/synthetic/home",
        "PATH": "/synthetic/bin",
        "CLAUDE_CONFIG_DIR": "/synthetic/config",
        "ANTHROPIC_API_KEY": "never-copy-this",
        "CLAUDE_CODE_SAFE_MODE": "1",
    }
    result = verifier.environment(source)
    assert result["HOME"] == source["HOME"]
    assert result["CLAUDE_CONFIG_DIR"] == source["CLAUDE_CONFIG_DIR"]
    assert "ANTHROPIC_API_KEY" not in result
    assert "CLAUDE_CODE_SAFE_MODE" not in result
    assert result[verifier.SENTINEL_NAME] == verifier.SENTINEL_VALUE


def test_evidence_never_exports_raw_session_result_or_tool_input():
    summary = verifier.summarize(
        [
            {"type": "system", "subtype": "init", "session_id": "private-native-session", "model": "claude-test"},
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "SECRET_TOOL_INPUT"}}]
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "private-native-session",
                "result": "SECRET_RESULT",
                "permission_denials": [
                    {"tool_name": "Write", "tool_use_id": "private", "tool_input": {"file_path": "SECRET_PATH"}}
                ],
            },
        ]
    )
    encoded = json.dumps(summary)
    for private in ("private-native-session", "SECRET_TOOL_INPUT", "SECRET_RESULT", "SECRET_PATH"):
        assert private not in encoded
    assert summary["tool_use_counts"]["Bash"] == 1
    assert summary["terminal"]["permission_denial_count"] == 1
    assert summary["session_id_sha256"] == [verifier.sha256("private-native-session")]


def test_native_test_results_are_observed_without_exporting_tool_text():
    summary = verifier.summarize(
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "content": "private-prefix Ran 1 test\nFAILED (failures=1)"},
                        {"type": "tool_result", "content": "private-prefix Ran 1 test\nOK"},
                    ]
                },
            },
        ]
    )
    assert summary["known_fixture_test_failure_count"] == 1
    assert summary["known_fixture_test_success_count"] == 1
    assert "private-prefix" not in json.dumps(summary)


def test_permission_denial_prevents_treating_success_envelope_as_success():
    assert not verifier.successful(
        {
            "reason": "exited",
            "returncode": 0,
            "terminal": {"subtype": "success", "is_error": False, "permission_denial_count": 1},
        }
    )


def test_run_once_records_explicit_error_without_retry(tmp_path, monkeypatch):
    target = tmp_path / "calls.txt"
    code = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_text('one launch'); sys.exit(7)"
    reaped = []

    def reap_error_child(process):
        # This test owns exit/no-retry metadata; the cancellation test below exercises real group cleanup.
        assert process.wait(timeout=3) == 7
        reaped.append(process.pid)
        return False  # Group absence was not probed, so the synthetic cleanup result remains unverified.

    monkeypatch.setattr(verifier, "stop_group", reap_error_child)
    summary, events = verifier.run_once(
        [sys.executable, "-c", code, str(target)], tmp_path, dict(os.environ), "", timeout=5
    )
    assert target.read_text() == "one launch"
    assert summary["launch_count"] == 1
    assert summary["automatic_retries"] == 0
    assert summary["returncode"] == 7
    assert len(reaped) == 1
    assert summary["process_group_absent_after_cleanup"] is False
    assert events == []


def test_cancel_records_partial_effect_and_reaps_process(tmp_path):
    marker = tmp_path / "mutation.txt"
    code = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('partial'); time.sleep(30)"
    summary, _ = verifier.run_once(
        [sys.executable, "-c", code, str(marker)], tmp_path, dict(os.environ), "", timeout=5, cancel_marker=marker
    )
    assert summary["reason"] == "cancelled_after_mutation"
    assert summary["process_group_absent_after_cleanup"] is True
    assert marker.read_text() == "partial"


@pytest.mark.parametrize("timeout", [0, -1, 181])
def test_native_probe_deadline_cannot_be_unbounded(tmp_path, timeout):
    with pytest.raises(ValueError, match="180 seconds"):
        verifier.run_once([sys.executable, "-c", "pass"], tmp_path, {}, "", timeout=timeout)


def test_native_recipe_preserves_native_configuration():
    recipe = verifier.NATIVE_ARGUMENTS
    for forbidden in (
        "--bare",
        "--safe-mode",
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--tools",
        "--dangerously-skip-permissions",
        "--setting-sources",
    ):
        assert forbidden not in recipe
    assert recipe[-2:] == ("--permission-prompts", "none")


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("mismatch", ["installed_version", "prior_version", "binary"])
async def test_candidate_preflight_mismatch_prevents_fixture_and_model_launch(tmp_path, monkeypatch, repair, mismatch):
    executable = tmp_path / "synthetic-claude"
    executable.write_bytes(b"synthetic executable")
    previous = (
        tmp_path
        / "docs"
        / "verification"
        / ("native-claude-2.1.304-attempt2.json" if repair else "native-claude-2.1.304-attempt1.json")
    )
    previous.parent.mkdir(parents=True)
    previous.write_text(
        json.dumps(
            {
                "observed_version": "wrong prior version" if mismatch == "prior_version" else verifier.EXPECTED_VERSION,
                "executable_sha256": (
                    "wrong binary hash" if mismatch == "binary" else verifier.sha256(executable.read_bytes())
                ),
            }
        )
    )
    monkeypatch.setattr(verifier, "__file__", str(tmp_path / "scripts" / "verify_native_cli.py"))
    version_calls = []

    def version_only(argv, **kwargs):
        version_calls.append(argv)
        assert argv == [str(executable), "--version"]
        output = "unexpected version" if mismatch == "installed_version" else verifier.EXPECTED_VERSION
        return subprocess.CompletedProcess(argv, 0, (output + "\n").encode(), b"")

    monkeypatch.setattr(verifier.subprocess, "run", version_only)
    monkeypatch.setattr(verifier, "create_fixture", lambda *args: pytest.fail("Fixture created before valid preflight"))
    monkeypatch.setattr(
        verifier.tempfile, "TemporaryDirectory", lambda **kwargs: pytest.fail("Fixture opened before valid preflight")
    )
    from clink.runner import ProcessPool

    model_launches = []

    async def never_launch(*args, **kwargs):
        model_launches.append(True)
        pytest.fail("Model launched despite candidate preflight mismatch")

    monkeypatch.setattr(ProcessPool, "execute", never_launch)
    evidence = {}
    with pytest.raises(ValueError, match="version|binary"):
        await verifier.verify_candidate_runtime(str(executable), evidence, 1, repair_tests_cancel=repair)
    assert version_calls == [[str(executable), "--version"]]
    assert model_launches == []
    assert evidence["model_invocations_submitted"] == 0
    assert "observed_version" not in evidence
