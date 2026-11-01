"""Documented Antigravity fixture hooks and private child correlation, without a CLI."""

import json
import os
import shlex
import subprocess
import sys
from copy import deepcopy

import pytest

import clink.native
import clink.runner
from scripts import native_agy_fixture as fixture
from scripts import verify_native_adapters as verifier
from scripts.verify_native_cli import SENTINEL_NAME, SENTINEL_VALUE


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".clink-native-verification-fixture").write_text("synthetic-test-fixture\n")
    (tmp_path / "arithmetic.py").write_text(verifier.INITIAL_ARITHMETIC)
    fixture.create_agy_fixture(tmp_path, test_command=fixture.agy_fixture_command(tmp_path))
    return tmp_path


def hook(root, kind, event):
    completed = subprocess.run(
        [sys.executable, str(root / "agy_hook.py"), kind],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def child_stream(*children, root="PRIVATE_ROOT_CONVERSATION"):
    return "\n".join(
        [
            json.dumps({"event": "init", "conversation_id": root, "init": {}}),
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": root,
                        "subagent_info": {
                            "subagents": [
                                {
                                    "type_name": name,
                                    "conversation_id": identity,
                                    "initial_prompt": "PRIVATE_PROMPT",
                                    "log_uri": "PRIVATE_LOG_URI",
                                }
                                for name, identity in children
                            ]
                        },
                    },
                }
            ),
        ]
    )


def child_events(root, identity, *, read_error="", path=None):
    assert hook(root, "session_start", {"conversationId": identity}) == {}
    assert (
        hook(
            root,
            "post_tool",
            {
                "conversationId": identity,
                "toolCall": {"name": "read_file", "args": {"Path": str(path or root / "specialist.txt")}},
                "error": read_error,
            },
        )
        == {}
    )
    assert hook(root, "stop", {"conversationId": identity, "fullyIdle": True, "terminationReason": "model_stop"}) == {
        "decision": "stop"
    }


def test_builder_uses_supported_project_files_and_narrow_native_hooks(project):
    hooks = json.loads((project / ".agents/hooks.json").read_text())["clink-native-fixture"]
    assert set(hooks) == {"SessionStart", "PreToolUse", "PostToolUse", "Stop"}
    assert hooks["PreToolUse"][0]["matcher"] == fixture.FIXTURE_TOOLS
    assert hooks["PostToolUse"][0]["matcher"] == "^(view_file|read_file)$"
    agent = (project / ".agents/agents/fixture-specialist.md").read_text()
    assert "  - read_file\n" in agent
    assert "hooks:\n  - ../hooks.json\n" in agent
    assert (project / ".agents/agents" / "../hooks.json").resolve() == project / ".agents/hooks.json"
    assert "model: inherit\n" in agent
    assert "subagent: true\nmainAgent: false\n" in agent
    assert "run_command" not in agent
    config = json.loads((project / ".agents/mcp_config.json").read_text())
    server = config["mcpServers"]["native_fixture"]
    assert server["command"] == sys.executable
    assert server["args"] == [str(project / "agy_mcp_server.py")]
    assert "env" not in server
    assert not (project / ".agents/settings.json").exists()


def test_capability_prompt_binds_one_exact_native_child_request(project):
    prompt = fixture.agy_capability_prompt(project)
    encoded = prompt.split("Use invoke_subagent with exactly this JSON argument object: ", 1)[1].split(". Wait for", 1)[
        0
    ]
    arguments = json.loads(encoded)
    assert arguments == {
        "TypeName": "fixture-specialist",
        "Role": "synthetic file reader",
        "Prompt": "Use read_file with exactly "
        + json.dumps({"Path": str(project / "specialist.txt")})
        + ". Return its exact token. Do not edit files, run commands, or delegate.",
    }
    assert "mcp_native_fixture_canary with exactly {}" in prompt
    assert "spawn_subagent" not in prompt
    assert "do not override its model" in prompt
    write_args = json.loads(prompt.split("JSON argument object: ", 1)[1].split(". Call mcp_native_fixture", 1)[0])
    assert write_args == {
        "Path": str(project / "arithmetic.py"),
        "Content": fixture.EXPECTED_ARITHMETIC,
        "Overwrite": True,
    }


@pytest.mark.parametrize("suffix,kind", [("", "test_command"), (" cancel", "cancel_command")])
def test_exact_visible_foreground_command_contract_emits_approval(project, suffix, kind):
    args = {
        "CommandLine": fixture.agy_fixture_command(project) + suffix,
        "Cwd": str(project),
        "WaitMsBeforeAsync": fixture.COMMAND_WAIT_MS,
    }
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": args}}) == {
        "decision": "auto_approve"
    }
    assert fixture.agy_fixture_observations(project)["agy_fixture_" + kind + "_approval_emitted"] is True
    instructions = fixture.agy_command_prompt(project)
    assert "10000" in instructions
    assert "exactly three arguments" in instructions
    assert "toolSummary" in instructions
    assert "Cwd" not in instructions
    assert "RunPersistent" not in instructions
    assert "IsDaemon" not in instructions


def modern_command(root, *, suffix=""):
    return {
        "CommandLine": fixture.agy_fixture_command(root) + suffix,
        "toolSummary": "Run the synthetic fixture operation.",
        "WaitMsBeforeAsync": 10000,
    }


