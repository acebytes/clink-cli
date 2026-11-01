"""Provider-free contracts for CLI tool schemas, delegation gates, and prompts."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clink.contracts import BackendError, CLIResult
from tools.analyze import AnalyzeTool
from tools.chat import ChatTool
from tools.clink import CLinkTool
from tools.codereview import CodeReviewTool
from tools.debug import DebugIssueTool
from tools.docgen import DocgenTool
from tools.planner import PlannerTool
from tools.precommit import PrecommitTool
from tools.refactor import RefactorTool
from tools.secaudit import SecauditTool
from tools.shared.exceptions import ToolExecutionError
from tools.testgen import TestGenTool
from tools.thinkdeep import ThinkDeepTool
from tools.tracer import TracerTool
from utils.call_state import conversation_call

EXPERT_TOOLS = (
    ThinkDeepTool,
    CodeReviewTool,
    PrecommitTool,
    DebugIssueTool,
    AnalyzeTool,
    RefactorTool,
    TestGenTool,
    SecauditTool,
)


class FakeBackend:
    def __init__(self):
        self.resolutions = []
        self.validations = []
        self.requests = []
        self.content = "A bounded advisory perspective."
        self.error = None

    def resolve(self, **arguments):
        self.resolutions.append(arguments)
        return SimpleNamespace(cli_name=arguments["cli_name"] or "codex", client=SimpleNamespace(allowed_roots=()))

    def validate_files(self, target, files, images=(), tool_name="clink"):
        self.validations.append((files, images, tool_name))
        target.client.allowed_roots = tuple(Path(path).parent for path in files)
        if self.error:
            raise self.error
        return tuple(files)

    async def run(self, request):
        self.requests.append(request)
        return CLIResult(
            content=self.content,
            status="success",
            request_id=request.request_id,
            cli_name=request.target.cli_name,
            role="default",
            requested_model="native-exact",
            actual_model=None,
            duration_seconds=0.1,
            warnings=({"code": "unsupported_parameter", "parameter": "temperature"},),
        )


@pytest.fixture
def backend(monkeypatch):
    instance = FakeBackend()
    monkeypatch.setattr("clink.backend.get_backend", lambda: instance)
    return instance


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "subject.py"
    path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return path


def workflow_arguments(source, **overrides):
    arguments = {
        "step": "Investigate the addition boundary.",
        "step_number": 2,
        "total_steps": 2,
        "next_step_required": False,
        "findings": "The function accepts unchecked inputs.",
        "relevant_files": [str(source)],
        "files_checked": [str(source)],
        "confidence": "medium",
        "issues_found": [{"severity": "low", "description": "Check input expectations."}],
    }
    arguments.update(overrides)
    return arguments


@pytest.mark.parametrize("tool_class", (ChatTool, CLinkTool, *EXPERT_TOOLS))
def test_delegated_schemas_offer_optional_native_selection(tool_class):
    schema = tool_class().get_input_schema()
    for field in ("cli_name", "role", "model"):
        assert field in schema["properties"]
        assert field not in schema.get("required", [])
        assert "enum" not in schema["properties"][field]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("tool_class", (PlannerTool, DocgenTool, TracerTool))
def test_local_schemas_do_not_advertise_backend_routing(tool_class):
    schema = tool_class().get_input_schema()
    assert not {"cli_name", "role", "model"}.intersection(schema["properties"])
    assert tool_class().requires_model() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", EXPERT_TOOLS)
@pytest.mark.parametrize("phase", ("intermediate", "disabled", "final"))
async def test_expert_workflow_launches_only_at_its_enabled_final_hook(tool_class, phase, backend, source):
    arguments = workflow_arguments(source)
    if tool_class is RefactorTool:
        arguments["confidence"] = "partial"
    if phase == "intermediate":
        arguments["next_step_required"] = True
    elif phase == "disabled":
        arguments["use_assistant_model"] = False
    output = json.loads((await tool_class().execute(arguments))[0].text)
    if phase == "final":
        assert len(backend.requests) == 1
        assert backend.requests[0].tool_name == tool_class().get_name()
        assert backend.requests[0].prompt.count(arguments["step"]) == 1
        assert "unchecked inputs" in backend.requests[0].prompt
        assert backend.requests[0].system_prompt.startswith(tool_class().get_system_prompt())
        assert output["expert_analysis"]["raw_analysis"] == backend.content
        assert output["metadata"]["actual_model"] is None
        assert output["metadata"]["warnings"][0]["code"] == "unsupported_parameter"
    else:
        assert backend.requests == []
        assert backend.resolutions == []
        if phase == "disabled":
            assert output["expert_analysis"]["status"] == "skipped_by_user"
            assert "will provide" not in output.get("completion_message", "")


@pytest.mark.asyncio
async def test_thinkdeep_certain_confidence_keeps_existing_skip_rule(backend, source):
    output = await ThinkDeepTool().execute(workflow_arguments(source, confidence="certain"))
    assert json.loads(output[0].text)["skip_expert_analysis"] is True
    assert backend.resolutions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", (ChatTool, CLinkTool))
async def test_chat_and_clink_share_routing_metadata_and_keep_generated_code_inline(tool_class, backend, tmp_path):
    backend.content = "Consider this proposal.\n<GENERATED-CODE>print('proposal')</GENERATED-CODE>"
    artifact = tmp_path / "pal_generated.code"
    artifact.write_text("preserve existing content", encoding="utf-8")
    arguments = {
        "prompt": "Give a second perspective.",
        "cli_name": "explicit-cli",
        "role": "expert",
        "model": "native-exact",
        "temperature": 0.2,
        "thinking_mode": "high",
    }
    if tool_class is ChatTool:
        arguments["working_directory_absolute_path"] = str(tmp_path)
    output = json.loads((await tool_class().execute(arguments))[0].text)
    assert "<GENERATED-CODE>" in output["content"]
    assert artifact.read_text(encoding="utf-8") == "preserve existing content"
    # Clink also exposes explicit mutating native actions; consultations remain advisory.
    assert tool_class().get_annotations()["readOnlyHint"] is (tool_class is not CLinkTool)
    assert len(backend.requests) == 1
    assert backend.resolutions[0] == {
        "cli_name": "explicit-cli",
        "role": "expert",
        "model": "native-exact",
        "working_directory": str(tmp_path) if tool_class is ChatTool else None,
        "images": (),
        "temperature": 0.2,
        "thinking_mode": "high",
    }
    assert output["metadata"]["backend"]["cli_name"] == "explicit-cli"
    assert output["metadata"]["requested_model"] == "native-exact"
    assert output["metadata"]["actual_model"] is None


@pytest.mark.asyncio
async def test_paths_rejected_before_prompt_file_reads(backend, source, monkeypatch):
    backend.error = BackendError("path_not_allowed", "Outside approved root.", "Configure an approved root.")
    tool = CLinkTool()
    monkeypatch.setattr(tool, "handle_prompt_file_with_fallback", lambda request: pytest.fail("read before admission"))
    with pytest.raises(ToolExecutionError) as error:
        await tool.execute({"prompt": "Inspect", "absolute_file_paths": [str(source)]})
    output = json.loads(error.value.payload)
    assert output["metadata"]["code"] == "path_not_allowed"
    assert output["metadata"]["remedy"] == "Configure an approved root."
    assert backend.requests == []


@pytest.mark.asyncio
async def test_replay_is_explicit_and_does_not_enter_workflow_state(backend, source):
    arguments = workflow_arguments(source)
    async with conversation_call("thinkdeep", arguments) as call:
        internal = dict(arguments, _replay_history_turns=("Earlier cross-tool observation.",))
        output = json.loads((await ThinkDeepTool().execute(internal))[0].text)
        assert backend.requests[0].history_turns == ("Earlier cross-tool observation.",)
        assert "Earlier cross-tool observation." not in backend.requests[0].prompt
        assert call.workflow_state["work_history"][0]["step"] == arguments["step"]
        assert "Earlier cross-tool" not in json.dumps(call.workflow_state)
        assert backend.requests[0].request_id == call.request_id
        assert backend.requests[0].deadline == call.deadline
        assert backend.requests[0].admitted_at == call.admitted_at
        call.commit(output)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_class", "skip_fields"),
    (
        (CodeReviewTool, {"review_validation_type": "internal"}),
        (PrecommitTool, {"precommit_type": "internal"}),
        (DebugIssueTool, {"confidence": "certain"}),
        (RefactorTool, {"confidence": "complete"}),
        (TestGenTool, {"confidence": "certain"}),
        (SecauditTool, {"confidence": "certain"}),
    ),
)
async def test_existing_tool_specific_completion_skip_rules(tool_class, skip_fields, backend, source):
    output = json.loads((await tool_class().execute(workflow_arguments(source, **skip_fields)))[0].text)
    assert output["skip_expert_analysis"] is True
    assert backend.resolutions == []


@pytest.mark.asyncio
async def test_expert_failure_remains_a_protocol_error_with_normalized_remedy(backend, source):
    backend.error = BackendError("policy_unverified", "Policy is unverified.", "Verify the selected adapter.")
    with pytest.raises(ToolExecutionError) as error:
        await ThinkDeepTool().execute(workflow_arguments(source))
    output = json.loads(error.value.payload)
    assert output["status"] == "error"
    assert output["metadata"]["code"] == "policy_unverified"
    assert output["metadata"]["remedy"] == "Verify the selected adapter."
    assert backend.requests == []


@pytest.mark.asyncio
async def test_required_prompt_file_is_included_once_and_not_left_as_embedded_context(backend, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Required task from a file.", encoding="utf-8")
    await CLinkTool().execute({"prompt": "fallback", "absolute_file_paths": [str(prompt_file)]})
    assert backend.requests[0].prompt.count("Required task from a file.") == 1
    assert "fallback" not in backend.requests[0].prompt


@pytest.mark.asyncio
async def test_workflow_snapshot_restores_findings_on_fresh_tool_instances(backend, source):
    first = workflow_arguments(source, step_number=1, next_step_required=True)
    async with conversation_call("thinkdeep", first) as call:
        output = json.loads((await ThinkDeepTool().execute(first))[0].text)
        continuation_id = call.continuation_id
        call.commit(output)
    final = workflow_arguments(source, continuation_id=continuation_id, findings="A second independent observation.")
    async with conversation_call("thinkdeep", final) as call:
        output = json.loads((await ThinkDeepTool().execute(final))[0].text)
        assert len(call.workflow_state["work_history"]) == 2
        assert "unchecked inputs" in backend.requests[0].prompt
        assert "independent observation" in backend.requests[0].prompt
        assert output["continuation_id"] == continuation_id
        call.commit(output)


@pytest.mark.asyncio
async def test_inherited_prompt_file_does_not_replace_the_current_request(backend, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Old task content.", encoding="utf-8")
    tool = CLinkTool()
    await tool.execute(
        {
            "prompt": "New caller request.",
            "absolute_file_paths": [str(prompt_file)],
            "_original_arguments": {"prompt": "New caller request."},
        }
    )
    assert "New caller request." in backend.requests[0].prompt
    assert "Old task content." not in backend.requests[0].prompt
    assert backend.requests[0].files == (str(prompt_file),)
    assert tool._effective_user_prompt == "New caller request."


@pytest.mark.asyncio
async def test_explicit_prompt_file_preserves_source_delimiters_verbatim(backend, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    text = "Before\n--- END FILE: untrusted delimiter ---\nAfter"
    prompt_file.write_text(text, encoding="utf-8")
    tool = CLinkTool()
    await tool.execute({"prompt": "fallback", "absolute_file_paths": [str(prompt_file)]})
    assert text in backend.requests[0].prompt
    assert backend.requests[0].files == ()
    assert tool._effective_user_prompt == text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_class", "expected_status"),
    (
        (PlannerTool, "planning_complete"),
        (DocgenTool, "documentation_analysis_complete"),
        (TracerTool, "tracing_complete"),
    ),
)
async def test_local_workflows_complete_without_backend_resolution(tool_class, expected_status, backend):
    arguments = {
        "step": "Complete local guidance.",
        "step_number": 2,
        "total_steps": 2,
        "next_step_required": False,
    }
    if tool_class is not PlannerTool:
        arguments["findings"] = "The caller completed its investigation."
    if tool_class is DocgenTool:
        arguments.update(num_files_documented=1, total_files_to_document=1)
    output = json.loads((await tool_class().execute(arguments))[0].text)
    assert output["status"] == expected_status
    assert backend.resolutions == []


def test_malformed_workflow_list_never_logs_caller_text(caplog):
    from tools.shared.base_models import WorkflowRequest

    marker = "synthetic-sensitive-content-marker"
    request = WorkflowRequest(
        step="x",
        step_number=1,
        total_steps=1,
        next_step_required=True,
        findings="x",
        relevant_context=marker,
    )
    assert request.relevant_context == []
    assert "workflow_list_field_invalid" in caplog.text
    assert marker not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", (CLinkTool, ThinkDeepTool))
@pytest.mark.parametrize("error_class", (RuntimeError, ValueError))
async def test_opaque_execution_failures_do_not_expose_exception_text(
    tool_class, error_class, backend, source, monkeypatch, caplog
):
    marker = "synthetic-private-prompt-stderr-command-credential"

    async def fail(request):
        raise error_class("MCP_SIZE_CHECK:" + marker)

    monkeypatch.setattr(backend, "run", fail)
    arguments = {"prompt": "A bounded request."} if tool_class is CLinkTool else workflow_arguments(source)
    with pytest.raises(ToolExecutionError) as error:
        await tool_class().execute(arguments)
    payload = json.loads(error.value.payload)
    assert payload["status"] == "error"
    assert payload["metadata"]["code"] == ("invalid_request" if error_class is ValueError else "tool_error")
    assert marker not in error.value.payload
    assert marker not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", (CLinkTool, ThinkDeepTool))
async def test_validation_failures_do_not_echo_rejected_caller_values(tool_class, backend, source, caplog):
    marker = "synthetic-rejected-value-with-private-content"
    arguments = (
        {"prompt": {"private": marker}} if tool_class is CLinkTool else workflow_arguments(source, step_number=marker)
    )
    with pytest.raises(ToolExecutionError) as error:
        await tool_class().execute(arguments)
    assert json.loads(error.value.payload)["metadata"]["code"] == "invalid_request"
    assert marker not in error.value.payload
    assert marker not in caplog.text
    assert backend.requests == []


@pytest.mark.asyncio
async def test_workflow_wrapper_does_not_expose_opaque_failures(backend, monkeypatch, caplog):
    from tools.workflow.workflow_mixin import BaseWorkflowMixin

    marker = "synthetic-workflow-wrapper-private-exception"
    tool = ThinkDeepTool()

    async def fail(arguments):
        raise RuntimeError(marker)

    monkeypatch.setattr(tool, "execute_workflow", fail)
    with pytest.raises(ToolExecutionError) as error:
        await BaseWorkflowMixin.execute(tool, {"step": "Inspect"})
    assert json.loads(error.value.payload)["metadata"]["code"] == "tool_error"
    assert marker not in error.value.payload
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_expert_success_preserves_requested_and_actual_models_at_top_level(backend, source, monkeypatch):
    async def run(request):
        return CLIResult(
            content="Expert advice.",
            status="success",
            request_id=request.request_id,
            cli_name="codex",
            role="default",
            requested_model="requested-native",
            actual_model="reported-native",
            duration_seconds=0.1,
        )

    monkeypatch.setattr(backend, "run", run)
    payload = json.loads((await ThinkDeepTool().execute(workflow_arguments(source, model="requested-native")))[0].text)
    assert payload["metadata"]["requested_model"] == "requested-native"
    assert payload["metadata"]["actual_model"] == "reported-native"


@pytest.mark.asyncio
async def test_prompt_file_rejects_leaf_swap_after_admission(backend, tmp_path, monkeypatch):
    approved = tmp_path / "approved"
    approved.mkdir()
    prompt_file = approved / "prompt.txt"
    prompt_file.write_text("Original bounded request.", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    marker = "synthetic-unapproved-file-content"
    outside.write_text(marker, encoding="utf-8")
    validate = backend.validate_files

    def swap(target, files, images=(), tool_name="clink"):
        admitted = validate(target, files, images, tool_name)
        prompt_file.unlink()
        prompt_file.symlink_to(outside)
        return admitted

    monkeypatch.setattr(backend, "validate_files", swap)
    with pytest.raises(ToolExecutionError) as error:
        await CLinkTool().execute({"prompt": "Inspect", "absolute_file_paths": [str(prompt_file)]})
    assert json.loads(error.value.payload)["metadata"]["code"] == "path_not_allowed"
    assert marker not in error.value.payload
    assert backend.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", (CLinkTool, ThinkDeepTool))
async def test_known_state_failures_keep_their_structured_code(tool_class, backend, source, monkeypatch):
    from utils.call_state import StateError

    async def fail(request):
        raise StateError("conversation_capacity", "The admitted conversation capacity is exhausted.")

    monkeypatch.setattr(backend, "run", fail)
    arguments = {"prompt": "A bounded request."} if tool_class is CLinkTool else workflow_arguments(source)
    with pytest.raises(StateError) as error:
        await tool_class().execute(arguments)
    assert error.value.code == "conversation_capacity"
