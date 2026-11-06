"""Legacy reasoning controls are explicit warnings, with no native budget mapping."""

import sys
from types import SimpleNamespace

import pytest

from clink.backend import CLIBackend
from clink.contracts import AdapterPolicy
from tools import AnalyzeTool, ChatTool, CodeReviewTool, ThinkDeepTool


def test_default_thinking_modes():
    assert ChatTool().get_default_thinking_mode() == "medium"
    assert ThinkDeepTool().get_default_thinking_mode() == "high"
    assert CodeReviewTool().get_default_thinking_mode() == "medium"
    assert AnalyzeTool().get_default_thinking_mode() == "medium"


@pytest.mark.parametrize("mode", ["minimal", "low", "medium", "high", "max"])
def test_explicit_thinking_mode_is_unapplied_with_warning(mode, tmp_path, monkeypatch):
    role = SimpleNamespace(name="default", prompt_text="Advice only", role_args=(), model=None, description=None)
    client = SimpleNamespace(
        name="codex",
        adapter="codex",
        runner="codex",
        executable=(sys.executable,),
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        config_args=(),
        env={},
        timeout_seconds=30,
        parser="codex_jsonl",
        default_model=None,
        get_role=lambda name: role,
    )
    registry = SimpleNamespace(get_client=lambda name: client)
    monkeypatch.setenv("DEFAULT_CLI", "codex")
    backend = CLIBackend(registry)
    # Test reasoning compatibility independently of a production adapter's model catalog.
    monkeypatch.setattr(
        backend,
        "_policy",
        lambda adapter: AdapterPolicy(adapter, verification_status="test_fixture_no_catalog"),
    )
    target = backend.resolve(thinking_mode=mode)
    assert any(code == "unsupported_parameter" and "thinking_mode" in message for code, message in target.warnings)
    assert target.effective_model is None
    assert target.client.config_args == ()