@pytest.mark.parametrize(
    "optional", [{}, {"IsDaemon": False}, {"BypassSandbox": False}, {"IsDaemon": False, "BypassSandbox": False}]
)
@pytest.mark.parametrize("suffix,kind", [("", "test_command"), (" cancel", "cancel_command")])
def test_modern_command_requires_the_exact_single_workspace_and_safe_options(project, optional, suffix, kind):
    args = {**modern_command(project, suffix=suffix), **optional}
    assert hook(
        project, "pre_tool", {"workspacePaths": [str(project)], "toolCall": {"name": "run_command", "args": args}}
    ) == {"decision": "auto_approve"}
    assert fixture.agy_fixture_observations(project)["agy_fixture_" + kind + "_approval_emitted"] is True
    assert shlex.split(fixture.agy_fixture_command(project)) == [sys.executable, str(project / "fixture.py")]


@pytest.mark.parametrize("case", ["missing", "empty", "outside", "multiple", "textual", "trailing_separator"])
def test_modern_command_does_not_guess_the_native_primary_workspace(project, case):
    event = {"toolCall": {"name": "run_command", "args": modern_command(project)}}
    workspaces = {
        "empty": [],
        "outside": [str(project.parent)],
        "multiple": [str(project), str(project.parent)],
        "textual": str(project),
        "trailing_separator": [str(project) + "/"],
    }
    if case != "missing":
        event["workspacePaths"] = workspaces[case]
    assert hook(project, "pre_tool", event) == {"decision": "allow"}
    assert fixture.agy_fixture_observations(project)["agy_workspace_paths_match"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"toolSummary": None},
        {"toolSummary": "x" * 4097},
        {"WaitMsBeforeAsync": True},
        {"WaitMsBeforeAsync": 500},
        {"IsDaemon": True},
        {"IsDaemon": 0},
        {"BypassSandbox": True},
        {"BypassSandbox": 0},
        {"NotificationTimeoutSeconds": 0},
        {"Cwd": "@root@"},
        {"RunPersistent": False},
        {"RequestedTerminalID": ""},
        {"PRIVATE_UNKNOWN_KEY": "PRIVATE_VALUE"},
    ],
)
def test_modern_command_rejects_unapproved_control_fields_and_types(project, change):
    args = modern_command(project)
    args.update({key: str(project) if value == "@root@" else value for key, value in change.items()})
    assert hook(
        project, "pre_tool", {"workspacePaths": [str(project)], "toolCall": {"name": "run_command", "args": args}}
    ) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_fixture_test_command_approval_emitted"] is False
    assert observed["agy_workspace_paths_match"] is True
    assert "PRIVATE_VALUE" not in (project / "agy-hooks.jsonl").read_text()


def test_absolute_agy_fixture_refuses_an_unexpected_execution_directory(tmp_path):
    root = tmp_path / "fixture"
    verifier.create_fixture(root, "agy")
    (root / "arithmetic.py").write_text(fixture.EXPECTED_ARITHMETIC)
    outside = tmp_path / "outside"
    outside.mkdir()
    command = shlex.split(fixture.agy_fixture_command(root))
    misplaced = subprocess.run(command, cwd=outside, capture_output=True, text=True)
    assert misplaced.returncode != 0
    assert "requires its exact project working directory" in misplaced.stderr
    assert not (root / "fixture-checks.jsonl").exists()
    assert not (outside / "fixture-checks.jsonl").exists()
    assert subprocess.run(command, cwd=root, capture_output=True).returncode == 0
    grok = tmp_path / "grok"
    verifier.create_fixture(grok, "grok")
    assert (grok / "fixture.py").read_text() == verifier.FIXTURE_PROGRAM


def test_builder_refuses_a_relative_or_different_fixture_command(tmp_path):
    (tmp_path / ".clink-native-verification-fixture").write_text("synthetic\n")
    with pytest.raises(ValueError, match="exact absolute"):
        fixture.create_agy_fixture(tmp_path, test_command="python fixture.py")


@pytest.mark.parametrize(
    "change",
    [
        {"CommandLine": "cat /unrelated/file"},
        {"CommandLine": "@fixture@; touch unrelated"},
        {"CommandLine": "@fixture@ &"},
        {"Cwd": "@outside@"},
        {"Cwd": "@relative@"},
        {"WaitMsBeforeAsync": True},
        {"WaitMsBeforeAsync": 0},
        {"WaitMsBeforeAsync": 10001},
        {"RunPersistent": True},
        {"RunPersistent": False},
        {"IsDaemon": True},
        {"BypassSandbox": True},
        {"RequestedTerminalID": "existing-terminal"},
        {"NotificationTimeoutSeconds": 10},
        {"Background": True},
    ],
)
def test_broader_command_arguments_delegate_to_native_policy(project, change):
    args = {"CommandLine": fixture.agy_fixture_command(project), "Cwd": str(project), "WaitMsBeforeAsync": 10000}
    for key, value in change.items():
        args[key] = (
            value.replace("@fixture@", fixture.agy_fixture_command(project))
            .replace("@outside@", str(project.parent))
            .replace("@relative@", ".")
            if isinstance(value, str)
            else value
        )
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": args}}) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_fixture_test_command_approval_emitted"] is False
    assert observed["agy_fixture_cancel_command_approval_emitted"] is False


def test_unmatched_command_records_fixed_predicates_without_native_values(project):
    args = {"CommandLine": fixture.agy_fixture_command(project), "Cwd": str(project), "WaitMsBeforeAsync": 1000}
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": args}}) == {"decision": "allow"}
    row = json.loads((project / "agy-hooks.jsonl").read_text())
    assert row["call_name_category"] == "run_command"
    for flag in (
        "args_object",
        "command_matches",
        "command_argv_matches",
        "cwd_matches",
        "wait_ms_integer",
        "expected_keys_present",
        "command_keys_exact",
    ):
        assert row[flag] is True
    assert row["wait_ms_matches"] is False
    assert row["unexpected_keys_present"] is False
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_unmatched_pre_tool_observed"] is True
    assert observed["agy_call_is_run_command"] is True
    assert observed["agy_wait_ms_matches"] is False
    encoded = (project / "agy-hooks.jsonl").read_text() + json.dumps(observed)
    assert str(project) not in encoded
    assert fixture.agy_fixture_command(project) not in encoded
    assert "CommandLine" not in encoded
    assert all(type(value) is bool for value in observed.values())


