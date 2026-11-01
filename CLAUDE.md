# Development guide for clink-mcp-server

Read [AGENTS.md](AGENTS.md) and `CLAUDE.local.md` if present. This is an advisory CLI-only fork of PAL MCP Server, preserving its 18 MCP workflows and caller-owned implementation. Copyright 2025 Beehive Innovations; retain [Apache 2.0](LICENSE), attribution, and source provenance.

## Setup and start

Use Python 3.10+; Python 3.12 is the tested runtime.

```sh
# Explicitly create .venv and install runtime requirements.
./run-server.sh --setup

# Launch the MCP stdio server without installing or rewriting configuration.
./run-server.sh

# Show usage without setup.
./run-server.sh --help
```

The launcher uses the checkout's `.venv/bin/python` and starts from the checkout directory. It never creates or refreshes `.env`, copies credentials, changes native CLI settings, or registers the server with an MCP client. Optional `.env` settings are loaded by the server, retaining the documented `PAL_MCP_FORCE_ENV_OVERRIDE` behavior. Native CLIs retain their login stores.

Configure workspace roots explicitly in operator manifests before supplying paths outside the checkout. See [getting started](docs/getting-started.md), [CLI configuration](docs/cli-configuration.md), and [migration](docs/migration.md). The default target is `codex` / `default`; concrete models are native identifiers. All tools are enabled unless listed in `DISABLED_TOOLS`, except essential discovery remains available.

## Checks

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v -m "not integration"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

Run focused tests while editing, then the nonintegration suite before a commit. Use check-only formatting for verification; make intended formatting changes explicitly. The legacy provider simulator and hosted-model integration commands do not establish CLI coverage.

Use synthetic fixtures/executables for ordinary tests. Live CLI policy checks require explicit opt-in and established protective controls before model calls; they may consume usage. Inspect the managed helper's usage first:

```sh
.venv/bin/python scripts/verify_cli_policy.py --help
```

Preserve prior failed attempts and genuine receipts. Installation/help, an operator-reported login, or a zero exit is insufficient to admit execution. See [admission evidence](docs/clink-cli-admission.md) and the exact adapter policy. Do not add bypass flags or automatic retries when verification fails.

## Runtime diagnostics

```sh
tail -n 100 logs/mcp_server.log
tail -f logs/mcp_activity.log
rg 'ERROR|WARNING' logs/mcp_server.log
```

`server.py` owns file rotation. Keep diagnostics metadata-only and redact sensitive fields. Do not include credential values, user content, prompts, argv, or raw session/receipt bodies in routine reports. Restart the MCP server/client connection after code/configuration changes; its bounded in-memory continuations are lost on restart.

## Code map and guardrails

- `server.py` owns MCP transport and per-call tool construction.
- `tools/` owns schemas and caller-driven workflow transitions; intermediate local steps must not launch a CLI.
- `clink/registry.py` and `clink/models.py` validate an immutable operator configuration snapshot.
- `clink/backend.py`, `clink/policy.py`, `clink/runner.py`, adapters, and parsers own launch policy, limits, cleanup, and terminal result validation.
- `utils/` owns bounded continuation storage, per-tool state, locks, and declared-file change detection.
- `systemprompts/` and `conf/cli_clients/` supply subordinate role expertise and safe defaults.

Project edits remain with the caller. Preserve safe argv/environment construction, no inherited hooks/MCP/plugins, no recursive delegation, no automatic retry/fallback, explicit root checks, and POSIX process-group cleanup. Native read access can be broader than server context roots; native auth/cache maintenance may write operational files. Do not claim stronger isolation than the installed-version evidence proves.

The [historical audit](docs/clink-only-audit.md) and [historical baseline](docs/clink-only-baseline.md) describe upstream PAL. The [approved design](docs/clink-only-design.md) and [implementation plan](docs/clink-implementation-plan.md) record intended CLI-only behavior; current code, tests, and genuine verification evidence determine implemented support.
