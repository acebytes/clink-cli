"""Exercise UTF-8 in actual local and delegated workflow responses."""

import json

import pytest

from tools.analyze import AnalyzeTool
from tools.codereview import CodeReviewTool
from tools.debug import DebugIssueTool
from tools.planner import PlannerTool


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_class", [AnalyzeTool, CodeReviewTool, DebugIssueTool])
async def test_workflow_cli_preserves_french_findings_and_analysis(tool_class, cli_backend, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALE", "fr-FR")
    source = tmp_path / "données.py"
    source.write_text("# Gestion des données 🔍\n", encoding="utf-8")
    analysis = "ÉLEVÉ: problème analysé; paramètres vérifiés ✅"
    cli_backend.content = analysis

    result = await tool_class().execute(
        {
            "step": "Analyser les données de connexion.",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "findings": "L’erreur concerne les données 🔍.",
            "relevant_files": [str(source)],
            "confidence": "medium",
            "model": "native-exact",
        }
    )

    payload = json.loads(result[0].text)
    assert payload["expert_analysis"]["raw_analysis"] == analysis
    assert "\\u" not in result[0].text
    assert len(cli_backend.requests) == 1
    assert "données" in cli_backend.requests[0].prompt
    assert "fr-FR" in cli_backend.requests[0].system_prompt


@pytest.mark.asyncio
async def test_workflow_local_findings_preserve_unicode_without_launch(cli_backend):
    findings = "Variable 'données' non définie; connexion interrompue 🔴"
    tool = DebugIssueTool()
    result = await tool.execute(
        {
            "step": "Examiner l’erreur.",
            "step_number": 1,
            "total_steps": 2,
            "next_step_required": True,
            "findings": findings,
            "confidence": "medium",
        }
    )
    payload = json.loads(result[0].text)
    assert payload["status"] == "pause_for_investigation"
    assert tool.work_history[-1]["findings"] == findings
    assert "\\u" not in result[0].text
    assert cli_backend.requests == []


@pytest.mark.asyncio
async def test_workflow_json_roundtrip_preserves_emoji_and_cjk(cli_backend):
    findings = "検証済み ✅; ошибка найдена 🔍"
    result = await PlannerTool().execute(
        {"step": findings, "step_number": 1, "total_steps": 2, "next_step_required": True}
    )
    assert findings in json.dumps(json.loads(result[0].text), ensure_ascii=False)
    assert cli_backend.requests == []
