"""One authorized search-capability probe; never changes production admission."""

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    import clink.backend as backend_module
    import server
    from tools.shared.exceptions import ToolExecutionError

    if sys.argv[1:] != ["--live"]:
        raise SystemExit("Explicit --live opt-in required")
    evidence = ROOT / "docs/verification/native-search-1"
    evidence.mkdir(parents=True, exist_ok=False)
    original = backend_module._VERIFIED_POLICIES["codex"]
    assert original.verified and not original.native_search
    # Only the unverified search-capability bit is enabled inside this isolated
    # verification process. Binary, recipe, resources and mandatory controls stay pinned.
    backend_module._VERIFIED_POLICIES["codex"] = replace(original, native_search=True)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "verification_only_search_override": True,
        "mandatory_controls_unchanged": True,
        "base_admission_sha256": original.evidence_sha256,
        "binary_sha256": original.executable_sha256,
        "recipe_sha256": original.recipe_sha256,
        "resource_files": original.resource_files,
        "parser_hashes": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("clink/parsers/base.py", "clink/parsers/codex.py")
        },
        "success": False,
    }
    try:
        result = await asyncio.wait_for(
            server.handle_call_tool(
                "apilookup",
                {
                    "cli_name": "codex",
                    "model": "gpt-6.1-sol",
                    "prompt": "Use native web search to find the official Model Context Protocol introduction. Return its title and source URL.",
                },
            ),
            240,
        )
        data = json.loads(result[0].text)
        metadata = data.get("metadata", {})
        record.update(
            status=data.get("status"),
            search_performed=metadata.get("search_performed"),
            sources=data.get("sources", []),
            requested_model=metadata.get("requested_model"),
            actual_model=metadata.get("actual_model"),
            backend=metadata.get("backend"),
        )
        record["success"] = bool(
            record["status"] == "success" and record["search_performed"] is True and record["sources"]
        )
    except ToolExecutionError as exc:
        failure = json.loads(exc.payload)
        record.update(status="error", error=failure.get("error", failure.get("metadata", {})))
    finally:
        await backend_module.get_backend().close()
        backend_module._VERIFIED_POLICIES["codex"] = original
        (evidence / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        (evidence / "probe.py").write_text(Path(__file__).read_text())
        print(json.dumps(record), flush=True)
    return 0 if record["success"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
