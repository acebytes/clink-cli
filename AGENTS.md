# Repository Guidelines

See `requirements.txt` and `requirements-dev.txt`

Also read CLAUDE.md and CLAUDE.local.md if available.

## Project Structure & Module Organization
`clink-mcp-server` is an advisory CLI-only fork of PAL MCP Server. `server.py` exposes all 18 MCP tools; `tools/` preserves their schemas and caller-driven workflows. `clink/backend.py` resolves immutable requests through the strict manifest registry, adapter policy, bounded subprocess runner, and parsers. Shared continuation and workflow state lives in `utils/`.

Prompts are under `systemprompts/`, bundled CLI manifests under `conf/cli_clients/`, and tests under `tests/`. Current setup/configuration docs are `docs/getting-started.md`, `docs/cli-configuration.md`, and `docs/migration.md`. Historical PAL audit/baseline/provider documents are provenance, not current execution guidance. Preserve Apache 2.0 and Copyright 2025 Beehive Innovations attribution.

## Build, Test, and Development Commands
Use Python 3.10+; Python 3.12 is the tested runtime.

```sh
# Explicit setup only: creates .venv if absent and installs runtime requirements.
./run-server.sh --setup

# Ordinary MCP stdio launch; does not install packages or rewrite configuration.
./run-server.sh

# Development dependencies and nonintegration verification.
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_clink_backend.py -q
.venv/bin/python -m pytest tests/ -v -m "not integration"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

Do not use old `.pal_venv` instructions or automatic provider installers. Native CLI policy probes are separate opt-in checks, may incur usage, and require established protective controls before a model call. Read `scripts/verify_cli_policy.py --help` through the managed Python before an authorized probe. Keep all prior failure receipts; do not fabricate admission or retry silently.

## Coding Style & Naming Conventions
Target Python 3.10+ with Black and isort using a 120-character line limit; Ruff enforces pycodestyle, pyflakes, bugbear, comprehension, and pyupgrade rules. Prefer explicit type hints, snake_case modules, and imperative commit-time docstrings. Extend workflows by defining hook or abstract methods instead of checking `hasattr()`/`getattr()`—inheritance-backed contracts keep behavior discoverable and testable.

## Testing Guidelines
Mirror production modules inside `tests/` and name tests `test_<behavior>` or `Test<Feature>` classes. Run `.venv/bin/python -m pytest tests/ -v -m "not integration"` before every commit; add coverage for coverage-sensitive changes. Use synthetic executables/fixtures for parser, runner, state, cancellation, and error cases. Local discovery must work without provider credentials or installed CLIs. Do not count retired hosted-provider simulator scenarios as CLI coverage.

Live CLI checks are explicitly opt-in, use disposable synthetic workspaces, and must record the installed version, policy recipe, actual model when available, no-write/hook/MCP results, and cancellation evidence. Keep credentials, user records, prompts, and raw session bodies out of tracked evidence and logs. Relevant runtime diagnostics are rotated under `logs/`; report bounded technical excerpts only.

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

Keep advisory policy enforced in the shared backend: no project edits, mutating shell tools, recursive delegation, inherited MCP, hooks/plugins, unsafe extra arguments, automatic retry, or provider fallback. Installed binaries and operator-reported login do not prove admission. Keep unverified adapters unavailable. Approved roots constrain server-supplied context, not every native filesystem read; disclose read scope honestly. Changes to authority, state, subprocess cleanup, or bounds require focused regression coverage.
