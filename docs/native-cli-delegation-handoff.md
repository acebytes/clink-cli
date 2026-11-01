# Handoff: native CLI delegation in clink

Date: 2026-10-31, America/Los_Angeles.

Confidence: high for inspected source behavior; proposed native execution compatibility remains unverified.

## Objective and decision status

The user clarified the primary objective:

> The main objective is to remodel clink to enable called agents to use whichever tools, subagents, hooks, etc already defined for that cli to enable called agents to better perform subcontracted tasks. The observability is a bonus that many users might be interested for transparency

The user then requested this handoff for the agent already working on `clink-mcp-server`.

The confirmed direction is native capability inheritance for subcontracted work, with optional human observability. Earlier discussion about PTYs and tmux was exploratory. A PTY, tmux, a browser viewer, particular MCP operation names, and a specific migration sequence have not been selected by the user.

This direction supersedes the earlier advisory-only objective wherever it prohibits the requested native tools, subagents, hooks, integrations, and task execution. Amend the design, prompts, configuration contracts, verification, and relevant project instructions together. Do not continue treating capability suppression as the product requirement merely because older documents and tests require it. Preserve historical evidence with its original scope.

## Checkout and concurrent work

Inspected repository: `/Users/d/Tools/clink-mcp-server`.

Snapshot before adding this handoff:

- Branch: `dex/agy-grok-support`.
- HEAD: `c7cdd3b5905c939a06dd87c295d9240b2da2b7c2`.
- Existing modified files: `clink/constants.py`, `clink/parsers/__init__.py`, `scripts/verify_distribution.py`, and `tests/test_uvx_resource_packaging.py`.
- Existing untracked work includes agy/grok parsers and manifests, `docs/verification/agy-grok-baseline/`, `docs/verification/agy-grok-discovery/`, synthetic parser fixtures/provenance, and additional CLI/parser tests.

That work belongs to the ongoing implementation. This handoff does not modify it, commit it, or establish its correctness. Refresh Git status and reread current source before editing; line references below describe the inspected snapshot. No `.flow/` directory was found; current design documents are under `docs/`.

## Verified implementation findings

Current data flow is `server.py → tool/workflow → CLIRequest → backend → bounded subprocess runner → adapter/parser → CLIResult`. Server continuations and per-tool workflow state live under `utils/`.

| Area | Current behavior and implication | Source |
| --- | --- | --- |
| Codex capabilities | Disables hooks, plugins, apps, subagents, shell execution, browser/computer tools, and skill discovery/loading. Overrides disable agents/MCP and suppress project instructions. Launch ignores user configuration/rules and uses ephemeral read-only execution. | [policy.py](/Users/d/Tools/clink-mcp-server/clink/policy.py:27) |
| Claude capabilities | Uses restricted/safe mode, a small read/search tool list, empty MCP configuration, disabled hooks/slash commands, denied permission prompts, and no session persistence. | [policy.py](/Users/d/Tools/clink-mcp-server/clink/policy.py:108) |
| Instructions | Backend prepends a prohibition on edits, mutating commands, agents, and MCP. The public tool and bundled role reinforce advisory-only work. Removing flags alone leaves contradictory instructions. | [backend.py](/Users/d/Tools/clink-mcp-server/clink/backend.py:373), [tools/clink.py](/Users/d/Tools/clink-mcp-server/tools/clink.py:30), [default.txt](/Users/d/Tools/clink-mcp-server/systemprompts/clink/default.txt:1) |
| Configuration and environment | Child environment uses a narrow allowlist. Native auth-location variables survive, but arbitrary integration variables cannot be supplied through manifests. Inference: configured hooks/MCP tools that depend on omitted variables may fail. Extra launch arguments are tightly restricted. | [policy.py](/Users/d/Tools/clink-mcp-server/clink/policy.py:12), [backend.py](/Users/d/Tools/clink-mcp-server/clink/backend.py:455), [registry.py](/Users/d/Tools/clink-mcp-server/clink/registry.py:194) |
| Process and conversation lifetime | Each consultation launches with stdin/stdout/stderr pipes, writes one prompt, closes stdin, and collects output until exit. Continuations replay server history; they expire and disappear on server restart. Native resume is deliberately excluded, and results exclude native session identities. | [runner.py](/Users/d/Tools/clink-mcp-server/clink/runner.py:108), [runner.py](/Users/d/Tools/clink-mcp-server/clink/runner.py:185), [backend.py](/Users/d/Tools/clink-mcp-server/clink/backend.py:436), [contracts.py](/Users/d/Tools/clink-mcp-server/clink/contracts.py:140), [design](/Users/d/Tools/clink-mcp-server/docs/clink-only-design.md:115) |
| Admission | Verification requires MCP exclusion, disabled hooks/plugins/subagents, and read-only enforcement. Runtime admission hashes the executable, launch recipe, and resources. Deleting flags invalidates existing admission. These checks verify the old objective. | [verify_cli_policy.py](/Users/d/Tools/clink-mcp-server/scripts/verify_cli_policy.py:89), [backend.py](/Users/d/Tools/clink-mcp-server/clink/backend.py:537) |
| Recursion | Native subagents are disabled separately from the `CLINK_DELEGATION_DEPTH` marker that blocks nested clink launches. The marker does not prevent access to an already-running MCP server. | [backend.py](/Users/d/Tools/clink-mcp-server/clink/backend.py:567), [design](/Users/d/Tools/clink-mcp-server/docs/clink-only-design.md:137) |
| Human visibility | Results and metadata-only logs are available. No live terminal observer, PTY, or tmux session implementation was found in relevant source/docs. | [runner.py](/Users/d/Tools/clink-mcp-server/clink/runner.py:94), [runner.py](/Users/d/Tools/clink-mcp-server/clink/runner.py:271) |