@pytest.mark.parametrize(
    "call",
    [
        None,
        {"name": "PRIVATE_TOOL_NAME", "args": {"PRIVATE_CUSTOM_KEY": "PRIVATE_ARGUMENT_VALUE"}},
        {"name": "run_command"},
        {"name": "run_command", "args": "PRIVATE_RAW_ARGUMENTS"},
        {"name": "run_command", "args": None},
        {"name": ["PRIVATE_TOOL_NAME"], "args": []},
    ],
)
def test_malformed_or_unknown_calls_never_retain_native_names_or_argument_values(project, call):
    assert hook(project, "pre_tool", {"toolCall": call}) == {"decision": "allow"}
    row = json.loads((project / "agy-hooks.jsonl").read_text())
    assert row["call_name_category"] in fixture.CALL_CATEGORIES
    assert all(type(row[flag]) is bool for flag in fixture.DIAGNOSTIC_FLAGS)
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_unmatched_pre_tool_observed"] is True
    encoded = (project / "agy-hooks.jsonl").read_text() + json.dumps(observed)
    for private in ("PRIVATE_TOOL_NAME", "PRIVATE_CUSTOM_KEY", "PRIVATE_ARGUMENT_VALUE", "PRIVATE_RAW_ARGUMENTS"):
        assert private not in encoded


def test_wrong_command_types_have_presence_without_text_or_integer_matches(project):
    args = {"CommandLine": {"PRIVATE_KEY": "PRIVATE_VALUE"}, "Cwd": None, "WaitMsBeforeAsync": True}
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": args}}) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    for prefix in ("command", "cwd", "wait_ms"):
        assert observed["agy_" + prefix + "_key_present"] is True
        assert observed["agy_" + prefix + "_matches"] is False
    assert observed["agy_command_textual"] is False
    assert observed["agy_cwd_textual"] is False
    assert observed["agy_wait_ms_integer"] is False
    assert "PRIVATE" not in (project / "agy-hooks.jsonl").read_text()


@pytest.mark.parametrize("neutral", [True, False])
def test_extra_argument_diagnosis_never_changes_the_exact_approval_predicate(project, neutral):
    args = {"CommandLine": fixture.agy_fixture_command(project), "Cwd": str(project), "WaitMsBeforeAsync": 10000}
    args.update(
        {"RunPersistent": False, "IsDaemon": False, "RequestedTerminalID": ""}
        if neutral
        else {"PRIVATE_UNKNOWN_KEY": "PRIVATE_ARGUMENT_VALUE"}
    )
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": args}}) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_command_matches"] is observed["agy_cwd_matches"] is observed["agy_wait_ms_matches"] is True
    assert observed["agy_unexpected_keys_present"] is True
    assert observed["agy_neutral_extra_keys_only"] is neutral
    assert observed["agy_command_keys_exact"] is False
    assert observed["agy_fixture_test_command_approval_emitted"] is False
    encoded = (project / "agy-hooks.jsonl").read_text() + json.dumps(observed)
    for private in ("PRIVATE_UNKNOWN_KEY", "PRIVATE_ARGUMENT_VALUE", "RunPersistent", "RequestedTerminalID"):
        assert private not in encoded


@pytest.mark.parametrize("case,argv_matches", [("whitespace", True), ("invalid_shell", False), ("different", False)])
def test_shell_argv_equivalence_is_diagnostic_only(project, case, argv_matches):
    command = {
        "whitespace": "  " + fixture.agy_fixture_command(project) + "   ",
        "invalid_shell": fixture.agy_fixture_command(project) + " '",
        "different": fixture.agy_fixture_command(project) + "; echo PRIVATE_ARGUMENT_VALUE",
    }[case]
    args = {"CommandLine": command, "Cwd": str(project), "WaitMsBeforeAsync": 10000}
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": args}}) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_command_matches"] is False
    assert observed["agy_command_argv_matches"] is argv_matches
    assert observed["agy_fixture_test_command_approval_emitted"] is False
    assert "PRIVATE_ARGUMENT_VALUE" not in (project / "agy-hooks.jsonl").read_text()


def test_approved_call_facts_cannot_mask_a_later_unmatched_call(project):
    approved = {"CommandLine": fixture.agy_fixture_command(project), "Cwd": str(project), "WaitMsBeforeAsync": 10000}
    rejected = {"CommandLine": "PRIVATE_COMMAND", "Cwd": "PRIVATE_CWD", "WaitMsBeforeAsync": 1}
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": approved}}) == {
        "decision": "auto_approve"
    }
    assert hook(project, "pre_tool", {"toolCall": {"name": "run_command", "args": rejected}}) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_command_matches"] is observed["agy_cwd_matches"] is observed["agy_wait_ms_matches"] is False
    assert observed["agy_multiple_unmatched_pre_tools"] is False
    hook(project, "pre_tool", {"toolCall": {"name": "PRIVATE_TOOL", "args": {}}})
    assert fixture.agy_fixture_observations(project)["agy_multiple_unmatched_pre_tools"] is True


