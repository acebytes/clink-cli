# Native CLI tasks

Explicit `clink` actions delegate scoped implementation using the selected CLI's configured tools, hooks, skills, plugins, MCP integrations, and subagents under native permissions. Omitted `action` and `action: "consult"` retain advisory consultation with `codex/default`; analytical workflows keep their existing intent.

**Claude Code 2.1.304 native tasks are admitted** for the exact binary and launch recipe in the [native admission record](verification/native-claude-2.1.304-admission.json). A changed binary/recipe fails closed and requires reverification.

Genuine attempts 2/3 cover file edits, native failing-then-passing tests, hooks, a configured subagent, synthetic MCP environment inheritance, same-session follow-up, `permission_needed`, and cancellation after known mutation with the synthetic descendant gone. Attempt 1 and attempt 2's incomplete test/cancellation coverage remain preserved in [native lifecycle results](native-lifecycle-results.md). This evidence covers the recorded synthetic project, not every native configuration/model/platform or all detached/remote work.

**Grok 1.0.70 native tasks are also admitted** for the exact macOS arm64 binary and recipe in the [Grok admission record](verification/native-grok-1.0.70-admission.json), with observed `grok-4.7-build` execution. Its composite evidence covers native edit/test, hooks, specialist, MCP/environment, follow-up, permission refusal and cancellation after mutation. All nine top-level invocations remain recorded. Ordinal8 returned `uncertain/native_failed` with unknown cause; ordinal9 passed without establishing a diagnosed or repaired production defect. See the [compatibility and limitation review](native-grok-admission-review.md).

**Antigravity 1.4.12 native tasks are admitted** for the exact macOS arm64 binary and recipe in the [Agy admission record](verification/native-agy-1.4.12-admission.json). One complete sequence passed edit/test, configured hooks/MCP and child lifecycle/read, same-session follow-up, permission refusal, and cancellation with the identified child and process group gone. All ten calls and earlier failed gates remain preserved. Actual model and returned child-token content remain unverified; see the [admission review](native-agy-admission-review.md).

