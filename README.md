# clink-mcp-server

A CLI orchestration server that preserves PAL's 18 MCP tools. Existing consultations return advice and proposed code as text; explicit `clink` native task actions can delegate implementation using the selected CLI's configured capabilities and permissions. Claude Code 2.1.304 native task admission is verified for the exact recorded binary and launch recipe; the admitted Codex consultation profile remains separate.

Derived from [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server), formerly Zen MCP. **Copyright 2025 Beehive Innovations.** This fork preserves the upstream attribution and [Apache 2.0 license](LICENSE).

## Start from this checkout

Use a POSIX environment on macOS or Linux and a Python interpreter compatible with [requirements.txt](requirements.txt). Python 3.10+ is required; Python 3.12 is the tested runtime. Native Windows delegation is unavailable until equivalent process-tree cleanup is verified.

```sh
./run-server.sh --setup
./run-server.sh
```

`--setup` creates `.venv` when needed and installs the runtime requirements, then exits. Ordinary startup uses `.venv/bin/python` without installing anything. The server communicates over stdio and normally runs under an MCP client; it may appear quiet when started directly.

The launcher does not create or refresh `.env`, copy credentials, register MCP servers, or change native CLI configuration. Optional server settings are listed in [.env.example](.env.example). The server does not require hosted-provider API keys. Authenticate a supported CLI using its own native login mechanism before requesting delegated work.

Add a server entry to your MCP client's configuration using the absolute checkout path:

```json
{
  "mcpServers": {
    "clink": {
      "command": "/absolute/path/clink-mcp-server/run-server.sh",
      "args": []
    }
  }
}
```

Ensure the client's `PATH` can resolve your native CLI, or configure its absolute executable path in a CLI manifest. Configure workspace roots before passing project paths outside this checkout. See [getting started](docs/getting-started.md), [CLI configuration](docs/cli-configuration.md), [native task lifecycle](docs/native-cli-tasks.md), and [migration from PAL](docs/migration.md).

## Selection and availability

Consultations default to `cli_name: "codex"` and `role: "default"`. Omitted `clink.action` and explicit `"consult"` retain that behavior. Native `"start"` requires explicit CLI selection and a project directory; it never inherits the consultation default. For consultations, a concrete `model` is passed as a native CLI identifier. Selection follows request → role → client default → the admitted adapter's native default; omitted, `null`, or `"auto"` selects the next default. Provider aliases and catalogs are not used. Requested and reported model identities are separate, and absent actual identity remains unknown.

Call `version` and `listmodels` first. Discovery lists configured clients, roles, executable presence, verification status, model defaults, file-read scope, and capabilities without logging in or calling a model. Executable presence alone does not admit execution.

| CLI | Policy availability |
| --- | --- |
| Codex 0.166.0 | Text, explicit local images, and structured native search admitted for the pinned macOS arm64 binary, restrictive recipe, and frozen model catalog. |
| Claude Code 2.1.304 | Native tasks admitted for the exact recorded binary/recipe; consultation remains `policy_unverified` under its separate advisory policy. |
| Gemini | Unavailable until version-specific policy controls and live tests pass. |
| Antigravity (`agy`) / xAI Grok Build (`grok`) | Parser/discovery support only; execution remains `policy_unverified`, with no native task admission. |

The [accepted Codex admission](docs/verification/cli-policy/codex-0.166.0-admission-v5-search.json) binds the exact version, executable, restrictive recipe, and bundled model catalog by SHA-256. It covers the tested macOS arm64 binary; other builds/versions are not admitted by that record. The [verification report](docs/verification/cli-policy/README.md) preserves failed attempts and limits. Historical v1/v2 acceptances were invalidated for backend usability; corrected text/image probes and a real structured-source lookup support the current admission. [Antigravity/Grok findings](docs/clink-cli-admission.md) are a separate investigation.

The [Claude native admission](docs/verification/native-claude-2.1.304-admission.json) combines genuine attempts 2/3: file edits, a native failing-then-passing test, configured hooks/subagent, synthetic MCP environment inheritance, same-session follow-up, `permission_needed`, and cancellation after known mutation with the synthetic descendant gone. Attempt 1 and attempt 2's incomplete test/cancellation coverage remain preserved. This covers the recorded installed binary/recipe and synthetic project, not every user configuration, model, platform, or detached/remote cleanup case. A changed binary/recipe fails closed and needs reverification. See [native lifecycle results](docs/native-lifecycle-results.md).

The Codex catalog/default snapshot is frozen until an explicitly verified refresh. It accepts only the 12 exact native IDs listed in the admission record; unknown names and prefix/namespace aliases return `unsupported_capability` before launch. With no request, role, or client model, the captured native default `gpt-6.1-sol` is passed explicitly and reported as `effective_model`, with `default_origin: "pinned_native_catalog"`. Only `gpt-6.1-sol` was exercised live; `actual_model` remains unknown unless reported by the CLI. This is native-ID selection, not provider alias translation.