def test_missing_diagnostic_flags_remain_false_and_boolean_receipts_preserve_budget(project, monkeypatch):
    (project / "agy-hooks.jsonl").write_text(
        json.dumps({"event": "pre_tool", "payload_valid": True, "fixture_predicate_unmatched": True}) + "\n"
    )
    observed = fixture.agy_fixture_observations(project)
    assert all(observed["agy_" + flag] is False for flag in fixture.DIAGNOSTIC_FLAGS)
    assert observed["agy_unmatched_pre_tool_observed"] is True
    monkeypatch.setattr(verifier, "PROJECT", project)
    directory = project / "docs/verification"
    directory.mkdir(parents=True)
    prior = directory / "prior.json"
    entry = {"ordinal": 1, "check": "edit", "observations": observed, "passed": False}
    prior.write_text(
        json.dumps(
            {
                "scope": verifier.SCOPE,
                "adapter": "agy",
                "submission_ledger": [entry],
                "submission_allowance_total": 5,
                "submissions_consumed_total": 1,
            }
        )
    )
    before = prior.read_bytes()
    ledger, allowance, _ = verifier.load_history(prior, "agy")
    assert ledger == [entry]
    assert allowance == 5
    assert prior.read_bytes() == before


def arithmetic_write(root):
    return {
        "TargetFile": str(root / "arithmetic.py"),
        "Overwrite": True,
        "CodeContent": "def add(a, b):\n    return a + b\n",
        "Description": "Correct the synthetic addition function.",
    }


def modern_write(root):
    return {"Path": str(root / "arithmetic.py"), "Content": fixture.EXPECTED_ARITHMETIC, "Overwrite": True}


def test_source_known_modern_whole_file_write_is_exact_and_protects_permission_probe(project):
    args = modern_write(project)
    assert hook(project, "pre_tool", {"toolCall": {"name": "write_file", "args": args}}) == {"decision": "auto_approve"}
    args["Path"] = str(project / "permission-needed.txt")
    assert hook(project, "pre_tool", {"toolCall": {"name": "write_file", "args": args}}) == {
        "decision": "force_ask",
        "reason": "Synthetic approval probe",
    }
    assert fixture.agy_fixture_observations(project)["permission_hook_observed"] is True
    assert not (project / "permission-needed.txt").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"Path": "arithmetic.py"},
        {"Path": "@outside@"},
        {"Content": "PRIVATE_DIFFERENT_CODE"},
        {"Overwrite": False},
        {"Overwrite": 1},
        {"Append": False},
        {"Append": True},
        {"ArtifactMetadata": {}},
        {"Description": "unapproved extra field"},
    ],
)
def test_modern_write_rejects_different_targets_content_and_additional_keys(project, change):
    args = modern_write(project)
    args.update(
        {key: str(project.parent / "arithmetic.py") if value == "@outside@" else value for key, value in change.items()}
    )
    assert hook(project, "pre_tool", {"toolCall": {"name": "write_file", "args": args}}) == {"decision": "allow"}
    assert fixture.agy_fixture_observations(project)["agy_fixture_arithmetic_edit_approval_emitted"] is False
    assert "PRIVATE_DIFFERENT_CODE" not in (project / "agy-hooks.jsonl").read_text()


def test_only_the_exact_native_arithmetic_write_contract_is_approved(project):
    args = arithmetic_write(project)
    assert hook(project, "pre_tool", {"toolCall": {"name": "write_to_file", "args": args}}) == {
        "decision": "auto_approve"
    }
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_fixture_arithmetic_edit_approval_emitted"] is True
    log = (project / "agy-hooks.jsonl").read_text()
    assert "return a + b" not in log
    assert str(project) not in log


def test_unmatched_write_diagnoses_path_code_and_overwrite_without_retaining_content(project):
    args = arithmetic_write(project)
    args.update(CodeContent="PRIVATE_CODE_CONTENT", Overwrite=False)
    assert hook(project, "pre_tool", {"toolCall": {"name": "write_to_file", "args": args}}) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_call_is_write_to_file"] is True
    assert observed["agy_write_path_matches"] is observed["agy_write_target_safe"] is True
    assert observed["agy_write_code_textual"] is observed["agy_write_overwrite_boolean"] is True
    assert observed["agy_write_code_matches"] is observed["agy_write_overwrite_matches"] is False
    assert observed["agy_write_keys_exact"] is True
    assert "PRIVATE_CODE_CONTENT" not in (project / "agy-hooks.jsonl").read_text()


def test_unmatched_child_request_diagnoses_only_shape_and_exact_match(project):
    args = fixture._subagent_request(project)
    args["Prompt"] = "PRIVATE_CHILD_PROMPT"
    assert hook(project, "pre_tool", {"toolCall": {"name": "invoke_subagent", "args": args}}) == {"decision": "allow"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_call_is_invoke_subagent"] is True
    assert observed["agy_args_object"] is observed["agy_expected_keys_present"] is True
    assert observed["agy_subagent_request_matches"] is False
    assert "PRIVATE_CHILD_PROMPT" not in (project / "agy-hooks.jsonl").read_text()


@pytest.mark.parametrize(
    "change",
    [
        {"TargetFile": "@outside@"},
        {"TargetFile": "arithmetic.py"},
        {"Overwrite": False},
        {"Overwrite": 1},
        {"CodeContent": 123},
        {"CodeContent": "def add(a, b):\n    return a - b\n"},
        {"CodeContent": "x" * 4097},
        {"Description": None},
        {"Append": True},
        {"IsArtifact": False},
        {"ArtifactMetadata": {}},
        {"Background": True},
    ],
)
def test_arithmetic_write_outside_or_additional_arguments_delegate_to_native_policy(project, change):
    args = arithmetic_write(project)
    args.update(change)
    if args["TargetFile"] == "@outside@":
        args["TargetFile"] = str(project.parent / "arithmetic.py")
    assert hook(project, "pre_tool", {"toolCall": {"name": "write_to_file", "args": args}}) == {"decision": "allow"}
    assert fixture.agy_fixture_observations(project)["agy_fixture_arithmetic_edit_approval_emitted"] is False


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("modern", [False, True])
def test_arithmetic_approval_refuses_external_aliases(project, kind, modern):
    outside = project.parent / (project.name + "-outside.py")
    outside.write_text("external synthetic sentinel\n")
    target = project / "arithmetic.py"
    target.unlink()
    if kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)
    assert hook(
        project,
        "pre_tool",
        {
            "toolCall": {
                "name": "write_file" if modern else "write_to_file",
                "args": modern_write(project) if modern else arithmetic_write(project),
            }
        },
    ) == {"decision": "allow"}
    assert outside.read_text() == "external synthetic sentinel\n"