The Codex native adapter remains disabled: its installed `exec --json` interface omits a genuine prelaunch permission refusal, preventing reliable `permission_needed` reporting for that path. Its public history APIs do not expose the missing failure either. See the [source-backed diagnosis](native-codex-attempt5-diagnosis.md). The existing Codex admission verifies its restrictive consultation profile only. Enabling `native_task` or finding an executable does not admit native execution. See [configuration](cli-configuration.md#native-task-profile).

All four adapters reuse `NativeTaskManager` and the shared process pool. Task handles, attempt tracking, deduplication, project/session ownership, cancellation, and retention follow the same rules below; adding an adapter does not create a separate pool or change the consultation defaults.

## Submit and inspect work

These examples use the separately admitted Claude native profile; use `cli_name: "grok"` or `cli_name: "agy"` for an admitted Grok or Antigravity installation. Replace the directory with an existing absolute path inside the configured client's `allowed_roots`.

For Agy outside the server checkout, use the complete [external-project manifest example](cli-configuration.md#antigravity-projects-outside-the-checkout). `path_not_allowed` is a project/context admission refusal before launch; inspect the configured roots and resolved paths. Omitting `action` still selects consultation, whose admission is separate from native tasks.

```json
{
  "action": "start",
  "cli_name": "claude",
  "project_dir": "/work/project",
  "prompt": "Implement the scoped fix, run the relevant checks, and report changes, checks, and unfinished work.",
  "request_key": "fix-42-start-1"
}
```

Start/follow-up may set a finite numeric `timeout_seconds` from 1 to 1800; it bounds that attempt including launch preparation and queue wait. A shorter configured client timeout wins. Each follow-up receives a fresh deadline. Start atomically reserves a task and caller key, schedules server-owned work, and promptly returns `task_handle`, `attempt_id`, and `state`. It does not wait for task completion. Keep the response and request key. Poll with only the action and handle:

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
| Prompt / captured streams | 1 MiB assembled input, including Agy's encoded envelope; 10 MiB each for stdout and stderr. |

Records, request keys, and private session mappings disappear on server restart. The native CLI may retain its own operational/session files; this server provides no durable task store or automatic recovery. Save useful results durably before restart/expiry. See [shared execution bounds](cli-configuration.md#execution-policy-and-bounds).

## Adapter transports and permissions

This table describes implemented contracts. Claude, Grok, and Antigravity have production native admission for their exact recorded binaries/recipes; native Codex remains disabled for its known output limitation.

| Adapter | Input and native follow-up | Permission behavior |
| --- | --- | --- |
| Claude | Text on stdin; `--print --output-format stream-json --verbose --permission-prompts none`; `--resume` with the recorded private session. | Native configuration and permissions apply. Unattended prompts are denied; explicit denial events or the terminal denial list produce `permission_needed`, even with a successful result label. |
| Grok | `--prompt-file` and `--output-format json`, with empty stdin; `--resume` with the recorded exact UUID. | Native headless approval refusals are recognized from the documented stderr marker. A provider refusal alone is not treated as a permission request. |
| Antigravity | `--input-format stream-json --output-format stream-json`; one `{"event":"user","message":{"content":"..."}}` NDJSON event followed by EOF; `--conversation` with the private identity. | Native headless confirmation requests are soft-denied. Reported `denied_actions` produce `permission_needed`; timeout/interrupted/error finish reasons cannot establish completion. |
| Codex | `exec --json --color never --cd <project> -`; follow-up uses `exec --cd <project> resume --json <session> -`, with optional native model selection. | Native `exec` approval semantics apply. The wrapper adds no approval or sandbox override; recognized native permission-denial results produce `permission_needed`. |

The server selects resume identities from its private mapping; callers cannot supply arbitrary session IDs, titles, or latest-session flags. It checks the resulting native identity against the recorded session. Intermediate recoverable tool errors may precede terminal success; nonzero process exit and fatal terminal errors still prevent successful execution. Missing or unrecognized terminal output cannot prove completion.

Grok's prompt is at most 1 MiB in a `0600` regular file inside a unique `0700` directory. The server checks the file's identity, permissions, size, and content before launch, keeps it alive through shared process cleanup, and removes it on controlled completion, failures, queue rejection, or cancellation. Task text is absent from argv and stdin. A hard host/server crash can leave private temporary files; these files are not a durable task store.

The operator configures native roles and inherited environment **names** separately from consultation settings. Values are resolved only into the child environment; keep them out of manifests, prompts, logs, and receipts. Grok additionally preserves a configured `GROK_HOME`; other integration environment names require explicit operator configuration. Native login/configuration remains CLI-owned. This MVP supplies no permission bypass, PTY, interactive approval relay, or terminal viewer. Recognized permission denials report `permission_needed`; other terminal failures or incomplete signals can report `failed` or `uncertain`. A new explicit submission still uses native permissions.

Explicit image input is currently unsupported for native tasks. Legacy `temperature` and `thinking_mode` controls are rejected for native actions; use native CLI configuration and the supported native model fields.

Risk & Mitigation: native tasks can read/write and invoke external integrations beyond server-supplied context roots. Approved roots do not establish an OS sandbox. Hooks, detached descendants, native subagents, and remote MCP work may outlive the supervised process; cleanup evidence must state observed limits. Inspect `possible_partial_effects` and actual project/external state after failed, cancelled, uncertain, or lost executions before deciding what to submit next.

## Verification and testing

Ordinary tests use synthetic executables, output fixtures, temporary native homes, and disposable Git repositories; they require no provider credentials or model calls:

```sh
.venv/bin/python -m pytest tests/test_native_adapters.py tests/test_native_runtime.py -q
.venv/bin/python -m pytest tests/test_claude_native_parser.py tests/test_grok_native_parser.py tests/test_agy_native_parser.py tests/test_codex_native_parser.py -q
.venv/bin/python -m pytest tests/test_native_tasks.py tests/test_native_server.py tests/test_native_runner.py -q
.venv/bin/python -m pytest tests/test_native_adapter_verifier.py tests/test_native_fixture_trust.py -q
```

These checks cover input transport, session matching, terminal/permission parsing, environment-name inheritance, shared limits, and cleanup. They do not establish installed CLI authentication, configuration fidelity, or native admission.

For an authorized live check, inspect the appropriate harness first:

```sh
.venv/bin/python scripts/verify_native_cli.py --help
.venv/bin/python scripts/verify_native_adapters.py --help
```

The first harness is the recorded Claude lifecycle verifier. The second tests Grok/Agy/Codex through the actual registry, runtime, process pool, and task manager using a verifier-local candidate policy. It requires explicit `--live` opt-in and a unique durable evidence path. Model submissions are bounded and retained in the receipt lineage; prior failed/partial attempts and spent submissions must not be reset or silently retried. Native approval/configuration remains authoritative, and a passing probe does not automatically change production admission.

Grok's verifier can explicitly enroll only its exact marked synthetic Git root with `--trust-grok-fixture`. The helper coordinates the native trust file, preserves unrelated entries/comments, and removes only its own unchanged grant. Existing or changed grants are never overwritten. This verification-only option is not used by normal task submission and does not bypass native permission rules.
