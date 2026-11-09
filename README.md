# Clink CLI and MCP server

Call an AI coding agent you already have installed. Use `clink generate` when a script or app needs one answer as JSON. Connect `clink-mcp-server` when you want an MCP client such as Claude Code to ask a second agent for advice or delegate a task. The `clink-cli` Python package installs both commands.

Clink builds on the original CLI bridge and multi-model workflows in Beehive Innovations’ [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server), formerly Zen MCP. **Copyright 2025 Beehive Innovations.** Clink retains the upstream attribution and [Apache 2.0 license](https://github.com/acebytes/clink-cli/blob/main/LICENSE).

## Install with uv

```sh
uv tool install clink-cli
```

**Check your agent build first.** Clink needs Python 3.10+ (tested on 3.12). Local tools such as `version` and `listmodels` work without a CLI login. Calling an agent requires an exact, verified Apple Silicon macOS executable for the operation you choose. A newer version, another build with the same version number, or an Intel/Linux binary can install successfully and still be refused. Sign in through the agent’s own CLI. See the [pinned downloads and fingerprints](https://github.com/acebytes/clink-cli/blob/main/docs/pinned-cli-builds.md).

If uv already manages the v0.2.0 GitHub wheel under the old package name, run `uv tool uninstall clink-mcp-server` before installing `clink-cli`. Start with the [Clink documentation](https://clinkcli.org/). For a wheel, source checkout, or another MCP client, see [project setup](https://github.com/acebytes/clink-cli/blob/main/docs/project-setup.md).

## Programmatic generation

[Prompter](https://prompterapp.org/) bundles Clink in its macOS app. When a user chooses Prompt Improve, the app sends the draft and displays the selected CLI’s rewrite. Here is how to make the same kind of call from a script.

**1. Check Claude Code.** With `claude` on your path, run this on your Mac:

```sh
clink generation-capabilities --json --cli claude --executable "$(realpath "$(command -v claude)")"
```

Look for `"status": "compatible"` in `preflight`. This checks the executable without launching it or testing login. To see the supported families without requesting an individual check, run `clink generation-capabilities --json`.

**2. Approve the project.** In a project you want Claude to use, run:

```sh
clink allow "$(pwd -P)" --cli claude
```

Read the path and answer the default-No question. For an unattended script, explicitly approve that same directory with `--yes`. **If you use PyPI 0.2.3**, run the first approval from inside the project itself. That release can also save the folder the command was run from. If you previously ran it from home, [check your existing permissions](https://github.com/acebytes/clink-cli/blob/main/docs/cli-configuration.md#review-grants-created-by-023).

**3. Make one request.** Save the following as `rewrite.py` and run `python3 rewrite.py` from the approved project. Use a full model ID your Claude account can access. Clink passes the project and instructions to the CLI under its native permissions.

```python
import json
import subprocess
from pathlib import Path
from uuid import uuid4

request = {
    "schema_version": 3,
    "request_id": str(uuid4()),
    "cli": "claude",
    "execution_mode": "native",
    "project_dir": str(Path.cwd().resolve()),
    "model_selection": {"kind": "explicit", "id": "claude-opus-5-5"},
    "instructions": "Rewrite the input as one clear question. Return only the question.",
    "input": "cache here good idea? what might go wrong",
    "timeout_seconds": 120,
}
attempt = subprocess.run(
    ["clink", "generate", "--request-json", "-"],
    input=json.dumps(request), stdout=subprocess.PIPE, text=True, check=False,
)
result = json.loads(attempt.stdout)
if attempt.returncode or result["status"] != "completed":
    raise SystemExit(result["error"] or result["status"])
if result["request_id"] != request["request_id"]:
    raise SystemExit("Clink returned a result for a different request")
print(result["output"])
```

**4. Read the result.** The CLI reads one JSON object through end-of-input and writes one terminal JSON object to stdout. For example, this excerpt shows the fields a caller reads from a successful **offline test** with a synthetic agent:

```json
{"status": "completed", "output": "final answer", "truncated": false, "error": null}
```

The full response includes the request ID, request and input hashes, execution details, and warnings. `execution.actual_model` can be null. A completed response confirms that the agent returned final text. Verify the work separately if the agent was asked to perform an action. See the [native generation contract](https://github.com/acebytes/clink-cli/blob/main/docs/generation-native-protocol.md) and [schema 3 model selection](https://github.com/acebytes/clink-cli/blob/main/docs/generation-native-families.md).

| Exit | What the caller should do |
| --- | --- |
| 0 | Read the completed output. |
| 1 | Inspect `error.code` and `error.message` for a configuration, policy, login or execution failure. |
| 2 | Correct the invocation or invalid/oversized request. |
| 3 | Treat the returned prefix as incomplete. Some families reject oversized output with exit 1 instead. |
| 124 / 130 | The attempt timed out / was cancelled. Check for side effects before deciding whether to retry. |

## Connect to Claude Code

From the repository where you use Claude Code, register Clink and check the connection:

```sh
project="$(pwd -P)"
claude mcp add --scope local --transport stdio clink -- "$(uv tool dir --bin)/clink-mcp-server" --project "$project"
claude mcp get clink
```

Open Claude Code in that directory. Use `/mcp` to confirm that Clink is connected, then ask it to call Clink’s `version` or `listmodels` tool. Neither makes a model call.

**To run a native task in this repository**, approve it for the agent that will do the work. Run this in the same directory, then reconnect Clink or restart the MCP client:

```sh
clink allow "$(pwd -P)" --cli claude --project "$(pwd -P)"
```

This also works when a script has already approved a different folder for Claude. `--project` selects the configuration the server loads. An existing list of approved folders still applies. The native task below uses its explicit `project_dir`. For a separate consultation working directory in each repository, use a [project-local manifest](https://github.com/acebytes/clink-cli/blob/main/docs/project-setup.md), which takes precedence over the user manifest for MCP. Script generation reads its own user or application configuration. See [configuration precedence](https://github.com/acebytes/clink-cli/blob/main/docs/cli-configuration.md).

For advice, pass these arguments to the `clink` MCP tool:

```json
{"cli_name":"codex","prompt":"Review this proposed cache policy: cache successful responses for 60 seconds. What edge case should I test first?"}
```

For a native task, use an approved absolute project path:

```json
{
  "action": "start",
  "cli_name": "claude",
  "project_dir": "/absolute/path/to/your-project",
  "prompt": "Find the cache invalidation tests and report the file paths.",
  "request_key": "find-cache-tests-1",
  "timeout_seconds": 180
}
```

Keep the returned `task_handle` and pass it to `{"action":"status","task_handle":"<returned handle>"}`. A native task also supports `followup` when offered, and `cancel`. Handles live in server memory and expire on restart. See the [native task guide](https://github.com/acebytes/clink-cli/blob/main/docs/native-cli-tasks.md).

## Supported operations

A **consultation** returns advice. An **MCP native task** starts a CLI agent you can poll or follow up with. **Generation** returns one result to a script with no automatic retry or fallback. Each “Yes” below means Clink verified a specific executable and launch configuration for that operation. The [verification record](https://github.com/acebytes/clink-cli/blob/main/docs/release-evidence.md) calls this *admission*.

| Agent and verified build | Consult | MCP native task | Generate |
| --- | --- | --- | --- |
| Codex 0.166.0 | Yes, including explicit images and structured search | No | Yes, explicit model |
| Claude Code 2.1.304 (`claude`) | No | Yes | Yes, explicit full model ID |
| xAI Grok Build 1.0.70 (`grok`) | No | Yes | Yes, native default |
| Antigravity 1.4.12 (`agy`) | No | Yes | No |
| Gemini CLI | No | No | No |

Native tasks and generation can edit files, run tools, and call external services within the CLI’s own permissions. Approved folders control which project and supplied paths Clink accepts. They do not confine everything the native CLI can access. Cancellation does not undo completed actions.

## Tools and configuration

All 18 MCP tools are available by default. `DISABLED_TOOLS` can hide optional tools. `version` and `listmodels` remain available.

| Tools | Purpose |
| --- | --- |
| `chat`, `clink` | Consult an agent. `clink` also runs native tasks. |
| `thinkdeep`, `codereview`, `precommit`, `debug`, `secaudit`, `analyze`, `refactor`, `testgen` | Investigate in caller-driven steps, with an optional final expert consultation. |
| `consensus`, `apilookup` | Gather independent opinions, or look up API documentation with structured sources. |
| `planner`, `docgen`, `tracer`, `challenge` | Local planning, documentation, tracing and reasoning workflows. |
| `listmodels`, `version` | Inspect the configured agents, supported operations and server version. |

## Coming from PAL MCP

PAL introduced the `clink` tool alongside its provider-backed workflows. This fork carries that work forward for people who want to call installed CLI agents from scripts, applications, or MCP clients. If you use PAL’s provider integrations, [PAL’s documentation](https://github.com/BeehiveInnovations/pal-mcp-server) covers that setup. See the [Clink migration guide](https://github.com/acebytes/clink-cli/blob/main/docs/migration.md) for configuration changes.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| `policy_unverified` or preflight `admission_unverified` | Check both the agent build and the operation in the table above. Compare the [executable fingerprint](https://github.com/acebytes/clink-cli/blob/main/docs/pinned-cli-builds.md). |
| `path_not_allowed` | Approve the exact folder for the selected CLI. For an MCP task, pass the server’s `--project` to `clink allow`, then reconnect. |
| `permission_needed` | Review the requested action and the CLI’s own project trust and headless permissions. A Clink folder approval does not grant native permissions. |
| Installed CLI appears in `listmodels`, but a call is refused | Check the status of the specific operation. Executable discovery and CLI authentication are separate checks. |

## Development

```sh
./run-server.sh --setup
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -m "not integration and not real_cli and not private_evidence"
```

See [public verification](https://github.com/acebytes/clink-cli/blob/main/docs/release-evidence.md) for the formatting, build and evidence checks. Report issues in the [Clink tracker](https://github.com/acebytes/clink-cli/issues).