def test_protected_target_force_ask_precedes_fixture_write_approval(project):
    args = arithmetic_write(project)
    args["TargetFile"] = str(project / "permission-needed.txt")
    assert hook(project, "pre_tool", {"toolCall": {"name": "write_to_file", "args": args}}) == {
        "decision": "force_ask",
        "reason": "Synthetic approval probe",
    }
    observations = fixture.agy_fixture_observations(project)
    assert observations["permission_hook_observed"] is True
    assert observations["agy_fixture_arithmetic_edit_approval_emitted"] is False


def arithmetic_replacement(root, *, multi=False):
    chunk = {
        "TargetContent": "    return a - b",
        "ReplacementContent": "    return a + b",
        "AllowMultiple": False,
        "StartLine": 2,
        "EndLine": 2,
    }
    shared = {
        "TargetFile": str(root / "arithmetic.py"),
        "Instruction": "Correct the synthetic addition function.",
        "Description": "Replace subtraction with addition.",
    }
    return {**shared, "ReplacementChunks": [chunk]} if multi else {**shared, **chunk}


@pytest.mark.parametrize("multi", [False, True])
def test_ungranted_arithmetic_replacement_variants_delegate_to_native_policy(project, multi):
    args = arithmetic_replacement(project, multi=multi)
    name = "multi_replace_file_content" if multi else "replace_file_content"
    assert hook(project, "pre_tool", {"toolCall": {"name": name, "args": args}}) == {"decision": "allow"}
    assert fixture.agy_fixture_observations(project)["agy_fixture_arithmetic_edit_approval_emitted"] is False


@pytest.mark.parametrize("kind", ["mcp_canary", "subagent"])
def test_exact_fixture_capabilities_emit_only_fixed_approval_metadata(project, kind):
    args = {} if kind == "mcp_canary" else fixture._subagent_request(project)
    name = "mcp_native_fixture_canary" if kind == "mcp_canary" else "invoke_subagent"
    event = {"conversationId": "PRIVATE_CONVERSATION", "toolCall": {"name": name, "args": args}}
    assert hook(project, "pre_tool", event) == {"decision": "auto_approve"}
    observations = fixture.agy_fixture_observations(project)
    assert observations["agy_fixture_" + kind + "_approval_emitted"] is True
    log = (project / "agy-hooks.jsonl").read_text()
    assert str(project) not in log
    assert "PRIVATE_CONVERSATION" not in log
    assert "Use read_file" not in log
    assert "Subagents" not in log
    assert "CommandLine" not in log


@pytest.mark.parametrize(
    "case",
    [
        "extra_mcp_arg",
        "other_mcp",
        "extra_child",
        "workspace",
        "model",
        "fork",
        "count",
        "workspace_uri",
        "wrong_prompt",
    ],
)
def test_unmatched_capabilities_delegate_to_native_policy(project, case):
    name, args = "invoke_subagent", fixture._subagent_request(project)
    if case == "extra_mcp_arg":
        name, args = "mcp_native_fixture_canary", {"extra": "PRIVATE_ARGUMENT"}
    elif case == "other_mcp":
        name, args = "mcp_unrelated_canary", {}
    elif case == "extra_child":
        args = {"Subagents": [args, deepcopy(args)]}
    elif case == "workspace":
        args["Workspace"] = str(project.parent)
    elif case == "model":
        args["Model"] = "arbitrary-model"
    elif case == "fork":
        args["Fork"] = False
    elif case == "count":
        args["Count"] = 1
    elif case == "workspace_uri":
        args["WorkspaceUri"] = str(project)
    else:
        args["Prompt"] = "Read an unrelated file"
    assert hook(project, "pre_tool", {"toolCall": {"name": name, "args": args}}) == {"decision": "allow"}
    observations = fixture.agy_fixture_observations(project)
    assert not any(observations["agy_fixture_" + kind + "_approval_emitted"] for kind in fixture.APPROVAL_KINDS)


def test_exact_classic_single_child_request_retains_compatibility(project):
    assert hook(
        project,
        "pre_tool",
        {"toolCall": {"name": "invoke_subagent", "args": fixture._classic_subagent_request(project)}},
    ) == {"decision": "auto_approve"}


def test_builder_refuses_replacing_existing_configuration(project):
    before = (project / ".agents/hooks.json").read_bytes()
    with pytest.raises(ValueError, match="existing files"):
        fixture.create_agy_fixture(project, test_command=fixture.agy_fixture_command(project))
    assert (project / ".agents/hooks.json").read_bytes() == before


def test_builder_requires_disposable_marker(tmp_path):
    with pytest.raises(ValueError, match="marker"):
        fixture.create_agy_fixture(tmp_path, test_command=fixture.agy_fixture_command(tmp_path))


def test_permission_force_ask_is_exact_target_and_preserves_other_native_policy(project):
    exact = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": str(project / "permission-needed.txt")}}}
    assert hook(project, "pre_tool", exact) == {"decision": "force_ask", "reason": "Synthetic approval probe"}
    observed = fixture.agy_fixture_observations(project)
    assert observed["permission_hook_observed"] is True
    assert observed["protected_file_untouched"] is True
    for other in (
        "permission-needed.txt",
        str(project / "arithmetic.py"),
        str(project.parent / "permission-needed.txt"),
    ):
        assert hook(project, "pre_tool", {"toolCall": {"args": {"TargetFile": other}}}) == {"decision": "allow"}
    assert not (project / "permission-needed.txt").exists()


