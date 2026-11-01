# Repository Guidelines

See `requirements.txt` and `requirements-dev.txt`

Also read CLAUDE.md and CLAUDE.local.md if available.

## Project Structure & Module Organization
`clink-mcp-server` is a CLI-only fork of PAL MCP Server. `server.py` exposes all 18 MCP tools; existing analytical workflows remain caller-driven. `clink/backend.py` handles advisory consultations through the registry, policy, bounded runner, and parsers. Explicit `clink` native actions route to `clink/tasks.py` and `clink/native.py`, with separate profiles, private native sessions, and admission. `clink/native_adapters.py` owns adapter argv, input transport, session validation, and parser dispatch; every native adapter reuses the task manager and shared process pool. Shared PAL continuation/workflow state lives in `utils/` and must not be reused for native tasks.

Prompts are under `systemprompts/`, bundled CLI manifests under `conf/cli_clients/`, and tests under `tests/`. Current setup/configuration docs are `docs/getting-started.md`, `docs/cli-configuration.md`, `docs/native-cli-tasks.md`, and `docs/migration.md`. Historical PAL audit/baseline/provider documents are provenance, not current execution guidance. Preserve Apache 2.0 and Copyright 2025 Beehive Innovations attribution.

## Build, Test, and Development Commands
Use Python 3.10+; Python 3.12 is the tested runtime.

Project configuration is generated separately with `.venv/bin/python -m clink.setup --project /absolute/project`. It creates `.clink/` manifests/prompts and project `.mcp.json`; `./run-server.sh --setup` remains dependency installation only. `./run-server.sh --project /absolute/project` explicitly binds one server to that project, preserving installation cwd/logging/`.env`. The local Claude plugin under `integrations/claude` exposes `/clink:setup` when loaded directly from the checkout. See `docs/project-setup.md`.

```sh
# Explicit setup only: creates .venv if absent and installs runtime requirements.
./run-server.sh --setup

# Ordinary MCP stdio launch; does not install packages or rewrite configuration.
./run-server.sh

# Development dependencies and nonintegration verification.
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_clink_backend.py -q
.venv/bin/python -m pytest tests/test_native_adapters.py tests/test_native_runtime.py tests/test_native_fixture_trust.py -q
.venv/bin/python -m pytest tests/ -v -m "not integration"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

Do not use old `.pal_venv` instructions or automatic provider installers. Live CLI probes are separate opt-in checks, may incur usage, and require established protective controls before a model call. Read the appropriate managed helper's `--help`: `scripts/verify_cli_policy.py` for advisory policy, `scripts/verify_native_cli.py` for Claude native, or `scripts/verify_native_adapters.py` for Grok/Agy/Codex candidates. Keep all prior failure receipts and consumed submission history; do not fabricate admission or retry silently.

## Coding Style & Naming Conventions
Target Python 3.10+ with Black and isort using a 120-character line limit; Ruff enforces pycodestyle, pyflakes, bugbear, comprehension, and pyupgrade rules. Prefer explicit type hints, snake_case modules, and imperative commit-time docstrings. Extend workflows by defining hook or abstract methods instead of checking `hasattr()`/`getattr()`—inheritance-backed contracts keep behavior discoverable and testable.

## Testing Guidelines
Mirror production modules inside `tests/` and name tests `test_<behavior>` or `Test<Feature>` classes. Run `.venv/bin/python -m pytest tests/ -v -m "not integration"` before every commit; add coverage for coverage-sensitive changes. Use synthetic executables/fixtures for parser, runner, state, cancellation, and error cases. Local discovery must work without provider credentials or installed CLIs. Do not count retired hosted-provider simulator scenarios as CLI coverage.

Live CLI checks are explicitly opt-in and use disposable synthetic workspaces. Record installed identity, recipe, actual model when reported, permissions, and cancellation evidence. Consultation checks establish capability suppression; separate native checks establish intended configuration, hook/tool/subagent/MCP behavior, permissions, native follow-up, partial effects, and cleanup. Neither profile's receipts admit the other. Candidate verifier policies are process-local and never admit production automatically. Grok fixture trust enrollment requires the verifier's explicit `--trust-grok-fixture` option and must retain unrelated trust bytes, remove only its own unchanged grant, and preserve refused-cleanup evidence. Keep credentials, user records, prompts, and raw session bodies out of tracked evidence and logs. Relevant runtime diagnostics are rotated under `logs/`; report bounded technical excerpts only.

## Commit & Pull Request Guidelines
Follow Conventional Commits: `type(scope): summary`, where `type` is one of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, or `chore`. Keep commits focused, referencing issues or simulator cases when helpful. Pull requests should outline intent, list validation commands executed, flag configuration or tool toggles, and attach screenshots or log snippets when user-visible behavior changes.

## GitHub CLI Commands
The GitHub CLI (`gh`) streamlines issue and PR management directly from the terminal.

### Viewing Issues
```bash
# View issue details in current repository
gh issue view <issue-number>