Codex receives bounded inline text and admitted images; native file/command tools are disabled. The native read-only profile can still read beyond the workspace, so this is not OS read isolation. Native search is verified from completed search records with actual returned structured source URLs; model prose and requested/open-page URLs do not establish search results. The backend has no automatic fallback or model retry.

For example, the `clink` tool accepts:

```json
{"prompt":"Review the declared change and return findings.","cli_name":"codex","role":"codereviewer"}
```

Chat retains its required `working_directory_absolute_path`; that directory and explicit file paths must be within operator-configured roots. A request cannot expand those roots. Consultation code generation returns content and does not create project files such as `pal_generated.code`.

## Tools and continuity

All **18 tools are enabled** unless listed in `DISABLED_TOOLS`. `version` and `listmodels` remain available.

| Tools | Behavior |
| --- | --- |
| `chat`, `clink` with omitted action or `action: "consult"` | One advisory CLI consultation. |
| `clink` with `start`, `status`, `followup`, or `cancel` | Explicit bounded native task lifecycle; launch requires separate native admission. |
| `thinkdeep`, `codereview`, `precommit`, `debug`, `secaudit`, `analyze`, `refactor`, `testgen` | Preserve caller-driven investigation; consult an expert at the final hook when requested. |
| `consensus` | Sequential consultations by default, with explicit parallel mode. Consultants receive the original proposal and declared context; the caller synthesizes. |
| `apilookup` | One verified native search lookup returning structured sources; recognized empty results return `no_results`. No automatic follow-up or fallback. |
| `planner`, `docgen`, `tracer`, `challenge` | Local caller-driven guidance with no delegated model call. |
| `listmodels`, `version` | Local discovery and provenance. |

Server continuations preserve bounded conversation and separate per-tool workflow state across calls. They are process-local and disappear on restart or expiry. They are not native CLI session IDs. Native tasks instead use opaque `task_handle` and per-submission `attempt_id` values; private native sessions are never exposed. See [task state and retention](docs/native-cli-tasks.md#state-and-retention). Consensus consultants do not receive conversation replay, caller findings, or peer answers; declared file changes invalidate their aggregate.

## Execution boundaries

Consultations retain enforced advisory controls: no project edits, mutating shell tools, recursive delegation, inherited MCP, or hooks/plugins. Explicit native tasks use a separate profile that intentionally preserves configured tools, hooks, skills, plugins, MCP integrations, and subagents under native permissions. It supplies no permission bypass, PTY, approval relay, or durable server task store. Unsupported headless approval needs return `permission_needed`.

Both profiles share bounded process supervision. Roots constrain project selection and server-supplied context; they do not establish native filesystem or external-tool isolation. Native tasks reserve one writer for overlapping canonical project directories and serialize native session input; external writers remain outside that coordination. Server shutdown cancels its active tasks and awaits cleanup.

The default pool permits two running processes and eight queued launches. Requests have a 1,800-second overall deadline, including lock/queue waits; queue wait is capped at 30 seconds. Capture is capped at 10 MiB per stream and returned delegated text at 20,000 characters, with truncation metadata. A failed request is not automatically retried; a new explicit request may incur usage. Native CLIs can retry internally, and cancellation cannot undo edits, external actions, or remote work already submitted. Native task `completed` describes terminal execution, not verified task fulfillment; agent-reported changes and checks remain unverified until independently checked. See [native task limits and risks](docs/native-cli-tasks.md).

## Development and provenance

The [17-tool live matrix](docs/verification/live-tools-2/README.md) and [separate actual lookup](docs/verification/native-search-1/README.md) cover all 18 tool outcomes, with local tools remaining local and source files unchanged. The lookup returned 20 structured source URLs. Subsequent URL/hostname/Unicode and literal `is_error` hardening was tested offline with separately recorded parser hashes and synthetic/source-shaped fixtures; the live call was not repeated under that final hardening, and no raw native-output replay is claimed.

Install development requirements separately, then run nonintegration tests and check-only formatting:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v -m "not integration"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

See the [CLI-only implementation results](docs/clink-implementation-results.md) for integrated verification and current limits. The [historical PAL audit](docs/clink-only-audit.md) and [historical baseline](docs/clink-only-baseline.md) describe the pinned upstream implementation and its earlier test environment, not final CLI-only verification. The [earlier advisory design](docs/clink-only-design.md) and [implementation plan](docs/clink-implementation-plan.md) retain their original scope. The [native lifecycle plan](docs/native-cli-lifecycle-plan.md) and [delegation handoff](docs/native-cli-delegation-handoff.md) record the newer explicit native-task direction; historical admission receipts do not admit that profile. Real CLI policy tests require explicit opt-in and can use native subscription/API quota. Existing PAL documents that describe hosted providers are historical; use the configuration and migration guides above for this fork.
