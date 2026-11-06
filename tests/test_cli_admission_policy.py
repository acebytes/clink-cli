"""Exercise the admission verifier without invoking installed CLIs or models."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import verify_cli_policy as policy


def test_environment_preserves_auth_locations_and_drops_bypasses():
    environment = policy.child_environment(
        {
            "HOME": "/native/home",
            "CODEX_HOME": "/native/codex",
            "CLAUDE_CONFIG_DIR": "/native/claude",
            "PATH": "/native/bin",
            "ANTHROPIC_API_KEY": "secret",
            "OPENAI_API_KEY": "secret",
            "CLAUDE_CODE_SIMPLE": "1",
            "CLINK_DELEGATION_DEPTH": "0",
            "BASH_ENV": "/unsafe/startup",
            "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "unsafe",
        }
    )
    assert environment == {
        "HOME": "/native/home",
        "CODEX_HOME": "/native/codex",
        "CLAUDE_CONFIG_DIR": "/native/claude",
        "PATH": "/native/bin",
        "CLINK_DELEGATION_DEPTH": "1",
    }


@pytest.mark.parametrize("adapter", ["codex", "claude"])
def test_recipe_fingerprint_is_stable_and_sensitive_to_authority(adapter):
    recipe = policy.policy_recipe(adapter)
    assert policy.canonical_sha256(recipe) == policy.canonical_sha256(policy.policy_recipe(adapter))
    changed = {**recipe, "argv": [*recipe["argv"], "--dangerously-skip-permissions"]}
    assert policy.canonical_sha256(recipe) != policy.canonical_sha256(changed)


def test_codex_recipe_disables_recursive_authority_and_preserves_auth():
    arguments = policy.policy_recipe("codex")["argv"]
    assert ["--ask-for-approval", "never"] == arguments[1:3]
    assert all(flag in arguments for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"))
    assert arguments[arguments.index("--sandbox") + 1] == "read-only"
    for override in ("orchestrator.mcp.enabled=false", "agents.enabled=false", "notify=[]"):
        assert override in arguments
    for feature in policy.CODEX_FEATURES_OFF:
        assert any(arguments[index : index + 2] == ["--disable", feature] for index in range(len(arguments)))
    assert "mcp_servers={}" not in arguments


def test_claude_recipe_is_restricted_and_does_not_disable_oauth():
    arguments = policy.policy_recipe("claude")["argv"]
    for flag in ("--restricted", "--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in arguments
    assert arguments[arguments.index("--tools") + 1] == "Read,Glob,Grep,WebSearch,WebFetch"
    assert json.loads(arguments[arguments.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert json.loads(arguments[arguments.index("--settings") + 1]) == {"disableAllHooks": True}
    assert arguments[arguments.index("--permission-prompts") + 1] == "none"
    assert "--bare" not in arguments


def test_no_inference_transport_refuses_user_and_turn_messages():
    probe = object.__new__(policy.ControlProbe)
    probe.protocol = "claude"
    probe.process = Mock()
    with pytest.raises(ValueError, match="no-inference"):
        probe.send({"type": "user", "message": {"content": "Do something"}})
    with pytest.raises(ValueError, match="no-inference"):
        probe.send({"id": 3, "method": "turn/start", "params": {}})
    with pytest.raises(ValueError, match="no-inference"):
        probe.send({"type": "user", "method": "initialize", "message": {"content": "Spoofed user query"}})
    probe.protocol = "codex"
    with pytest.raises(ValueError, match="no-inference"):
        probe.send({"type": "user", "method": "initialize", "message": {"content": "Spoofed user query"}})
    probe.process.stdin.write.assert_not_called()


def test_runtime_summaries_exclude_account_credentials_and_host_context():
    initialized = {
        "response": {
            "subtype": "success",
            "response": {
                "claude_code_version": "2.1.304",
                "account": {"token": "SECRET"},
                "agents": [{"name": "internal", "description": "PRIVATE"}],
                "user_output_styles_dir": "/private/host",
            },
        }
    }
    mcp = {"response": {"subtype": "success", "response": {"mcpServers": []}}}
    summary = policy.claude_runtime_summary(initialized, mcp)
    assert summary["mcp_servers_empty"]
    assert summary["agent_definition_count"] == 1
    rendered = json.dumps(summary)
    assert all(value not in rendered for value in ("SECRET", "PRIVATE", "/private/host", "token"))


def test_missing_effective_controls_are_never_assumed_safe():
    summary = policy.codex_runtime_summary({})
    assert summary["global_mcp_disabled"] is False
    assert summary["approval_never"] is False
    assert not any(summary["disabled_features"].values())


def test_diagnostics_export_only_known_categories():
    categories = policy.diagnostic_categories(b"Error loading configuration: permission denied /native/SECRET-token")
    assert categories == ["configuration_error", "permission_denied"]
    assert "SECRET" not in json.dumps(categories)


def test_structured_search_sources_exclude_prose_secrets_and_unrelated_hosts():
    summary = policy.structured_search_summary(
        {
            "type": "web_search",
            "query": "PRIVATE query omitted",
            "action": {"type": "open_page", "url": "https://modelcontextprotocol.io/docs/intro?token=SECRET#fragment"},
            "results": [
                {"url": "https://modelcontextprotocol.io/docs/intro", "title": "PRIVATE title"},
                {"url": "https://SECRET@modelcontextprotocol.io/credentials"},
                {"url": "https://unrelated.example/PRIVATE"},
            ],
        }
    )
    assert summary["action_type"] == "open_page"
    assert summary["source_urls"] == ["https://modelcontextprotocol.io/docs/intro"]
    assert all(value not in json.dumps(summary) for value in ("SECRET", "PRIVATE", "unrelated"))


def test_requested_native_open_url_alone_is_not_retrieved_source_evidence():
    summary = policy.structured_search_summary(
        {
            "type": "web_search",
            "action": {"type": "open_page", "url": "https://modelcontextprotocol.io/docs/intro"},
        }
    )
    assert summary["source_urls"] == []
    assert summary["requested_action_urls"] == ["https://modelcontextprotocol.io/docs/intro"]
    assert summary["results_present"] is False


def test_production_parser_rejects_native_error_item_even_after_completed_turn():
    events = [
        {"type": "item.completed", "item": {"type": "error", "message": "Code Mode is unavailable"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "CLINK_NATIVE_TEXT_READY"}},
        {"type": "turn.completed", "usage": {}},
    ]
    result = policy.production_parse_status("\n".join(json.dumps(event) for event in events), "")
    assert result["status"] == "rejected"
    assert result["code"] == "parse_error"
    clean = policy.production_parse_status("\n".join(json.dumps(event) for event in events[1:]), "")
    assert clean["status"] == "passed"


def test_bundled_catalog_is_pinned_and_uses_direct_tool_mode():
    root = Path(policy.__file__).resolve().parents[1]
    resource = root / policy.CODEX_CATALOG_PATH
    projected = json.loads(resource.read_text())
    assert policy.file_sha256(resource) == policy.CODEX_CATALOG_SHA256
    assert len(projected["models"]) == 12
    assert all(model["tool_mode"] == "direct" for model in projected["models"])


@pytest.mark.private_evidence
def test_bundled_catalog_projection_preserves_every_other_native_field(private_evidence_root):
    root = Path(policy.__file__).resolve().parents[1]
    original = json.loads(
        (private_evidence_root / "docs/verification/cli-policy/codex-0.166.0-model-catalog-original.json").read_text()
    )
    resource = root / policy.CODEX_CATALOG_PATH
    projected = json.loads(resource.read_text())
    assert policy.file_sha256(resource) == policy.CODEX_CATALOG_SHA256
    assert len(projected["models"]) == len(original["models"]) == 12
    assert [model["slug"] for model in projected["models"]] == [model["slug"] for model in original["models"]]
    for index, before in enumerate(original["models"]):
        after = projected["models"][index]
        assert after["tool_mode"] == "direct"
        assert {key: value for key, value in before.items() if key != "tool_mode"} == {
            key: value for key, value in after.items() if key != "tool_mode"
        }
    assert {key: value for key, value in original.items() if key != "models"} == {
        key: value for key, value in projected.items() if key != "models"
    }


def test_catalog_asset_mismatch_blocks_argument_rendering(monkeypatch):
    monkeypatch.setattr(policy, "file_sha256", lambda _: "changed")
    with pytest.raises(RuntimeError, match="catalog resource changed"):
        policy.render_codex_arguments(policy.policy_recipe("codex")["argv"])


def test_recipe_pins_catalog_content_with_relocation_independent_placeholder():
    recipe = policy.policy_recipe("codex")
    assert 'model_catalog_json="@CLINK_CODEX_MODEL_CATALOG@"' in recipe["argv"]
    assert recipe["resources"] == [{"path": policy.CODEX_CATALOG_PATH, "sha256": policy.CODEX_CATALOG_SHA256}]
    rendered = policy.render_codex_arguments(recipe["argv"])
    assert all(policy.CODEX_CATALOG_PLACEHOLDER not in argument for argument in rendered)


def test_normalized_observed_native_success_fixture_passes_exact_production_parser():
    root = Path(policy.__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/clink/codex-0.166.0-direct-success.jsonl"
    result = policy.production_parse_status(fixture.read_text(), "")
    assert result["status"] == "passed"


def test_duplicate_key_diagnostics_retain_no_identifier_values():
    value, duplicates = policy.diagnostic_json(b'{"id":"PRIVATE_A","type":"web_search","id":"PRIVATE_B"}')
    assert value["type"] == "web_search"
    assert duplicates == [{"field": "id", "count": 2, "all_values_strings": True, "values_equal": False}]
    assert "PRIVATE" not in json.dumps(duplicates)


def test_native_shapes_keep_status_and_types_without_response_text():
    result = policy.native_field_shape({"type": "agent_message", "text": "PRIVATE response", "status": None})
    assert result["fields"]["status"] == {"kind": "null"}
    assert result["fields"]["text"] == {"kind": "string", "length": 16}
    assert "PRIVATE" not in json.dumps(result)


def test_normalized_replay_preserves_duplicate_ids_and_masks_receipt_content():
    native = '{"type":"item.completed","item":{"id":"PRIVATE_A","type":"web_search","id":"PRIVATE_B","text":"PRIVATE_BODY","results":[{"type":"text_result","url":"https://modelcontextprotocol.io/docs?token=PRIVATE"}]}}'
    result = policy.normalized_native_jsonl(native)
    assert "PRIVATE" not in result
    value, duplicates = policy.diagnostic_json(result.encode())
    assert duplicates == [{"field": "id", "count": 2, "all_values_strings": True, "values_equal": False}]
    assert value["item"]["results"][0]["url"] == "https://modelcontextprotocol.io/docs"


def test_help_or_synthetic_success_cannot_unlock_live_execution():
    gates = {"help": "verified", "synthetic_profile": "verified"}
    with pytest.raises(RuntimeError, match="Live inference blocked"):
        policy.require_live_isolation(gates)
    assert policy.missing_gates(gates) == list(policy.ISOLATION_GATES + policy.LIVE_GATES)


def test_one_unknown_isolation_gate_prevents_live_execution():
    gates = dict.fromkeys(policy.ISOLATION_GATES, "verified")
    gates["managed_policy_cannot_weaken_controls"] = "unverified"
    with pytest.raises(RuntimeError, match="managed_policy"):
        policy.require_live_isolation(gates)


def test_workspace_digest_detects_no_write_regression(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("unchanged\n")
    before = policy.workspace_digest(tmp_path)
    source.write_text("changed\n")
    assert policy.workspace_digest(tmp_path) != before


@pytest.mark.parametrize("adapter", ["codex", "claude"])
def test_missing_cli_is_closed_before_any_live_request(monkeypatch, adapter):
    monkeypatch.setattr(policy.shutil, "which", lambda _: None)
    result = policy.collect(adapter, live=True)
    assert result["model_requests_submitted"] == 0
    assert result["admission"]["status"] == "policy_unverified"
    assert result["live_request"] == "blocked_before_inference"


def test_version_mismatch_never_starts_runtime_control_probe(monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_bytes(b"synthetic executable")
    monkeypatch.setattr(policy.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(policy, "bounded_version", Mock(return_value=(0, b"unsafe wrapper SECRET")))
    runtime = Mock()
    monkeypatch.setattr(policy, "synthetic_probe", runtime)
    result = policy.collect("codex", live=True)
    assert result["observed_version"] == "unrecognized_or_failed"
    assert "SECRET" not in json.dumps(result)
    runtime.assert_not_called()


def test_canary_observation_does_not_satisfy_unperformed_live_gates(monkeypatch, tmp_path):
    executable = tmp_path / "claude"
    executable.write_bytes(b"synthetic executable")
    monkeypatch.setattr(policy.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(policy, "bounded_version", Mock(return_value=(0, policy.EXPECTED_VERSIONS["claude"].encode())))
    runtime = Mock(return_value={"status": "observed", "canary_executed": False})
    monkeypatch.setattr(policy, "synthetic_probe", runtime)
    result = policy.collect("claude", live=True)
    assert result["live_request"] == "blocked_before_inference"
    assert all(value == "unverified" for value in result["gates"].values())
    assert result["authentication_outcome"] == "not_tested_no_model_request"
    runtime.assert_called_once()


def test_codex_live_preconditions_require_every_observed_isolation_control():
    observation = {
        "version_matches": True,
        "runtime_preflight": {
            "status": "observed",
            "canary_executed": False,
            "workspace_unchanged": True,
            "probe_process_group_stopped": True,
            "controls": {
                **dict.fromkeys(policy.CODEX_PREFLIGHT_CONTROLS, True),
                "disabled_features": dict.fromkeys(policy.CODEX_FEATURES_OFF, True),
            },
        },
    }
    assert policy.codex_live_preconditions(observation)
    observation["runtime_preflight"]["controls"]["disabled_features"]["shell_tool"] = False
    assert not policy.codex_live_preconditions(observation)


def test_changed_executable_is_rejected_before_live_spawn(monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_bytes(b"changed executable")
    spawn = Mock()
    monkeypatch.setattr(policy.subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="Executable changed"):
        policy.codex_live_challenge(str(executable), {}, "unmatched-fingerprint")
    spawn.assert_not_called()


def test_live_broken_stdin_still_terminates_and_reaps_process_group(monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_bytes(b"unchanged executable")
    process = Mock()
    process.stdin.write.side_effect = BrokenPipeError()
    monkeypatch.setattr(policy.subprocess, "Popen", Mock(return_value=process))
    selector = Mock()
    monkeypatch.setattr(policy.selectors, "DefaultSelector", Mock(return_value=selector))
    stop = Mock(return_value=True)
    monkeypatch.setattr(policy, "stop_process_group", stop)
    with pytest.raises(BrokenPipeError):
        policy.codex_live_challenge(str(executable), {}, policy.file_sha256(executable))
    stop.assert_called_once_with(process)
    selector.close.assert_called_once()
    process.stdout.close.assert_called_once()


def test_failed_native_sandbox_blocks_live_even_when_control_config_passes(monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_bytes(b"synthetic executable")
    monkeypatch.setattr(policy.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(policy, "bounded_version", Mock(return_value=(0, policy.EXPECTED_VERSIONS["codex"].encode())))
    monkeypatch.setattr(
        policy,
        "synthetic_probe",
        Mock(
            return_value={
                "status": "observed",
                "canary_executed": False,
                "workspace_unchanged": True,
                "probe_process_group_stopped": True,
                "controls": {
                    **dict.fromkeys(policy.CODEX_PREFLIGHT_CONTROLS, True),
                    "disabled_features": dict.fromkeys(policy.CODEX_FEATURES_OFF, True),
                },
            }
        ),
    )
    monkeypatch.setattr(
        policy,
        "codex_sandbox_probe",
        Mock(
            return_value={
                "exit_code": 0,
                "read_succeeded": True,
                "write_denied": False,
                "forbidden_file_absent": False,
                "workspace_unchanged": False,
            }
        ),
    )
    live = Mock()
    monkeypatch.setattr(policy, "codex_live_challenge", live)
    result = policy.collect("codex", live=True)
    assert result["live_request"] == "blocked_before_inference"
    assert result["model_requests_submitted"] == 0
    live.assert_not_called()


@pytest.mark.parametrize("kind", ["helper", "control"])
def test_selector_setup_failure_stops_child_group(monkeypatch, tmp_path, kind):
    process = Mock()
    monkeypatch.setattr(policy.subprocess, "Popen", Mock(return_value=process))
    selector = Mock()
    selector.register.side_effect = OSError("synthetic registration failure")
    monkeypatch.setattr(policy.selectors, "DefaultSelector", Mock(return_value=selector))
    stop = Mock(return_value=True)
    monkeypatch.setattr(policy, "stop_process_group", stop)
    with pytest.raises(OSError, match="synthetic registration failure"):
        if kind == "helper":
            policy.bounded_command(["synthetic-cli", "--version"], {})
        else:
            policy.ControlProbe(["synthetic-cli"], tmp_path, {}, "codex")
    stop.assert_called_once_with(process)
    selector.close.assert_called_once()
    process.stdout.close.assert_called_once()
    process.stderr.close.assert_called_once()
