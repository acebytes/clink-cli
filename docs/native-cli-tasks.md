# Native CLI tasks

Explicit `clink` actions delegate scoped implementation using the selected CLI's configured tools, hooks, skills, plugins, MCP integrations, and subagents under native permissions. Omitted `action` and `action: "consult"` retain advisory consultation with `codex/default`; analytical workflows keep their existing intent.

**Claude Code 2.1.304 native tasks are admitted** for the exact binary and launch recipe in the [native admission record](verification/native-claude-2.1.304-admission.json). A changed binary/recipe fails closed and requires reverification.

Genuine attempts 2/3 cover file edits, native failing-then-passing tests, hooks, a configured subagent, synthetic MCP environment inheritance, same-session follow-up, `permission_needed`, and cancellation after known mutation with the synthetic descendant gone. Attempt 1 and attempt 2's incomplete test/cancellation coverage remain preserved in [native lifecycle results](native-lifecycle-results.md). This evidence covers the recorded synthetic project, not every native configuration/model/platform or all detached/remote work.

The existing Codex admission verifies its restrictive consultation profile only. Enabling `native_task` or finding an executable does not admit native execution. See [configuration](cli-configuration.md#native-task-profile) and [admission scope](clink-cli-admission.md#native-task-admission-is-separate).

## Submit and inspect work

These examples use the separately admitted Claude native profile. Replace the directory with an existing absolute path inside the configured client's `allowed_roots`.

```json
{
  "action": "start",
  "cli_name": "claude",
  "project_dir": "/work/project",
  "prompt": "Implement the scoped fix, run the relevant checks, and report changes, checks, and unfinished work.",
  "request_key": "fix-42-start-1"
}
```

Start/follow-up may set a finite numeric `timeout_seconds` from 1 to 1800; it bounds that attempt including launch preparation and queue wait. Each follow-up receives a fresh deadline. Start atomically reserves a task and caller key, schedules server-owned work, and promptly returns `task_handle`, `attempt_id`, and `state`. It does not wait for task completion. Keep the response and request key. Poll with only the action and handle:

```json
{"action":"status","task_handle":"nt_0123456789abcdef0123456789abcdef"}
```

The states are `queued`, `running`, `completed`, `permission_needed`, `failed`, `cancelled`, and `uncertain`. Unknown or expired handles return `unavailable` with `execution_outcome: "unknown"`; status/cancel never launch replacement work. `uncertain` also reports an unknown execution outcome. Returned times are process-local monotonic timestamps, not wall-clock dates.

A terminal status may include `result.content`, truncation metadata, and bounded technical metadata. Only the latest attempt's result is retained. `completed` means terminal execution succeeded; it does **not** prove that the assigned goal was fulfilled. `execution_does_not_verify_task_fulfillment` remains `true`, and `agent_reported_claims` are marked `unverified`. Independently check reported changes, tests, unfinished work, and possible partial effects.

## Continue or cancel

When `followup_available` is true, send only new input and any new context. The server resumes the recorded private native session without replaying the full PAL conversation:

```json
{
  "action": "followup",
  "task_handle": "nt_0123456789abcdef0123456789abcdef",
  "prompt": "Address the remaining failing check, then report its result.",
  "request_key": "fix-42-followup-1"
}
```

Each follow-up creates a new attempt. The CLI, canonical project, role, instruction profile, and model selection remain bound to the task; conflicting selections fail. Native session identifiers never become public handles or PAL `continuation_id` values. Native actions reject `continuation_id`, and status/cancel do not create history turns.

```json
{"action":"cancel","task_handle":"nt_0123456789abcdef0123456789abcdef"}
```

Cancellation is idempotent. It removes queued work or awaits process-group cleanup before releasing ownership. Server shutdown also cancels active native tasks and awaits cleanup. Neither operation rolls back files, external actions, usage, or remote work already submitted.

## Redelivery and ownership

Use a unique `request_key` for each intended start/follow-up. Keys contain 1–128 ASCII characters: the first is alphanumeric, and the rest may also contain `_`, `.`, `:`, or `-`. If the response is lost, redeliver the identical request with the same key. Within retention, a server-wide index returns the original handle/attempt without submitting again, even if the original handle was lost. Different input with that key fails with `request_key_conflict`.

Redelivering an older attempt returns its own metadata. If its result was replaced, `result_unavailable` is true; it never returns a newer attempt's result. Retention is volatile, so deduplication is not a durable idempotency guarantee. After restart or expiry, investigate actual effects before another submission. The server never automatically resends, retries, resumes, or switches providers after an ambiguous outcome.

Session input is serialized. One active native writer may own a canonical project directory; equal, parent, or child project directories are rejected while that ownership is active, before taking a process slot. External writers and CLIs launched elsewhere remain outside this coordination. Native subagents are supported; the known-depth recursion marker cannot police calls through an already-running clink server, and no general recursion scheduler is supplied.

## State and retention

| Bound | Limit |
| --- | --- |
| Retained tasks / attempts | 64 tasks; 16 attempts per task. |
| Task metadata and results | 8 MiB total, including index/context and active-result reservations. |
| Retained result | Latest attempt only; at most 20,000 characters. |
| Inactive retention | Three hours after the last terminal attempt; polling does not extend it. |
| Active work | Never evicted; capacity overflow fails before submission. |
| Running processes / waiters | Shared pool: 2 by default, 8 waiters. |
| Queue / overall deadline | At most 30 seconds / 1800 seconds, including launch preparation and queue wait. |
| Prompt / captured streams | 1 MiB prompt; 10 MiB each for stdout and stderr. |

Records, request keys, and private session mappings disappear on server restart. The native CLI may retain its own operational/session files; this server provides no durable task store or automatic recovery. Save useful results durably before restart/expiry. See [shared execution bounds](cli-configuration.md#execution-policy-and-bounds).

## Permissions and effects

The operator configures native roles and inherited environment **names** separately from consultation settings. Values are resolved only into the child environment; keep them out of manifests, prompts, logs, and receipts. The native CLI owns authentication/configuration and enforces permissions. This MVP supplies no permission bypass, PTY, interactive approval relay, or terminal viewer. An approval that cannot be obtained headlessly returns `permission_needed`; a new explicit submission still uses native permissions. Explicit image input is currently unsupported for native tasks. Legacy `temperature` and `thinking_mode` controls are rejected for native actions; use native CLI configuration and the supported native model fields.

Risk & Mitigation: native tasks can read/write and invoke external integrations beyond server-supplied context roots. Approved roots do not establish an OS sandbox. Hooks, detached descendants, native subagents, and remote MCP work may outlive the supervised process; cleanup evidence must state observed limits. Inspect `possible_partial_effects` and actual project/external state after failed, cancelled, uncertain, or lost executions before deciding what to submit next.
