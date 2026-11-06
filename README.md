# clink-mcp-server

Delegate work to installed CLI agents through MCP. Consultations return advice and proposed code. Explicit native tasks can edit files, run tests, and use the selected CLI's configured tools, hooks, skills, plugins, MCP integrations, and subagents under its native permissions.

Version **0.2.1** preserves PAL's **18 MCP tools** and adds a separate `clink generate` command for bounded programmatic text generation. The Python distribution is named `clink-cli` and installs both `clink` and `clink-mcp-server`.

Derived from [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server), formerly Zen MCP. **Copyright 2025 Beehive Innovations.** Retains upstream attribution and the [Apache 2.0 license](LICENSE).

## Supported operations

Admission is specific to an operation, executable, launch recipe, and parser. Support in one column does not enable another.

| CLI | Consultation | Native MCP tasks | Programmatic generation |
| --- | --- | --- | --- |
| Codex 0.166.0 | Admitted for text, explicit local images, and structured search. | Disabled: supported output omits a genuine prelaunch permission refusal. | Admitted; schema 2 or 3 with an explicit model. |
| Claude Code 2.1.304 | Unavailable under the advisory policy. | Admitted. | Admitted; schema 2 or 3 with an explicit full model ID. |
| xAI Grok Build 1.0.70 (`grok`) | Unverified. | Admitted. | Admitted; schema 3 with `native_default` selection. |
| Antigravity 1.4.12 (`agy`) | Unverified. | Admitted. | Implemented but unverified; execution is refused. |
| Gemini | Unverified. | Not implemented. | Not implemented. |

The recorded admissions cover exact macOS arm64 binaries. Other builds, operating systems, versions, or changed recipes require verification and fail closed. An installed executable or successful login alone is insufficient. Grok reported `grok-4.7-build` in the recorded successful generation text/action checks. Antigravity's native-task actual model remains unknown; its native-task admission does not admit generation. See [release evidence and limits](docs/release-evidence.md).

Python 3.10+ is required; Python 3.12 is the tested runtime. Process supervision targets POSIX systems. Local discovery and offline tests can run without CLI credentials. Native Windows delegation is unavailable; the [container](docker/README.md) supports local tools without installing CLI agents.

## Install and connect

To install the PyPI release into an isolated tool environment, use:

```sh
uv tool install clink-cli
clink generation-capabilities --json
clink-mcp-server --help
```

If you installed the v0.2.0 GitHub wheel with uv, run `uv tool uninstall clink-mcp-server` first so both executable names are available to the new distribution.

For a standalone wheel installation, replace the wheel path and choose a persistent environment outside the source tree:

```sh
python3 -m venv /absolute/path/to/clink-runtime
/absolute/path/to/clink-runtime/bin/python -m pip install /absolute/path/to/clink_cli-0.2.1-py3-none-any.whl
/absolute/path/to/clink-runtime/bin/python -m clink.setup --project /absolute/path/to/your-project
```

The installed package contains its manifests, prompts, and admission resources. It does not need the original checkout at runtime. To register its console entrypoint directly in an MCP client, use:

```json
{
  "mcpServers": {
    "clink": {
      "command": "/absolute/path/to/clink-runtime/bin/clink-mcp-server",
      "args": ["--project", "/absolute/path/to/your-project"]
    }
  }
}
```

Alternatively, from the reviewed 0.2.1 source tree:

```sh
cd /absolute/path/to/clink-mcp-server
./run-server.sh --setup
```

This explicitly creates `.venv`, installs runtime requirements, and exits. Ordinary startup installs nothing. The launcher does not create `.env`, copy credentials, change native CLI configuration, or register MCP clients. Optional server settings are documented in [.env.example](.env.example). To build a wheel from this tree, install the development requirements and run `.venv/bin/python -m build`; the wheel is written under `dist/`.

Configure an existing project from that source installation:

```sh
.venv/bin/python -m clink.setup --project /absolute/path/to/your-project
```

Setup creates project-local `.clink/cli_clients/` manifests, `.clink/prompts/`, and `.mcp.json`. It makes no model calls and preserves custom files by reporting conflicts. The generated MCP registration binds a server explicitly to that project. Reconnect your MCP client, then call `version` and `listmodels` with empty arguments. These calls need no native CLI login.

Claude Code users can load the checkout-local setup plugin:

```sh
cd /absolute/path/to/your-project
claude --plugin-dir /absolute/path/to/clink-mcp-server/integrations/claude
```

Then run `/clink:setup`. This command is available only when that local plugin is loaded; MCP registration alone does not install it. See the [project setup guide](docs/project-setup.md) for selected clients, configuration precedence, conflicts, and recovery.

For another MCP client, adapt this stdio registration to its configuration format:

```json
{
  "mcpServers": {
    "clink": {
      "command": "/absolute/path/to/clink-mcp-server/run-server.sh",
      "args": ["--project", "/absolute/path/to/your-project"]
    }
  }
}
```

Authenticate the chosen CLI through its own login mechanism before an admitted model call. Ensure the MCP client's `PATH` can find it, or configure its executable path in a manifest. The server does not require hosted-provider API keys. See [getting started](docs/getting-started.md), [CLI configuration](docs/cli-configuration.md), and [migration from PAL](docs/migration.md).

## Consult or delegate

Omitting `action` selects consultation with `codex` and the `default` role. The following is a `clink` MCP tool argument, not a shell command:

```json
{"prompt":"Review this proposed change and return suggested improvements.","cli_name":"codex"}
```

For native work, select an admitted CLI and a project inside its configured roots:

```json
{
  "action": "start",
  "cli_name": "claude",
  "project_dir": "/absolute/path/to/your-project",
  "prompt": "Fix the failing test, run the relevant checks, and report changed files and unfinished work.",
  "request_key": "fix-test-1",
  "timeout_seconds": 180
}
```

Save the returned `task_handle`. Poll, follow up when `followup_available` is true, or cancel:

```json
{"action":"status","task_handle":"<returned task_handle>"}
```

```json
{"action":"followup","task_handle":"<returned task_handle>","prompt":"Check one edge case and report the result.","request_key":"fix-test-followup-1"}
```

```json
{"action":"cancel","task_handle":"<returned task_handle>"}
```

A follow-up resumes the private native session with a new attempt; native handles are separate from consultation `continuation_id` values. Identical submissions with the same request key are deduplicated while retained. Changed input with that key returns `request_key_conflict`. Handles, session mapping, and deduplication are volatile and are lost on restart. See [native task usage](docs/native-cli-tasks.md) for states, bounds, and ownership rules.

## Programmatic generation

The examples below use the source installation's `.venv/bin`. For a standalone wheel, use `/absolute/path/to/clink-runtime/bin` instead. Install the checkout's console commands locally if needed:

```sh
.venv/bin/python -m pip install -e .
.venv/bin/clink generation-capabilities --json
```

Capabilities discovery is read-only: it does not launch a CLI, authenticate, or grant execution authority. It reports schemas 2 and 3 with operation-specific admission. Optional `--cli NAME --executable /absolute/path/to/executable` checks one local executable's identity without running it. Launch admission is checked again before execution.

Approve an existing project folder independently of MCP project setup:

```sh
.venv/bin/clink allow /absolute/path/to/your-project --cli grok
```

The command shows the resolved path and asks a default-No question; `--yes` explicitly preapproves it for scripts. It saves an `allowed_roots` entry in operator configuration and preserves existing settings. This does not grant native CLI trust or permissions. Generation does not discover project manifests from request paths.

Schema 3 example `request.json`, using an existing canonical project path:

```json
{
  "schema_version": 3,
  "request_id": "66f96fc9-969d-4c0e-ab92-e355fa0e7588",
  "execution_mode": "native",
  "project_dir": "/absolute/path/to/your-project",
  "cli": "grok",
  "model_selection": {"kind": "native_default"},
  "instructions": "Rewrite the supplied text for clarity. Return only the revision.",
  "input": "Explain the tradeoffs of a cache for this application.",
  "timeout_seconds": 120
}
```

```sh
.venv/bin/clink generate --request-json - < request.json
```

Generation reads one JSON request followed by EOF and writes one terminal JSON result. Schema 3 uses `model_selection`: Codex and Claude require `{"kind":"explicit","id":"<native model ID>"}`; Grok and Antigravity accept only `{"kind":"native_default"}`. Antigravity still refuses execution because its generation admission is unverified. Schema 2 preserves Codex/Claude's explicit `model` field. The older no-tools schema 1 profile remains refused.

Each accepted request makes at most one top-level CLI invocation, with no wrapper retry, resume, or fallback. Only `status: "completed"` with exit `0` is ordinary success; validate the result's request identity and hashes before using its text. Completion validates final-response transport, not fulfillment. Requested/effective model selection is distinct from CLI-reported actual identity, which remains `null` when unreported. See [generation setup](docs/generation-native-setup.md), [schema 2 protocol](docs/generation-native-protocol.md), and [schema 3 and family discovery](docs/generation-native-families.md).

## Tools and configuration

All 18 tools are enabled unless listed in `DISABLED_TOOLS`; `version` and `listmodels` remain available.

| Tools | Purpose |
| --- | --- |
| `chat`, `clink` | Advisory consultations; `clink` also provides explicit native tasks. |
| `thinkdeep`, `codereview`, `precommit`, `debug`, `secaudit`, `analyze`, `refactor`, `testgen` | Caller-driven investigation with final expert-call conditions. |
| `consensus` | Independent consultations for caller synthesis, sequential by default with explicit parallel mode. |
| `apilookup` | Verified native search with structured sources. |
| `planner`, `docgen`, `tracer`, `challenge` | Local workflow guidance. |
| `listmodels`, `version` | Local configuration discovery and provenance. |

Manifest precedence is bundled defaults → legacy `~/.pal/cli_clients/` → user `~/.clink/cli_clients/` → explicitly bound project `.clink/cli_clients/` → `CLI_CLIENTS_CONFIG_PATH`. Each later manifest replaces the entire named client. Consultation and native task model defaults are separate; generation has its own request and admission contracts. See [CLI configuration](docs/cli-configuration.md).

**Risk & Mitigation:** Native execution can change files or external systems before failure or cancellation. Approved roots constrain project selection and supplied context, not all native authority. Cancellation does not roll back effects or guarantee detached/remote work stopped. Inspect actual changes and independently run checks before accepting work. Clink preserves native permission controls and provides no approval bypass or relay. See [security policy](SECURITY.md).

## Development

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v -m "not integration and not real_cli and not private_evidence"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
.venv/bin/python -m build
```

Ordinary verification uses synthetic executables and makes no live model calls. Historical live verification requires the private evidence and approval process; the public verification scripts do not submit model requests. See [release evidence](docs/release-evidence.md) and [changelog](CHANGELOG.md). Report bugs in this fork's [issue tracker](https://github.com/acebytes/clink-mcp-server/issues), excluding secrets and private transcripts.