Only the pinned Codex configuration currently has admitted execution. Claude, Gemini, agy, and grok remain unavailable under the current policy. This is a statement about clink's existing admission, not evidence that those CLIs lack useful native capabilities. See [current availability](/Users/d/Tools/clink-mcp-server/README.md:41).

Existing workflow semantics also matter. Chat/clink consult once; eight expert workflows consult at their enabled final hook; planner/docgen/tracer/challenge remain local. Consensus isolates consultant inputs and invalidates results when declared files change. Enabling tools does not automatically turn those workflows into autonomous implementation, and allowing consensus participants to edit shared files conflicts with its current consistency checks. See [tool behavior](/Users/d/Tools/clink-mcp-server/README.md:66).

## Recommended target contract

Delegate a scoped task to the chosen CLI in the intended project, using its configured native capabilities, and expose progress, results, permission needs, and follow-up conversation state.

1. **Preserve effective configuration.** Let the native CLI load its project/user instructions, tools, skills, hooks, plugins, MCP servers, and subagents. Keep authentication/configuration CLI-owned. Resolve the correct working directory and relevant environment; do not clone credential stores or place secret values in prompts, manifests, receipts, or logs. Record configuration sources and capability evidence without exposing values. Available capabilities remain bounded by native policy and the assigned task.
2. **Preserve permission semantics.** Configuration inheritance does not authorize silently approving every operation. Use the CLI's existing permission settings and supported approval handoff. Report a required decision when the transport cannot obtain it. Verify differences between headless and interactive trust behavior rather than promising exact equivalence.
3. **Choose transport per adapter.** Prefer structured CLI events when they preserve the capabilities required by the task. Use a PTY when a verified feature or approval interaction requires a terminal. Do not require tmux merely to enable tools. A native session can be resumed across separate processes where the CLI supports it.
4. **Represent task/session state explicitly.** Distinguish execution attempts, conversational sessions, and workflow continuations. Expose running, awaiting input, complete, failed, cancelled, and uncertain outcomes. Bind resumed sessions to the correct CLI/project and avoid full history replay into an already-resumed native conversation. Define deadline, expiry, retention, and restart behavior before implementing persistence.
5. **Permit native subcontracting.** Native subagents are part of the requested capability set. Treat recursive clink calls separately through explicit lineage and bounded depth/concurrency. An environment marker alone cannot police an already-running server. Account for parent/child scheduling so waiting parents do not occupy every slot needed by their children.
6. **Preserve task intent.** An implementation task may edit and test; a review still requests findings. Decide how existing analytical workflows map to the new execution capability without silently changing their purpose. Roles should guide work without reinstating global advisory bans.
7. **Add observation as an option.** A structured activity stream may be enough. For interactive sessions, tmux can offer a shared terminal and read-only attachment. Define single-writer ownership and explicit human takeover. Protect and bound retained output; ordinary diagnostic logs should remain content-free.

## Transport evidence and limits