def test_absent_or_malformed_hook_does_not_establish_permission_refusal(project):
    observed = fixture.agy_fixture_observations(project)
    assert observed["permission_hook_observed"] is False
    assert observed["protected_file_untouched"] is True
    assert hook(project, "pre_tool", {"toolCall": {"args": None}}) == {"decision": "allow"}
    assert fixture.agy_fixture_observations(project)["permission_hook_observed"] is False
    (project / "agy-permission-hook-observed.json").write_text('{"target_matches":true,"force_ask_emitted":"true"}')
    assert fixture.agy_fixture_observations(project)["permission_hook_observed"] is False


def test_session_start_marker_proves_project_hook_loaded_without_raw_fields(project):
    event = {
        "conversationId": "PRIVATE_NATIVE_SESSION",
        "workspacePaths": ["PRIVATE_WORKSPACE"],
        "modelName": "PRIVATE_MODEL",
    }
    assert hook(project, "session_start", event) == {}
    observed = fixture.agy_fixture_observations(project)
    assert observed["agy_session_start_observed"] is True
    log = (project / "agy-hooks.jsonl").read_text()
    for private in ("PRIVATE_NATIVE_SESSION", "PRIVATE_WORKSPACE", "PRIVATE_MODEL"):
        assert private not in log
    assert "conversation_fingerprint" in log


def test_named_child_successful_exact_read_is_correlated_without_exporting_identity(project):
    identity = "PRIVATE_CHILD_CONVERSATION"
    child_events(project, identity)
    fixture.observe_agy_stream(project, child_stream(("fixture-specialist", identity)))
    observed = fixture.agy_fixture_observations(project)
    assert observed["fixture_subagent_started"] is True
    assert observed["fixture_subagent_stopped"] is True
    assert observed["fixture_subagent_read_observed"] is True
    assert observed["fixture_subagent_token_return_verified"] is False
    encoded = json.dumps(observed) + (project / "agy-subagent-observed.json").read_text()
    for private in (
        identity,
        "PRIVATE_ROOT_CONVERSATION",
        "PRIVATE_PROMPT",
        "PRIVATE_LOG_URI",
        "conversation_fingerprint",
    ):
        assert private not in encoded


@pytest.mark.parametrize("extra", [{"ByteOffset": 1}, {"StartLine": 2}, {"EndLine": 1}])
def test_child_read_with_unverified_ranges_or_offsets_cannot_establish_canary_read(project, extra):
    identity = "PRIVATE_CHILD_CONVERSATION"
    hook(project, "session_start", {"conversationId": identity})
    hook(
        project,
        "post_tool",
        {
            "conversationId": identity,
            "toolCall": {"name": "read_file", "args": {"Path": str(project / "specialist.txt"), **extra}},
        },
    )
    hook(project, "stop", {"conversationId": identity, "fullyIdle": True, "terminationReason": "model_stop"})
    fixture.observe_agy_stream(project, child_stream(("fixture-specialist", identity)))
    assert fixture.agy_fixture_observations(project)["fixture_subagent_read_observed"] is False


@pytest.mark.parametrize("case", ["wrong_child", "root_identity", "wrong_file", "read_error", "missing_stop"])
def test_child_read_proof_rejects_unrelated_or_incomplete_observations(project, case):
    identity = "PRIVATE_CHILD_CONVERSATION"
    if case == "root_identity":
        identity = "PRIVATE_ROOT_CONVERSATION"
    child_events(
        project,
        identity,
        read_error="native read failed" if case == "read_error" else "",
        path=project / "arithmetic.py" if case == "wrong_file" else None,
    )
    if case == "missing_stop":
        path = project / "agy-hooks.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        path.write_text("\n".join(json.dumps(row) for row in rows if row["event"] != "stop") + "\n")
    fixture.observe_agy_stream(
        project, child_stream(("unrelated" if case == "wrong_child" else "fixture-specialist", identity))
    )
    assert fixture.agy_fixture_observations(project)["fixture_subagent_read_observed"] is False


def test_different_children_cannot_supply_combined_lifecycle_proof(project):
    first, second = "PRIVATE_CHILD_A", "PRIVATE_CHILD_B"
    hook(project, "session_start", {"conversationId": first})
    hook(
        project,
        "post_tool",
        {
            "conversationId": first,
            "toolCall": {"name": "view_file", "args": {"AbsolutePath": str(project / "specialist.txt")}},
        },
    )
    hook(project, "stop", {"conversationId": second, "fullyIdle": True, "terminationReason": "model_stop"})
    fixture.observe_agy_stream(project, child_stream(("fixture-specialist", first), ("fixture-specialist", second)))
    assert fixture.agy_fixture_observations(project)["fixture_subagent_read_observed"] is False


def test_new_submission_does_not_reuse_a_previous_child_correlation(project):
    identity = "PRIVATE_CHILD_CONVERSATION"
    child_events(project, identity)
    fixture.observe_agy_stream(project, child_stream(("fixture-specialist", identity)))
    assert fixture.agy_fixture_observations(project)["fixture_subagent_read_observed"] is True
    fixture.observe_agy_stream(project, child_stream())
    assert fixture.agy_fixture_observations(project)["fixture_subagent_read_observed"] is False


def test_native_mcp_canary_observes_only_inherited_synthetic_environment(project):
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "canary"}})
    for present in (False, True):
        completed = subprocess.run(
            [sys.executable, str(project / "agy_mcp_server.py")],
            input=request + "\n",
            text=True,
            capture_output=True,
            check=True,
            env={SENTINEL_NAME: SENTINEL_VALUE} if present else {},
        )
        assert json.loads(completed.stdout)["result"]["isError"] is not present
        assert json.loads((project / "mcp-called.json").read_text())["inherited_sentinel_matches"] is present


