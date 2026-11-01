# clink-mcp-server

An advisory CLI orchestration server that preserves PAL's 18 MCP tools and routes delegated analysis through installed, policy-verified AI CLIs. The calling assistant investigates, edits files, advances workflows, and synthesizes results. Delegated CLIs return advice and proposed code as text.

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

Ensure the client's `PATH` can resolve your native CLI, or configure its absolute executable path in a CLI manifest. Configure workspace roots before passing project paths outside this checkout. See [getting started](docs/getting-started.md), [CLI configuration](docs/cli-configuration.md), and [migration from PAL](docs/migration.md).

## Selection and availability

Delegated tools default to `cli_name: "codex"` and `role: "default"`. A concrete `model` is passed as a native CLI identifier. Model selection follows request → role → client default → the admitted adapter's native default; omitted, `null`, or `"auto"` selects the next default. Provider aliases and catalogs are not used. Requested and reported model identities are separate, and absent actual identity remains unknown.

Call `version` and `listmodels` first. Discovery lists configured clients, roles, executable presence, verification status, model defaults, file-read scope, and capabilities without logging in or calling a model. Executable presence alone does not admit execution.

| CLI | Policy availability |
| --- | --- |
| Codex 0.166.0 | Text, explicit local images, and structured native search admitted for the pinned macOS arm64 binary, restrictive recipe, and frozen model catalog. |
| Claude 2.1.304 | Unavailable with `policy_unverified`; managed hooks/policy and live authority gates remain unresolved. |
| Gemini | Unavailable until version-specific policy controls and live tests pass. |
| Antigravity (`agy`) / xAI Grok Build (`grok`) | Unavailable with `policy_unverified`; inherited hooks/MCP and sandbox controls have unresolved gates. |

The [accepted Codex admission](docs/verification/cli-policy/codex-0.166.0-admission-v5-search.json) binds the exact version, executable, restrictive recipe, and bundled model catalog by SHA-256. It covers the tested macOS arm64 binary; other builds/versions are not admitted by that record. The [verification report](docs/verification/cli-policy/README.md) preserves failed attempts and limits. Historical v1/v2 acceptances were invalidated for backend usability; corrected text/image probes and a real structured-source lookup support the current admission. [Antigravity/Grok findings](docs/clink-cli-admission.md) are a separate investigation.

The Codex catalog/default snapshot is frozen until an explicitly verified refresh. It accepts only the 12 exact native IDs listed in the admission record; unknown names and prefix/namespace aliases return `unsupported_capability` before launch. With no request, role, or client model, the captured native default `gpt-6.1-sol` is passed explicitly and reported as `effective_model`, with `default_origin: "pinned_native_catalog"`. Only `gpt-6.1-sol` was exercised live; `actual_model` remains unknown unless reported by the CLI. This is native-ID selection, not provider alias translation.

Codex receives bounded inline text and admitted images; native file/command tools are disabled. The native read-only profile can still read beyond the workspace, so this is not OS read isolation. Native search is verified from completed search records with actual returned structured source URLs; model prose and requested/open-page URLs do not establish search results. The backend has no automatic fallback or model retry.

For example, the `clink` tool accepts:

```json
{"prompt":"Review the declared change and return findings.","cli_name":"codex","role":"codereviewer"}
```

Chat retains its required `working_directory_absolute_path`; that directory and explicit file paths must be within operator-configured roots. A request cannot expand those roots. Code generation returns content and does not create project files such as `pal_generated.code`.

## Tools and continuity

All **18 tools are enabled** unless listed in `DISABLED_TOOLS`. `version` and `listmodels` remain available.

| Tools | Behavior |
| --- | --- |
| `chat`, `clink` | One advisory CLI consultation. |
| `thinkdeep`, `codereview`, `precommit`, `debug`, `secaudit`, `analyze`, `refactor`, `testgen` | Preserve caller-driven investigation; consult an expert at the final hook when requested. |
| `consensus` | Sequential consultations by default, with explicit parallel mode. Consultants receive the original proposal and declared context; the caller synthesizes. |
| `apilookup` | One verified native search lookup returning structured sources; recognized empty results return `no_results`. No automatic follow-up or fallback. |
| `planner`, `docgen`, `tracer`, `challenge` | Local caller-driven guidance with no delegated model call. |
| `listmodels`, `version` | Local discovery and provenance. |

Server continuations preserve bounded conversation and separate per-tool workflow state across calls. They are process-local and disappear on restart or expiry. They are not native CLI session IDs. Consensus consultants do not receive conversation replay, caller findings, or peer answers; declared file changes invalidate their aggregate.

## Execution boundaries

Delegation requires enforced advisory controls: no project edits, mutating shell tools, recursive delegation, inherited MCP, or hooks/plugins. The runner filters child environments, bounds prompts/output and concurrency, and terminates process groups on timeout or cancellation. Native login/cache maintenance may still write operational files, and native file-read access may extend beyond the configured context roots. Roots limit the context supplied by this server; they do not claim complete child read isolation.

The default pool permits two running processes and eight queued launches. Requests have a 1,800-second overall deadline, including lock/queue waits; queue wait is capped at 30 seconds. Capture is capped at 10 MiB per stream and returned delegated text at 20,000 characters, with truncation metadata. A failed request is not automatically retried; a new explicit request may incur usage. Native CLIs can retry internally, and cancellation cannot undo remote work already submitted.

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

See the [CLI-only implementation results](docs/clink-implementation-results.md) for integrated verification and current limits. The [historical PAL audit](docs/clink-only-audit.md) and [historical baseline](docs/clink-only-baseline.md) describe the pinned upstream implementation and its earlier test environment, not final CLI-only verification. The [approved design](docs/clink-only-design.md) and [implementation plan](docs/clink-implementation-plan.md) record the fork's intended contracts. Real CLI policy tests require explicit opt-in and can use native subscription/API quota. Existing PAL documents that describe hosted providers are historical; use the configuration and migration guides above for this fork.
