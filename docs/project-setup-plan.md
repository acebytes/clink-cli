# Project-scoped setup implementation plan

Baseline: `dcc1825e1b024106c7409b7f76a5518644d22589`. Requested 2026-11-01: provide `clink:setup` that generates configuration inside a project, overriding optional user configuration and supporting multiple repositories from one installation.

The dedicated planning agent proposed this design with explicit Astra/ultra selection; effective runtime metadata was unavailable. Standing user authorization covers routine implementation, bounded scope decisions, verification repairs, and independent review. This repository has no `.flow/` tracker.

## Accepted design

1. Add explicit server/launcher `--project` binding, captured before lazy registry/backend initialization. Canonicalize an existing directory, reject different rebinding, and preserve existing unbound behavior, installation cwd, `.env`, logging, and dependency-only `--setup`. Do not infer configuration from request paths or parent searches.
2. Load bundled → legacy user → user → bound project's `.clink/cli_clients` → explicit `CLI_CLIENTS_CONFIG_PATH`. Retain whole-client replacement, atomic validation, sorted loading, and startup-cwd anchoring for relative explicit-source paths. Bound project supplies default cwd/roots when omitted.
3. Add `python -m clink.setup --project PATH`, defaulting to invocation cwd. Generate all bundled clients unless narrowed with repeated `--cli`. Create complete manifests and copied prompts with portable `../..` roots and `../prompts/` references. Preserve all bundled roles, model defaults, arguments, and native enablement. Read bundled templates only; no credentials, user registry, server import, native probes, or new console entrypoint.
4. Create/merge `.mcp.json` registration using the current interpreter and installed `server.py --project <canonical-root>`. Preserve the virtualenv interpreter path without resolving its symlink. Preserve unrelated JSON values; an existing differing clink entry conflicts. Registration paths are machine-local.
5. Preflight all outputs. Identical files are no-ops; custom files, symlink destinations, malformed/duplicate/oversized JSON, and unknown clients fail. Publish complete files atomically without replacing newly appearing destinations, registration last. Serialize setup invocations and detect concurrent changes before registration replacement. Retain valid partial outputs on failure; no force flag, rollback deletions, retry, or update ledger.
6. Provide a checkout-local Claude plugin at `integrations/claude`: plugin name `clink`, skill `setup`, explicit invocation only. It captures the user's project before switching to the installation to invoke the helper. No new MCP action, marketplace package, client approval changes, server restart, or model call.

## Implementation sequence

1. Registry, server, and launcher binding with precedence/startup regressions.
2. Scaffolder and registration with preservation/failure tests.
3. Plugin, documentation, distribution checks, full isolated suite, and independent Sol 6.1/high review.

No preparatory refactor is required. Preserve native admission, pinned recipes/resources, credentials, task lifecycle, and PAL continuation behavior.

## Acceptance matrix

| Area | Required evidence |
| --- | --- |
| Registry | Project-over-user and explicit-over-project precedence; whole-client replacement; absent layer; invalid layer failure; failed reload retains snapshot; relative explicit-source compatibility. |
| Isolation | Two project bindings select independent roots/configuration; later cwd/request changes do not switch either; failed initialization is not cached; different rebinding rejected. |
| Generation | All/selected clients; complete advisory/native roles; disabled native Codex retained; prompt bytes preserved; relocated manifests validate. |
| Preservation | Identical rerun; custom files/registration conflict; unrelated MCP entries retained; user settings/credentials never copied. |
| Failure | Malformed/duplicate/oversized JSON; symlink destinations; newly appearing outputs; detected concurrent edits; lock contention; induced partial publication; bounded diagnostics. |
| Startup/package | Original launcher behavior; project paths with spaces; installed setup/server binding; unchanged console mapping; required wheel/sdist resources retained. |
| Verification | Focused regressions; full network/home-isolated suite; check-only Ruff/Black/isort; independent read-only review. |

## Risks and limits

Project manifests can select executables/native role guidance. Explicit startup binding prevents arbitrary request paths from changing trusted configuration; native admission and permission controls remain unchanged. Roots constrain project selection/context, not all native authority.

File publication is atomic per file, not across the whole operation. Failed setup can leave valid files and an explicit rerun resumes. Portable Python offers no compare-and-swap for existing `.mcp.json`: locking coordinates setup instances, and rechecking detects observed external changes, but an unrelated edit in the final check-to-replace interval remains unsupported. Do not claim stronger protection or create credential-bearing backups.

Separate project processes have separate task records/concurrency limits. External native writers remain outside task ownership coordination. Restart loses volatile handles. Synthetic setup verification does not establish Claude UI acceptance or native model execution.