A PTY is an operating-system terminal interface that lets a program supply input and consume terminal output. It supplies neither conversational memory nor an agent task protocol. See [PTY documentation](https://www.man7.org/linux/man-pages/man7/pty.7.html).

Current [Claude headless documentation](https://code.claude.com/docs/en/headless) says ordinary `claude -p`, without bare mode, loads configured hooks, skills, subagents, plugins, MCP servers, and project instructions. It also documents different trust behavior in print mode, including project hooks/MCP loading without interactive trust prompts. This establishes that pipes alone do not prevent capability inheritance, but does not establish parity or permission behavior for the installed version. Recheck supported flags and configuration behavior during adapter implementation. Native session continuation is documented on the same page.

[tmux control mode](https://github.com/tmux/tmux/wiki/Control-Mode) exposes commands and output notifications; the output remains terminal data. Command acknowledgement is not agent-turn completion. The [tmux manual](https://github.com/tmux/tmux/blob/master/tmux.1) documents read-only attachment. Terminal silence alone is insufficient evidence of completion. Read-only attachment prevents accidental input; it is not an isolation boundary against processes with the same OS access.

## Migration and verification recommendations

1. **Amend the product and execution contract.** Reconcile this objective with `AGENTS.md`, `CLAUDE.md`, design/setup/configuration docs, manifests, role/system prompts, admission rules, and tests. Separate reusable preparation from behavior changes. Do not remove restrictions without replacing the obsolete contract and validating the intended behavior. Preserve all historical failures and receipts with their original scope.
2. **Complete one adapter through the whole task lifecycle.** Prefer the adapter for which native configuration, permissions, task execution, and follow-ups can be verified first. Reuse direct argv execution, immutable request/target snapshots, queueing, output bounds, deadlines, cancellation, parsers, and explicit error handling where compatible. Do not carry forward frozen capability/model projections that defeat the new objective merely because the old admission required them.
3. **Extend adapter coverage, then observation.** Use a common compatibility contract, allow provider-specific mechanisms, and disclose unsupported behavior. A terminal viewer should not delay basic delegated execution.

Use synthetic fixtures first, then separately authorized native checks in disposable workspaces. Test config/project-instruction discovery, a tool action, a hook, native subagent use, configured MCP integration/environment forwarding, approval handling, follow-up context, cancellation, and failure outcomes. Do not claim success for untested adapters or every possible user configuration.

Risk & Mitigation:

- **Side effects and uncertain completion.** A disconnect or timeout can follow successful edits or external actions. Preserve attempt identity and partial evidence. Never automatically resend, restart, or switch providers after ambiguous submission. Cancellation does not roll back work.
- **Concurrent writers.** Serialize session input and coordinate overlapping workspace edits. Separate worktrees are an option, but can change configuration discovery and trust context. Do not assume the resulting environment is equivalent.
- **Lifecycle boundaries.** Hooks, native subagents, detached jobs, and existing remote MCP services may outlive one subprocess. Verify what cleanup actually terminates and report what remains outside it.
- **Authority and sensitive output.** Existing approved roots constrain server-supplied attachments; they do not prove where a fully enabled CLI can read/write or what external tools can do. Keep approval, observer access, transcript retention, and redaction explicit without stripping the capabilities the user requested.

Relevant existing tests include `tests/test_cli_admission_policy.py`, `tests/test_clink_backend.py`, `tests/test_clink_registry.py`, `tests/test_cli_tool_bridges.py`, `tests/test_call_state.py`, and `tests/test_consensus_manifest.py`. Update tests that intentionally encode the old objective while retaining meaningful bounds, cancellation, parser, and state coverage.

Suggested commands after implementation, from `/Users/d/Tools/clink-mcp-server`:

```sh
.venv/bin/python -m pytest tests/test_cli_admission_policy.py tests/test_clink_backend.py tests/test_clink_registry.py tests/test_cli_tool_bridges.py tests/test_call_state.py tests/test_consensus_manifest.py -q
.venv/bin/python -m pytest tests/ -v -m "not integration"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

These commands are future implementation checks, not checks reported as run for this handoff. The old live-policy helper verifies capability suppression; redesign it before using it as evidence for native delegation.

## Open choices and authorization

Implementation choices remain proposed: public task/session operations versus extending existing tools; compatibility behavior for existing advisory clients; native session retention and restart recovery; permission handoff per CLI; environment inheritance rules; recursion limits; and the first adapter to validate. Resolve routine bounded choices through inspection and planning under the user's standing development authorization. Ask only for a material product or authority choice that existing instructions do not settle.

The user grants standing authorization for routine development scope amendments, verification repairs, and necessary scoped review exports, including technical materials otherwise covered by older per-packet exclusions. Preserve genuine review, read-only reviewer operation, prior failures, and consumed-attempt history. Exclude credentials, production/user records, and complete raw session/receipt bodies unless specifically authorized. This handoff does not authorize production deployment, unrelated disclosure, blanket permission bypasses, or unlimited live-provider usage.

Execution and routine engineering use `gpt-6.1-sol` with `ultra` reasoning. Planning uses the dedicated `planning` role configured for `gpt-6-astra`/`ultra`; Astra remains planning-only. Codex plan/implementation/completion reviews use `gpt-6.1-sol`/`high` through the configured review route. Preserve required native review gates and truthful runtime evidence; do not substitute a documentation check for an implementation/admission verdict.

## Handoff provenance

This investigation read source and current external documentation. It did not launch a provider, alter native configuration, or implement the redesign. This handoff is the only file created by this task.

Source inspection used an explorer explicitly selected as Sol 6.1/ultra. Architecture planning used the dedicated planning role configured for Astra/ultra. Effective runtime model/effort metadata was unavailable, so those settings remain runtime-unverified. This document records findings and recommendations; it is not a claim that native delegation is implemented or admitted.

Handoff validation resolved all 21 local source links and checked whitespace. An independent read-only documentation check, explicitly selected as Sol 6.1/high, found no material errors against the inspected source and Git snapshot. Its effective runtime settings remain unverified; it did not independently revalidate external documentation or prior investigation provenance. No application tests or live CLI probes were run for this documentation-only task. This check does not replace the receiving agent's implementation review or adapter verification.
