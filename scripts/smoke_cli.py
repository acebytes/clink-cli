"""Opt-in, advisory real-CLI smoke coverage for all 18 MCP tools.

Only synthetic source and questions are submitted. Reports retain status/model
metadata, not raw prompts, responses, commands, credentials, or stderr.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def cases(workspace, cli_name, model):
    routing = {"cli_name": cli_name, "model": model, "role": "default"}
    source = str(workspace / "example.py")
    yield "chat", {
        **routing,
        "prompt": "Briefly review this tiny function; return advice only.",
        "absolute_file_paths": [source],
        "working_directory_absolute_path": str(workspace),
    }
    yield "clink", {
        **routing,
        "prompt": "Explain what the supplied function does in one sentence.",
        "absolute_file_paths": [source],
    }
    for name in ("thinkdeep", "codereview", "precommit", "debug", "analyze", "refactor", "testgen", "secaudit"):
        arguments = {
            **routing,
            "step": "Review this tiny synthetic function; provide concise advisory findings only.",
            "step_number": 2,
            "total_steps": 2,
            "next_step_required": False,
            "findings": "The function returns a plus b. Caller inspected this entire two-line file. No observed defect.",
            "confidence": "high",
            "relevant_files": [source],
            "files_checked": [source],
            "use_assistant_model": True,
        }
        if name == "precommit":
            arguments["path"] = str(workspace)
            arguments["step_number"] = arguments["total_steps"] = 3
        if name == "refactor":
            arguments["confidence"] = "partial"
        yield name, arguments
    yield "consensus", {
        "step": "Should this two-line add function remain simple? Briefly assess the proposal.",
        "step_number": 1,
        "total_steps": 2,
        "next_step_required": False,
        "findings": "Caller seeks independent perspectives.",
        "models": [{**routing, "stance": "for"}, {**routing, "stance": "against"}],
        "execution_mode": "parallel",
        "relevant_files": [source],
        "timeout_seconds": 180,
    }
    yield "apilookup", {
        **routing,
        "prompt": "Use native web search to find the official Model Context Protocol introduction. Give its page title and source URL.",
    }
    yield "planner", {
        "step": "A local plan is complete.",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
    }
    yield "docgen", {
        "step": "Local documentation analysis is complete.",
        "step_number": 2,
        "total_steps": 2,
        "next_step_required": False,
        "findings": "The caller documented the function.",
        "num_files_documented": 1,
        "total_files_to_document": 1,
        "document_complexity": True,
        "document_flow": True,
        "update_existing": True,
        "comments_on_complex_logic": True,
    }
    yield "tracer", {
        "step": "Local trace is complete.",
        "step_number": 1,
        "total_steps": 1,
        "next_step_required": False,
        "findings": "add returns the sum.",
        "target_description": "add",
        "trace_mode": "precision",
    }
    yield "challenge", {"prompt": "Every function requires a class wrapper."}
    yield "listmodels", {}
    yield "version", {}


async def run(arguments):
    import server
    from clink.backend import get_backend
    from tools.shared.exceptions import ToolExecutionError

    workspace = ROOT / "test_output" / "live-smoke"
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "example.py"
    expected = b"def add(a, b):\n    return a + b\n"
    source.write_bytes(expected)
    evidence = ROOT / "docs" / "verification" / arguments.label
    evidence.mkdir(parents=True, exist_ok=False)
    records = []
    selected = [
        (name, data)
        for name, data in cases(workspace, arguments.cli_name, arguments.model)
        if not arguments.tool or name in arguments.tool
    ]
    for name, data in selected:
        server.TOOLS[name].get_request_model()(**data)
    try:
        for name, tool_arguments in selected:
            try:
                response = await asyncio.wait_for(server.handle_call_tool(name, tool_arguments), 240)
                data = json.loads(response[0].text)
                metadata = data.get("metadata") or {}
                participants = data.get("accumulated_responses", [])
                record = {
                    "tool": name,
                    "status": data.get("status"),
                    "success": data.get("attempt_success", True),
                    "requested_model": metadata.get("requested_model"),
                    "actual_model": metadata.get("actual_model"),
                    "backend": metadata.get("backend"),
                    "verification_status": metadata.get("verification_status"),
                    "sources": data.get("sources", []),
                    "warnings": metadata.get("warnings", []),
                    "expert_analysis_present": bool(data.get("expert_analysis")),
                    "participants": [
                        {
                            "status": item.get("status"),
                            "valid": item.get("valid"),
                            "requested_model": item.get("metadata", {}).get("requested_model"),
                            "actual_model": item.get("metadata", {}).get("actual_model"),
                            "backend": item.get("metadata", {}).get("backend"),
                        }
                        for item in participants
                    ],
                }
            except ToolExecutionError as exc:
                data = json.loads(exc.payload)
                record = {
                    "tool": name,
                    "success": False,
                    "status": "error",
                    "error_code": data.get("error", {}).get("code") or data.get("metadata", {}).get("code"),
                    "remedy": data.get("error", {}).get("remedy"),
                }
            except asyncio.TimeoutError:
                record = {"tool": name, "success": False, "status": "error", "error_code": "smoke_timeout"}
            record["fixture_unchanged"] = source.read_bytes() == expected
            records.append(record)
            report = {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "cli_name": arguments.cli_name,
                "selected_model": arguments.model,
                "real_cli_opt_in": True,
                "python_version": sys.version.split()[0],
                "smoke_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "results": records,
            }
            (evidence / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(record), flush=True)
            if not record["success"] or not record["fixture_unchanged"]:
                break
    finally:
        await get_backend().close()
    return 0 if all(item["success"] and item["fixture_unchanged"] for item in records) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly opt in to authenticated native inference")
    parser.add_argument("--cli-name", default="codex")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--label", required=True, help="Unique durable evidence directory name")
    parser.add_argument("--tool", action="append", help="Limit to a named tool (repeatable)")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("--live is required; ordinary tests never invoke authenticated CLIs")
    if not arguments.label.replace("-", "").replace("_", "").isalnum():
        parser.error("Invalid evidence label")
    return asyncio.run(run(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