# View issue from specific repository
gh issue view <issue-number> --repo owner/repo-name

# View issue with all comments
gh issue view <issue-number> --comments

# Get issue data as JSON for scripting
gh issue view <issue-number> --json title,body,author,state,labels,comments

# Open issue in web browser
gh issue view <issue-number> --web
```

### Managing Issues
```bash
# List all open issues
gh issue list

# List issues with filters
gh issue list --label bug --state open

# Create a new issue
gh issue create --title "Issue title" --body "Description"

# Close an issue
gh issue close <issue-number>

# Reopen an issue
gh issue reopen <issue-number>
```

### Pull Request Operations
```bash
# View PR details
gh pr view <pr-number>

# List pull requests
gh pr list

# Create a PR from current branch
gh pr create --title "PR title" --body "Description"

# Check out a PR locally
gh pr checkout <pr-number>

# Merge a PR
gh pr merge <pr-number>
```

Install GitHub CLI: `brew install gh` (macOS) or visit https://cli.github.com for other platforms.

## Security & Configuration Tips
Native CLIs own authentication; the server does not require hosted-provider API keys. Never commit secrets or generated logs. The launcher must not copy credentials, refresh `.env`, register MCP clients, or change native CLI configuration. Optional server settings are documented in `.env.example`; manifests follow whole-client precedence in `docs/cli-configuration.md`.

Keep consultation policy enforced in the shared backend: no project edits, mutating shell tools, recursive delegation, inherited MCP, hooks/plugins, unsafe extra arguments, automatic retry, or provider fallback. Omitted `clink.action` remains consultation with `codex/default`. Explicit native task actions intentionally preserve configured tools, hooks, skills, plugins, MCP integrations, and subagents under native permissions; do not reintroduce global advisory bans into native prompts or weaken the separate Codex profile.

Native launch requires its own installed-version admission; implementation, installation, and operator-reported login do not establish it. Claude Code 2.1.304 has [separate native admission](docs/verification/native-claude-2.1.304-admission.json) for the exact recorded binary/recipe; mismatches fail closed and require reverification. Grok 1.0.70 has [separate native admission](docs/verification/native-grok-1.0.70-admission.json) for its exact recorded Darwin arm64 binary/recipe, with observed `grok-4.7-build` execution. Its [compatibility review](docs/native-grok-admission-review.md) preserves nine top-level invocations and ordinal8’s unknown `uncertain/native_failed` cause; later cancellation passed without diagnosing that cause. Antigravity 1.4.12 has [separate native admission](docs/verification/native-agy-1.4.12-admission.json) for its exact Darwin arm64 binary/recipe; all four lifecycle gates passed, actual model is unknown, and child read/lifecycle proof does not verify returned-token content. Native Codex remains disabled because its supported output omits a genuine prelaunch refusal; see the [diagnosis](docs/native-codex-attempt5-diagnosis.md). Codex's admitted advisory recipe/catalog and the admitted Claude/Grok/Agy native recipes must remain unchanged by candidate work. Preserve prior failed/partial attempts and the limits in [native lifecycle results](docs/native-lifecycle-results.md).

Keep adapter-specific input transports intact: Grok uses a bounded private `0600` prompt file in a `0700` directory with empty stdin; Agy receives one user NDJSON event and EOF; Codex uses explicit stdin `-` through `exec`/`exec resume`. Private input resources must survive process supervision and be removed on controlled exit/cancellation. Preserve exact private session binding, request-key deduplication, task/attempt/session/PAL identity separation, overlapping-project ownership, bounded volatile retention, and shutdown cancellation. Recognized native denials return `permission_needed`; errors/incomplete output must not establish completion. Preserve native permission semantics without bypasses or wrapper approval overrides. Do not add automatic resend/resume/retry, provider fallback, PTYs, approval relays, or persistence within this MVP. Native subagents are allowed; the depth marker cannot police recursion through an already-running MCP server.

Risk & Mitigation: native tasks can mutate files and external systems before failure or cancellation. Approved roots constrain project selection and supplied context, not all native authority. Cancellation does not roll back effects, and detached or remote work can outlive process-group cleanup. `completed` is execution status, not proof of fulfillment. Changes to authority, state, cleanup, or bounds require focused regression coverage; keep logs metadata-only and results bounded.
