# First native CLI task lifecycle

Approved plan following the [handoff](native-cli-delegation-handoff.md). Implementation and verification are complete; see [results](native-lifecycle-results.md). The statements below record the planning boundary, when native execution was unverified and no application tests, authentication checks, or model calls had been performed for this lifecycle.

The MVP delegates scoped tasks through configured CLIs, returns status/results, continues native conversations, and supports cancellation. Tools, hooks, skills, plugins, MCP integrations, and subagents retain native permissions. PTY/tmux, viewers, server persistence, approval relays, and general recursion scheduling are deferred.

## 1. Establish one native adapter contract

Choose Claude Code provisionally for documented [headless execution and resume](https://code.claude.com/docs/en/headless). Installed 2.1.304 help confirms interfaces, not configuration inheritance, authentication, permissions, or cleanup. Verify these on the exact installed platform/version before admission. Discover supported flags; never invent them.

Adapt `clink/policy.py`, `clink/backend.py`, and the Claude parser. Scope new argv, environment inheritance, prompts, and admission to a separate native-task profile. Preserve Codex's environment allowlist, recipe digest, model catalog, and defaults. Bind native sessions to CLI, project, role, instruction profile, and model selection; reject conflicting follow-ups.

Distinguish recoverable tool errors from terminal failure. Intermediate tool failure can precede terminal success; fatal/configuration failure still fails. Exit status and the terminal envelope establish execution status, not task fulfillment. Bounded results report changes, checks, unfinished work, and known/possible partial effects. Label agent-reported claims unverified unless independently checked; `completed` must never imply the task goal was achieved.

**Exit criterion:** establish version-specific config loading, terminal outcomes, session identifiers/resume, permission behavior, and cancellation boundaries. Unknown mandatory behavior blocks native launch.

## 2. Add a small task interface and bounded records

Extend `tools/clink.py` with `action=consult|start|status|followup|cancel`; omission retains existing consultation behavior. `start` requires explicit CLI selection and an approved project directory. Other native actions use an opaque `task_handle`.

Start/follow-up atomically reserve a caller request key and record, schedule server-owned work, and promptly return handle, attempt ID, and state. `status` returns the eventual bounded result. A server-wide index retains key, input fingerprint, handle, and attempt ID within record limits/TTL, deduplicating starts even when the original handle was lost. Different-input key reuse fails. Older-attempt redelivery returns its own metadata and `result_unavailable` if replaced, never the latest result or another execution.

Use `clink/contracts.py` and a focused new `clink/tasks.py`. Keep four identities separate: public task handle, private native conversation/session mapping, per-submission attempt ID, and existing PAL workflow continuation ID. Route native actions in `server.py` before PAL conversation bookkeeping. Reject PAL `continuation_id` on these actions; polling and cancellation must not create history turns.

Retain at most 64 tasks, 16 attempts per task, and 8 MiB total metadata/results. Keep only the latest result, capped at 20,000 characters. Expire inactive records three hours after their last terminal attempt; polling does not extend retention. Never evict active work. Reject capacity overflow before submission. After restart/expiry, return unavailable handles with execution outcome unknown; never recreate tasks automatically.

**Exit criterion:** synthetic tests prove validation, deduplication, bounds/expiry, identity separation, and zero launches for unknown-handle status/cancel.

## 3. Preserve configuration and run the task

Reuse registry snapshots, direct argv execution, prompt/context bounds, and `ProcessPool`. Native-profile operators configure inherited environment **names**; resolve values from the server environment only into the child environment. Never copy credentials into manifests, prompts, receipts, or logs. Preserve native auth/config locations and project instructions. Record source categories and missing required names without values. Keep server-owned launch flags protected.

Compose task instructions and subordinate role expertise without the advisory ban. Native permissions remain authoritative. Unsupported headless approval ends with `permission_needed`, without silent approval, weakened permissions, or indefinite waiting.

Own background executions at server lifetime. Retain the two-process default, eight-waiter queue, 30-second queue limit, 1800-second deadline, and output ceilings. Acquire project/session ownership before occupying a process slot.

**Exit criterion:** a fake CLI receives intended configuration/environment and task; prompts/secrets remain absent from diagnostics.

## 4. Implement follow-up, ownership, and cancellation

Serialize session input and allow one delegated writer per canonical project, rejecting overlapping active roots. External writers remain outside this server's coordination. Follow-ups use the recorded native session and new input/context, without full PAL replay or silent profile/model changes.

Expose queued, running, completed, `permission_needed`, failed, cancelled, and uncertain outcomes. Idempotent cancellation removes queued work or performs process-group cleanup before releasing ownership. Preserve attempt identity and possible partial effects. The server never automatically resends, resumes, retries, or switches providers; investigate ambiguous timeout/disconnect before another submission.

Support native subagents. The depth marker cannot govern recursion through an already-running clink server. Disclose that limit, preserve known-depth rejection, and promptly reject conflicting project/session admissions.

**Exit criterion:** tests cover concurrent follow-ups, overlapping projects, queued/repeated cancellation, descendant cleanup, lost start responses, and cancellation after mutation.

## 5. Verify the complete lifecycle and reconcile guidance

Build a disposable fixture task that edits/tests a file, invokes a configured hook/subagent and synthetic MCP integration, returns a result, then follows up using native context. Exercise permission-needed and cancellation after side effects. Run offline fixtures before bounded authorized native checks. Retain genuine evidence and explicit unsupported capabilities.

Extend backend, parser, registry, call-state, bridge, and lifecycle tests. Preserve analytical tool intent and consensus consistency checks; this MVP enables native execution only through explicit lifecycle actions. Reconcile `AGENTS.md`, `CLAUDE.md`, role/tool prompts, configuration docs, and admission guidance during implementation. Preserve historical receipts with their original scope. Run focused tests, the nonintegration suite, and check-only lint/formatting before independent Sol/high review.

**Exit criterion:** one admitted adapter passes start → status/result → native follow-up → cancellation, while legacy defaults and analytical workflows retain their contracts.

## Risk & Mitigation

Native tasks can change files and external systems before failure. Cancellation does not roll back effects; detached descendants and remote MCP work may outlive the process. Document observed cleanup limits and never infer isolation from approved attachment roots.

Standing authorization covers routine scope amendments, verification repairs, and scoped review exports. Execution uses Sol/ultra; independent Codex review uses Sol/high. Planning selection was explicitly Astra/ultra; effective runtime settings remain unverified. Outstanding gates are installed native behavior, permission detection, configuration fidelity, session continuity, and cleanup evidence.
