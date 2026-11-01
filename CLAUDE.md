# Development guide for clink-mcp-server

Read [AGENTS.md](AGENTS.md) and `CLAUDE.local.md` if present. This CLI-only fork of PAL MCP Server preserves its 18 MCP workflows and advisory consultation defaults, with separate explicit native task actions for delegated execution. Copyright 2025 Beehive Innovations; retain [Apache 2.0](LICENSE), attribution, and source provenance.

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

Configure workspace roots explicitly in operator manifests before supplying paths outside the checkout. See [getting started](docs/getting-started.md), [CLI configuration](docs/cli-configuration.md), [native tasks](docs/native-cli-tasks.md), and [migration](docs/migration.md). Consultation defaults to `codex` / `default`; native start requires explicit CLI/project selection and independent admission. Concrete models are native identifiers, with native-task defaults configured separately. All tools are enabled unless listed in `DISABLED_TOOLS`, except essential discovery remains available.

## Checks

For each repository, `.venv/bin/python -m clink.setup --project /absolute/project` generates project manifests, copied prompts, and `.mcp.json` registration. Project manifests override user manifests only with explicit server `--project` binding; `CLI_CLIENTS_CONFIG_PATH` remains highest priority. Setup preserves custom files and makes no native calls. `run-server.sh --setup` still installs dependencies only. The checkout-local plugin under `integrations/claude` exposes `/clink:setup`; see [project setup](docs/project-setup.md). Never infer trusted configuration from task request paths or rebind an already initialized server.

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_native_adapters.py tests/test_native_runtime.py tests/test_native_fixture_trust.py -q
.venv/bin/python -m pytest tests/ -v -m "not integration"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

Run focused tests while editing, then the nonintegration suite before a commit. Use check-only formatting for verification; make intended formatting changes explicitly. The legacy provider simulator and hosted-model integration commands do not establish CLI coverage.

Use synthetic fixtures/executables for ordinary tests. Live CLI policy checks require explicit opt-in and established protective controls before model calls; they may consume usage. Inspect the managed helper's usage first:

```sh
.venv/bin/python scripts/verify_cli_policy.py --help
.venv/bin/python scripts/verify_native_cli.py --help
.venv/bin/python scripts/verify_native_adapters.py --help
```

Preserve prior failed attempts, consumed submissions, and genuine receipts. Installation/help, an operator-reported login, or a zero exit is insufficient to admit execution. See [admission evidence](docs/clink-cli-admission.md) and the exact profile policy. The first helper verifies advisory suppression; [Claude Code 2.1.304 native admission](docs/verification/native-claude-2.1.304-admission.json) is separate and binds the exact recorded binary/recipe. [Grok 1.0.70 native admission](docs/verification/native-grok-1.0.70-admission.json) enables only the exact recorded Darwin arm64 binary/recipe, with observed `grok-4.7-build` execution. Its [compatibility review](docs/native-grok-admission-review.md) retains all nine top-level invocations and ordinal8’s unknown `uncertain/native_failed` cause; a later cancellation passed without proving that cause resolved. Antigravity 1.4.12 has [separate native admission](docs/verification/native-agy-1.4.12-admission.json) for its exact Darwin arm64 binary/recipe, with all four lifecycle gates passed and actual model unknown. Its child read/lifecycle proof does not verify returned-token content. Native Codex remains disabled for the [supported-output refusal gap](docs/native-codex-attempt5-diagnosis.md). The candidate harness uses process-local verification policies and cannot enable production automatically. Changes to admitted binaries/recipes require reverification and fail closed. See [native lifecycle results](docs/native-lifecycle-results.md) for the original genuine passing, failed, and partial attempts. Do not add bypass flags or automatic retries when verification fails.

## Runtime diagnostics

```sh
tail -n 100 logs/mcp_server.log
tail -f logs/mcp_activity.log
rg 'ERROR|WARNING' logs/mcp_server.log
```

`server.py` owns file rotation. Keep diagnostics metadata-only and redact sensitive fields. Do not include credential values, user content, prompts, argv, or raw session/receipt bodies in routine reports. Restart the MCP server/client connection after code/configuration changes; PAL continuations and native task records/session mappings are volatile and lost on restart. Shutdown cancels server-owned native work and awaits cleanup; it does not roll back prior effects.

## Code map and guardrails

- `server.py` owns MCP transport, per-call tool construction, native routing before PAL bookkeeping, and server-lifetime cleanup.
- `tools/` owns schemas and caller-driven workflow transitions; intermediate local steps must not launch a CLI.
- `clink/registry.py` and `clink/models.py` validate an immutable operator configuration snapshot.
- `clink/backend.py`, `clink/policy.py`, `clink/runner.py`, adapters, and parsers own consultation policy, shared process limits, cleanup, and terminal validation.
- `clink/tasks.py` owns bounded task/attempt records, request-key deduplication, project/session ownership, and cancellation; `clink/native.py` and `clink/native_policy.py` own shared native admission/configuration and execution.
- `clink/native_adapters.py` owns adapter argv, session validation, parser dispatch, and input transport: Claude text stdin, Grok a private prompt file, Agy one user NDJSON event, and Codex stdin `-` through `exec`/`exec resume`. All use the same task manager and process pool.
- `utils/` owns bounded continuation storage, per-tool state, locks, and declared-file change detection.
- `systemprompts/` and `conf/cli_clients/` supply subordinate role expertise and safe defaults.

Consultations return proposed edits to the caller and retain their restrictions on tools, inherited hooks/MCP/plugins, and recursive delegation. Explicit native tasks may edit/test and use configured tools, hooks, skills, plugins, MCP integrations, and subagents under native permissions. Keep native argv, environment-name inheritance, role prompts, model defaults, session mapping, and admission separate from the pinned Codex consultation recipe. Candidate work must preserve that recipe/catalog and the admitted Claude/Grok/Agy native recipes. No permission bypass, wrapper approval override, or approval relay is provided; recognized headless denials return `permission_needed`, while terminal errors/incomplete signals cannot establish completion.

Keep Grok task text out of argv/stdin: its bounded `0600` prompt file belongs in a private `0700` directory and survives until shared process cleanup finishes. Remove private resources on controlled completion, failure, queue rejection, and cancellation. Grok additionally retains configured `GROK_HOME`; other integration environment names remain operator-owned explicit inheritance. The verifier's optional `--trust-grok-fixture` enrollment is limited to its marked synthetic Git root, preserves unrelated trust entries/comments, and removes only its own unchanged grant. Normal task submission never performs this enrollment.

Risk & Mitigation: native state and side effects require request-key deduplication, serialized session input, overlapping canonical project ownership, and bounded retention. Preserve safe argv construction, explicit root checks, no automatic resend/retry/fallback, and POSIX process-group cleanup. Native authority can extend beyond context roots; detached descendants and remote work may survive cleanup. Task `completed` does not verify fulfillment, and cancellation cannot undo partial effects.

The [historical audit](docs/clink-only-audit.md) and [historical baseline](docs/clink-only-baseline.md) describe upstream PAL. The [earlier advisory design](docs/clink-only-design.md) and [implementation plan](docs/clink-implementation-plan.md) retain their original scope; the [native lifecycle plan](docs/native-cli-lifecycle-plan.md) records the explicit native-task direction; current code, tests, and genuine verification evidence determine implemented support.