def test_hook_observation_log_is_bounded(project):
    (project / "agy-hooks.jsonl").write_bytes(b"x" * fixture.MAX_OBSERVATION_BYTES)
    assert hook(project, "session_start", {"conversationId": "PRIVATE"}) == {}
    assert (project / "agy-hooks.jsonl").stat().st_size == fixture.MAX_OBSERVATION_BYTES
    assert fixture.agy_fixture_observations(project)["hook_observation_limit_reached"] is True


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_stream_observer_never_truncates_linked_external_files(project, link_kind):
    outside = project.parent / (project.name + "-external")
    outside.write_text("external synthetic sentinel\n")
    artifact = project / "agy-subagent-observed.json"
    if link_kind == "symlink":
        artifact.symlink_to(outside)
    else:
        os.link(outside, artifact)
    with pytest.raises((OSError, ValueError)):
        fixture.observe_agy_stream(project, child_stream())
    assert outside.read_text() == "external synthetic sentinel\n"
    assert fixture.agy_fixture_observations(project)["fixture_subagent_read_observed"] is False


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("name", ["agy-permission-hook-observed.json", "agy-hooks.jsonl", "agy-hook-log-limit"])
def test_hook_writers_never_modify_linked_external_files(project, link_kind, name):
    outside = project.parent / (project.name + "-external")
    outside.write_text("external synthetic sentinel\n")
    artifact = project / name
    if link_kind == "symlink":
        artifact.symlink_to(outside)
    else:
        os.link(outside, artifact)
    if name == "agy-hook-log-limit":
        (project / "agy-hooks.jsonl").write_bytes(b" " * fixture.MAX_OBSERVATION_BYTES)
    event = {"toolCall": {"args": {"TargetFile": str(project / "permission-needed.txt")}}}
    completed = subprocess.run(
        [sys.executable, str(project / "agy_hook.py"), "pre_tool"],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert completed.returncode != 0
    assert outside.read_text() == "external synthetic sentinel\n"


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize(
    "name, forged",
    [
        ("agy-permission-hook-observed.json", {"target_matches": True, "force_ask_emitted": True}),
        ("agy-hooks.jsonl", {"event": "session_start", "payload_valid": True}),
        (
            "agy-subagent-observed.json",
            {
                "child_session_start_observed": True,
                "child_loop_stopped_observed": True,
                "child_exact_canary_read_observed": True,
            },
        ),
    ],
)
def test_observation_readers_ignore_external_aliases(project, link_kind, name, forged):
    outside = project.parent / (project.name + "-external")
    encoded = json.dumps(forged) + "\n"
    outside.write_text(encoded)
    artifact = project / name
    if link_kind == "symlink":
        artifact.symlink_to(outside)
    else:
        os.link(outside, artifact)
    observed = fixture.agy_fixture_observations(project)
    assert observed["permission_hook_observed"] is False
    assert observed["agy_session_start_observed"] is False
    assert observed["fixture_subagent_read_observed"] is False
    assert outside.read_text() == encoded


def test_observation_fifos_are_nonblocking_and_cannot_establish_proof(project):
    os.mkfifo(project / "agy-hooks.jsonl")
    os.mkfifo(project / "agy-subagent-observed.json")
    assert fixture.agy_fixture_observations(project)["scope_hook_observed"] is False
    with pytest.raises(OSError):
        fixture.observe_agy_stream(project, child_stream())


def test_no_host_configuration_or_environment_changes(project):
    before = dict(os.environ)
    hook(project, "session_start", {"conversationId": "PRIVATE"})
    fixture.agy_fixture_observations(project)
    assert dict(os.environ) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_hook_invoked", [True, False])
async def test_fake_agy_lifecycle_uses_native_runtime_and_requires_target_hook(
    tmp_path, monkeypatch, permission_hook_invoked
):
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    monkeypatch.setattr(clink.native, "__file__", str(tmp_path / "clink/native.py"))
    monkeypatch.setattr(clink.runner, "CLEANUP_SECONDS", 0.05)
    admitted = dict(clink.native._VERIFIED_NATIVE_POLICIES)
    executable = tmp_path / "fake-agy"
    executable.write_text("#!" + sys.executable + "\n" + """
import json
import pathlib
import signal
import subprocess
import sys
import uuid

if "--version" in sys.argv:
    print("1.4.12")
    raise SystemExit
root = pathlib.Path.cwd()
envelope = json.loads(sys.stdin.read())
assert envelope["event"] == "user"
content = envelope["message"]["content"]
prompt = json.loads(content.split("<assigned_task_json>\\n", 1)[1].split("\\n</assigned_task_json>", 1)[0])
session = sys.argv[sys.argv.index("--conversation") + 1] if "--conversation" in sys.argv else str(uuid.uuid4())
def hook(kind, event):
    event["workspacePaths"] = [str(root)]
    completed = subprocess.run([sys.executable, "agy_hook.py", kind], input=json.dumps(event), text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)
hook("session_start", {"conversationId": session})
print(json.dumps({"event": "init", "conversation_id": session, "init": {"cwd": str(root)}}), flush=True)
text = "Synthetic Agy result."
denials = []
if "to observe failure" in prompt:
    assert "invoke_subagent" in prompt and "spawn_subagent" not in prompt
    command_args = {"CommandLine": prompt.split("Run exactly `", 1)[1].split("`", 1)[0], "toolSummary": "Run synthetic arithmetic test", "WaitMsBeforeAsync": 10000}
    assert hook("pre_tool", {"conversationId": session, "toolCall": {"name": "run_command", "args": command_args}}) == {"decision": "auto_approve"}
    subprocess.run([sys.executable, "fixture.py"], capture_output=True)
    write_args = json.loads(prompt.split("JSON argument object: ", 1)[1].split(". Call mcp_native_fixture", 1)[0])
    assert hook("pre_tool", {"conversationId": session, "toolCall": {"name": "write_file", "args": write_args}}) == {"decision": "auto_approve"}
    (root / "arithmetic.py").write_text("def add(a, b):\\n    return a + b\\n")
    assert hook("pre_tool", {"conversationId": session, "toolCall": {"name": "run_command", "args": command_args}}) == {"decision": "auto_approve"}
    subprocess.run([sys.executable, "fixture.py"], capture_output=True)
    assert hook("pre_tool", {"conversationId": session, "toolCall": {"name": "mcp_native_fixture_canary", "args": {}}}) == {"decision": "auto_approve"}
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "canary"}})
    subprocess.run([sys.executable, "agy_mcp_server.py"], input=request + "\\n", text=True, capture_output=True, check=True)
    child_args = json.loads(prompt.split("Use invoke_subagent with exactly this JSON argument object: ", 1)[1].split(". Wait for", 1)[0])
    assert hook("pre_tool", {"conversationId": session, "toolCall": {"name": "invoke_subagent", "args": child_args}}) == {"decision": "auto_approve"}
    child = str(uuid.uuid4())
    hook("session_start", {"conversationId": child})
    hook("post_tool", {"conversationId": child, "toolCall": {"name": "read_file", "args": {"Path": str(root / "specialist.txt")}}})
    hook("stop", {"conversationId": child, "fullyIdle": True, "terminationReason": "model_stop"})
    print(json.dumps({"event": "step_update", "step_update": {"conversation_id": session, "step_index": 0,
        "step_type": "SUBAGENT", "state": "DONE", "subagent_info": {"subagents": [{"type_name": "fixture-specialist", "conversation_id": child}]}}}), flush=True)
    (root / "fake-memory").write_text("NATIVE_FIXTURE_CONTEXT_274816")
elif "continuation token" in prompt:
    text = (root / "fake-memory").read_text()
elif "permission-needed.txt" in prompt:
    if @PERMISSION_HOOK@:
        decision = hook("pre_tool", {"conversationId": session, "toolCall": {"name": "write_file", "args": {"Path": str(root / "permission-needed.txt")}}})
        assert decision["decision"] == "force_ask"
    denials = [{"action": "write", "display_name": "synthetic permission canary"}]
elif "foreground shell operation" in prompt:
    command_args = {"CommandLine": prompt.split("Run exactly `", 1)[1].split("`", 1)[0], "toolSummary": "Run synthetic cancellation target", "WaitMsBeforeAsync": 10000}
    assert hook("pre_tool", {"conversationId": session, "toolCall": {"name": "run_command", "args": command_args}}) == {"decision": "auto_approve"}
    child = subprocess.Popen([sys.executable, str(root / "fixture.py"), "child"])
    (root / "child.pid").write_text(str(child.pid))
    (root / "mutation.txt").write_text("known synthetic partial effect")
    def finish(signum, frame):
        child.wait(timeout=2)
        raise SystemExit
    signal.signal(signal.SIGTERM, finish)
    child.wait()
hook("stop", {"conversationId": session, "fullyIdle": True, "terminationReason": "model_stop"})
print(json.dumps({"event": "result", "result": {"conversation_id": session, "status": "SUCCESS", "response": text, "denied_actions": denials}}))
""".replace("@PERMISSION_HOOK@", repr(permission_hook_invoked)))
    executable.chmod(0o700)
    monkeypatch.setattr(verifier.shutil, "which", lambda name: str(executable))
    real_run = verifier.subprocess.run

    def process_metadata(command, **kwargs):
        if command[0] == "/bin/ps":
            path = tmp_path / "test_output/native-adapters/agy-attempt1.fixture/fixture.py"
            return subprocess.CompletedProcess(
                command, 0, f"Sun Nov 1 01:00:00 2026 {sys.executable} {path} child\n", ""
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(verifier.subprocess, "run", process_metadata)
    args = verifier.argument_parser().parse_args(
        [
            "--live",
            "--adapter",
            "agy",
            "--evidence",
            str(tmp_path / "docs/verification/agy-attempt1.json"),
            "--timeout",
            "5",
        ]
    )
    receipt = await verifier.verify(args)
    if permission_hook_invoked:
        assert receipt.get("stopped_after") is None, receipt
        assert receipt["native_verified"] is True
        assert receipt["submissions_in_this_invocation"] == 4
        assert receipt["submission_ledger"][2]["observations"]["permission_hook_observed"] is True
        assert receipt["submission_ledger"][3]["observations"]["agy_fixture_cancel_command_approval_emitted"] is True
    else:
        assert receipt["stopped_after"] == "permission_gate_failed"
        assert receipt["native_verified"] is False
        assert receipt["submissions_in_this_invocation"] == 3
        assert receipt["submission_ledger"][2]["observations"]["permission_hook_observed"] is False
    initial = receipt["submission_ledger"][0]["observations"]
    assert initial["fixture_subagent_read_observed"] is True
    assert initial["fixture_subagent_token_return_verified"] is False
    for kind in ("test_command", "arithmetic_edit", "mcp_canary", "subagent"):
        assert initial["agy_fixture_" + kind + "_approval_emitted"] is True
    assert receipt["pool_running_after_shutdown"] == receipt["project_leases_after_shutdown"] == 0
    encoded = json.dumps(receipt)
    assert "conversation_fingerprint" not in encoded
    assert clink.native._VERIFIED_NATIVE_POLICIES == admitted
