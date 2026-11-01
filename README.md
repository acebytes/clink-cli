# clink-mcp-server

Delegate work to installed CLI agents through MCP. Consultations return advice and proposed code. Explicit native tasks let an agent implement a request using its configured tools, hooks, skills, plugins, MCP integrations, and subagents under its own permissions. Start a task, inspect its progress, follow up in the same native session, or cancel it.

The server preserves PAL's **18 MCP tools**. Native task actions extend the existing `clink` tool.

Derived from [PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server), formerly Zen MCP. **Copyright 2025 Beehive Innovations.** This fork preserves the upstream attribution and [Apache 2.0 license](LICENSE).

## What works now

| Use | Interface | Current support |
| --- | --- | --- |
| Ask for analysis or proposed code | `clink` with omitted action or `action: "consult"` | Defaults to Codex and the `default` role; returns content without writing project files. |
| Delegate implementation | `clink` actions `start`, `status`, `followup`, `cancel` | Claude Code, Grok, and Antigravity native tasks, including configured capabilities and session continuation. |
| Inspect configuration | `version`, `listmodels` | Local discovery without logging in or calling a model. |

| CLI | Consultation | Native tasks |
| --- | --- | --- |
| Codex 0.166.0 | Verified text, explicit local images, and structured native search. | Adapter implemented; bundled profile disabled pending separate native live admission. |
| Claude Code 2.1.304 | Unavailable under the separate advisory policy. | Verified for the recorded binary and launch recipe. |
| Antigravity 1.4.12 (`agy`) | Consultation execution unverified. | Verified for the recorded macOS arm64 binary and native launch recipe. |
| xAI Grok Build 1.0.70 (`grok`) | Consultation execution unverified. | Verified for the recorded macOS arm64 binary and native launch recipe. |
| Gemini | Unavailable pending policy verification. | Not implemented. |

Verification is bound to exact binaries and launch recipes. Current live evidence covers the recorded macOS arm64 installations. An installed executable or an authentication session alone does not admit execution; upgrading a CLI can require reverification. See the [Codex admission report](docs/verification/cli-policy/README.md), [Claude native lifecycle results](docs/native-lifecycle-results.md), and [Grok native admission](docs/native-grok-admission-review.md).

Native Codex passed edit/test, configured MCP/subagent, and same-session follow-up checks, but remains disabled: Codex 0.166.0 omits a genuine prelaunch permission refusal from its supported execution output. Clink cannot reliably report that refusal as `permission_needed`. A supported native failure signal or a verified CLI upgrade is required; more permission grants do not repair this output limitation. See the [diagnosis](docs/native-codex-attempt5-diagnosis.md).

## Install and connect

Python 3.10+ is required; Python 3.12 is the tested runtime. Process supervision targets POSIX systems. Other macOS builds and Linux need their own CLI verification; native Windows delegation is unavailable until equivalent process-tree cleanup is verified.

```sh
./run-server.sh --setup
./run-server.sh
```

`--setup` creates `.venv` when needed and installs runtime requirements, then exits. Ordinary startup uses `.venv/bin/python` without installing anything. The server communicates over stdio and normally runs under an MCP client; it may appear quiet when started directly.

The launcher does not create `.env`, copy credentials, register MCP servers, or change native CLI configuration. Optional server settings are in [.env.example](.env.example). Authenticate a supported CLI through its native login mechanism before delegating work; the server does not require hosted-provider API keys.

Add this entry to your MCP client's configuration, replacing the checkout path:

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

Ensure the MCP client's `PATH` resolves the CLI, or configure its absolute executable path in a client manifest. Configure `working_dir` and `allowed_roots` before selecting projects outside the server's default working directory. Restart the MCP connection after updating server code or configuration. See [getting started](docs/getting-started.md), [CLI configuration](docs/cli-configuration.md), and [migration from PAL](docs/migration.md).

For multiple repositories, use [project setup](docs/project-setup.md) to generate each project's `.clink/` manifests/prompts and `.mcp.json` registration. From this checkout:

```sh
.venv/bin/python -m clink.setup --project /absolute/path/to/your-project
```

Claude Code also supports `/clink:setup` after loading the local plugin with `claude --plugin-dir /absolute/path/clink-mcp-server/integrations/claude`. Project settings override user settings when the server is launched with its generated `--project` binding. Setup preserves custom files and makes no model calls.

## Run the first native task

Call `version` and `listmodels` with empty arguments first. `listmodels` separates advisory `clients` from `native_task_clients`; inspect the admitted Claude, Grok, or Antigravity native profile. Discovery reports installation and recorded verification without authenticating or making inference calls. The current binary, recipe, and admission record are checked again at launch, so discovery availability is provisional.

In advisory `clients`, `installed: true` only means the executable is present; `available` additionally requires verified consultation policy and POSIX support. Agy consultation remains unavailable even when its native profile is admitted. Use explicit `action: "start"` for native delegation, and configure an [external-project manifest](docs/cli-configuration.md#antigravity-projects-outside-the-checkout) before selecting a project outside the default checkout roots. A `path_not_allowed` refusal happens before the CLI launches.

Use a disposable project with one failing test inside your configured roots. These examples are arguments to the **`clink` MCP tool**, not shell commands. Replace the project path, specify its actual test command in the prompt, and choose a new request key for each new submission:

```json
{
  "action": "start",
  "cli_name": "claude",
  "project_dir": "/absolute/path/to/scratch-project",
  "prompt": "Fix the failing addition test. Run this project's test command before and after the change. Report changed files, check results, and unfinished work.",
  "request_key": "scratch-fix-start-1",
  "timeout_seconds": 180
}
```

Start returns promptly with a `task_handle`, `attempt_id`, and state. Save them and poll using the returned handle:

```json
{"action":"status","task_handle":"nt_0123456789abcdef0123456789abcdef"}
```

States are `queued`, `running`, `completed`, `permission_needed`, `failed`, `cancelled`, or `uncertain`. Inspect `result.content`, any `error.code`, and `possible_partial_effects`. **`completed` means execution finished; independently inspect the diff and run the checks before accepting the work.** Agent-reported changes and checks are marked unverified.

When `followup_available` is true, send a new prompt and request key on the same handle:

```json
{
  "action": "followup",
  "task_handle": "nt_0123456789abcdef0123456789abcdef",
  "prompt": "Add one edge-case test for the behavior you just changed, run it, and report the result.",
  "request_key": "scratch-fix-followup-1",
  "timeout_seconds": 180
}
```

A follow-up creates a new attempt and resumes the private native session. The CLI, project, role, instruction profile, and model selection remain fixed. Native tasks use `task_handle`; do not pass a PAL `continuation_id`. Optional declared context uses `absolute_file_paths`.

To cancel active work:

```json
{"action":"cancel","task_handle":"nt_0123456789abcdef0123456789abcdef"}
```

Status and cancel accept only those two fields. Cancellation waits for supervised process cleanup and is idempotent. It does not roll back edits or external actions; if work already finished, its terminal outcome is retained.

Redeliver an identical submission with its original request key to retrieve the same task and attempt without another execution. Changing the input while reusing the key returns `request_key_conflict`. Deduplication lasts only while the server retains the attempt. See [native task usage](docs/native-cli-tasks.md) for all input, state, and retention rules.

## What to test now

The recorded Claude, Grok, and Antigravity lifecycle checks passed independent review for their exact admitted binaries/recipes. Use an admitted profile for the acceptance pass below in a disposable project. To test the newly enabled Antigravity path, change the example's `cli_name` to `agy`. Native Codex remains disabled for the output limitation described above.

| Check | Action and expected result |
| --- | --- |
| Connection and discovery | Restart the MCP connection, then call `version` and `listmodels`. Claude, Grok, and Antigravity have admitted native profiles; Codex remains the consultation backend. Discovery makes no model call. |
| Edit and test | Start the small failing-test task above. Receive a handle promptly, poll to completion, inspect the actual diff, and independently rerun the test successfully. |
| Same-session follow-up | Submit a small adjustment when `followup_available` is true. Expect the same handle, a new attempt ID, and useful prior native context without resending the conversation. |
| Duplicate delivery | Repeat an identical start with its original key. Expect the same handle/attempt and `deduplicated: true`, with no repeated work. Change the prompt with that key and expect `request_key_conflict`. |
| Your configured capabilities | Exercise one known benign hook, subagent, or MCP operation. Verify its actual artifact or result. Configure required environment variable names in the native profile; do not put credential values in prompts. |
| Cancellation | Start a harmless long-running task that first creates a disposable marker. Independently observe the marker and confirm work is still active before cancelling; `running` alone does not prove an edit happened. Expect cleanup before project ownership is released, the marker to remain, and repeated cancellation to retain the outcome. Independently check supervised descendants stopped. |
| Permission refusal | Use a disposable target protected by a known native permission rule. Expect `permission_needed`, unchanged protected content, and no permission bypass or automatic retry. Check the state even if no `error` object is present. |
| Project ownership | While a task is active, submit a separate start with a new key for the same project or an overlapping parent/child path. Expect `project_busy`. After cleanup, a new task can acquire the project. |
| Restart behavior | Save useful results and finish active tasks, then restart the server. An old handle returns `unavailable` with unknown execution outcome; it must not launch replacement work. |
| Consultation regression | Call `clink` with a review prompt and `absolute_file_paths`, omitting `action` and `cli_name`. Expect Codex advice and unchanged files. A subsequent consultation can use its PAL continuation ID. |

A `policy_unverified` launch after a CLI upgrade needs a new verification record. Report the CLI version and error; do not disable the admission check. Useful bug reports include the action, task/attempt identifiers, state/error code, and expected versus observed effects. Exclude credentials and raw private session transcripts.

## All 18 tools

All tools are enabled unless listed in `DISABLED_TOOLS`; `version` and `listmodels` remain available.

| Tools | Behavior |
| --- | --- |
| `chat`, `clink` | Advisory consultation by default. `clink` also exposes the explicit native lifecycle above. `chat` requires `working_directory_absolute_path`. |
| `thinkdeep`, `codereview`, `precommit`, `debug`, `secaudit`, `analyze`, `refactor`, `testgen` | Caller-driven investigation with existing final expert-call conditions. `use_assistant_model: false` skips delegated expert analysis. |
| `consensus` | Sequential consultations by default, with explicit parallel mode. Participants receive the original proposal and declared context, without peer answers; the caller synthesizes. Declared file changes invalidate the comparison. |
| `apilookup` | One verified native search lookup returning structured sources; recognized empty results return `no_results`. |
| `planner`, `docgen`, `tracer`, `challenge` | Local caller-driven guidance without delegated inference. |
| `listmodels`, `version` | Local discovery and provenance. |

The analytical tools still use consultations. Native implementation requires explicit `clink` task actions. Consultation continuations preserve bounded conversation and separate per-tool workflow state; they are process-local and disappear on restart or expiry. They are distinct from native task handles and private CLI session IDs.

## Configuration and execution limits

Client manifests load in this order: bundled clients → legacy `~/.pal/cli_clients` → `~/.clink/cli_clients` → `<bound-project>/.clink/cli_clients` → `CLI_CLIENTS_CONFIG_PATH`. Project configuration requires explicit `--project` launch binding. Each later manifest replaces the entire named client, rather than merging individual fields. See [project setup](docs/project-setup.md), the [manifest example](docs/cli-configuration.md#manifest-example), and the [native task profile](docs/cli-configuration.md#native-task-profile).

Consultations default to `codex/default`. Model selection follows request → role → client → admitted adapter default. Native tasks require explicit CLI selection and use separate native-role and native-profile defaults. All bundled native profiles leave the model to native CLI configuration unless overridden; Claude native tasks do not inherit the advisory `sonnet` default, and native Codex does not inherit the restrictive consultation catalog/default. Omitted, `null`, or `auto` model selection uses the next default. Provider aliases are never translated and backends are never silently switched.

The admitted Codex catalog accepts 12 exact native model IDs; only `gpt-6.1-sol` has been exercised live. Its captured default is passed explicitly when no other model is selected. Requested/effective identity and CLI-reported actual identity remain separate. Consultations receive bounded inline text and admitted explicit images; Codex native file/command tools are disabled. Native tasks currently reject images and legacy sampling controls such as `temperature` and `thinking_mode`.

| Resource | Bound |
| --- | --- |
| Shared process pool | Two running processes by default (`CLINK_MAX_CONCURRENT`), eight queued launches, 30-second maximum queue wait. |
| Attempt deadline | At most 1,800 seconds, including preparation and waiting; a shorter client timeout wins. Follow-ups get fresh deadlines. |
| Native task retention | 64 tasks, 16 attempts per task, 8 MiB of retained serialized data; latest result text at most 20,000 characters. |
| Terminal retention | Three hours without polling extensions. Active tasks are not evicted. Restart loses task handles, session mapping, and deduplication. |
| Prompt and output | Assembled input at most 1 MiB, including Agy's encoded input envelope; captured stdout and stderr at most 10 MiB each. Truncation is reported. |

Native tasks reserve one writer for overlapping canonical project directories and serialize session input. External writers are outside this coordination. Shutdown cancels active tasks and awaits cleanup. The server does not automatically retry, fall back, or recreate lost work; native CLIs may retry internally.

**Risk & Mitigation:** Native agents can change files and invoke external integrations under their configured permissions. Approved roots constrain project selection and server-supplied context; they are not an OS sandbox. Codex's consultation profile also does not provide OS read isolation. Check actual effects after failures, cancellation, or lost handles. Detached descendants and remote MCP jobs can outlive supervised cleanup. The depth guard cannot police recursion through an already-running MCP server. Use disposable acceptance projects and independently verify changes and cleanup.

## Native adapters and admission

Claude, Grok, and Antigravity have admitted native profiles. Grok and Antigravity admission is limited to their recorded 1.0.70 and 1.4.12 macOS arm64 binaries/recipes. Grok reported `grok-4.7-build`; Antigravity did not report its actual model. Antigravity's [admission review](docs/native-agy-admission-review.md) records all four lifecycle checks, preserves ten total calls, and distinguishes proven child read/lifecycle from unverified returned-token content. Native Codex remains disabled. All reuse the same task manager, request-key deduplication, project/session ownership, deadlines, and shared process pool. Setting `native_task.enabled` to `true` cannot replace a genuine installed-version admission record.

| Adapter | Implemented transport and native follow-up |
| --- | --- |
| Grok | `--prompt-file` with JSON output and empty stdin. The bounded prompt file is mode `0600` inside a private `0700` directory, retained through supervised process cleanup. Follow-up uses the private exact UUID with `--resume`. |
| Antigravity | One user NDJSON event on stdin, followed by EOF, with stream JSON output. Follow-up uses `--conversation` with the recorded private identity. |
| Codex | `exec` with JSON output and explicit stdin `-`; follow-up uses `exec resume` with the private UUID. Native configuration is loaded separately from the restrictive consultation recipe. |

The adapters preserve native permission behavior without adding bypasses or interactive approval relays. Grok headless refusals and Antigravity denied actions have explicit parser handling; Codex uses its native `exec` approval semantics. Recognized denials report `permission_needed`; terminal errors or incomplete output cannot establish successful execution. See [native transport and permission details](docs/native-cli-tasks.md#adapter-transports-and-permissions).

Before another candidate is enabled, genuine verification must establish the recorded binary/recipe, applicable configuration and capability inheritance, edit/test, native follow-up, permission refusal, and cancellation after observed effects. Offline fixtures and CLI help do not satisfy that gate. Durable restart recovery, interactive approval relay, PTY/tmux or takeover interfaces, native images, and general recursive scheduling remain separate work.

## Development and evidence

See [native adapter results](docs/native-adapters-results.md) for the shared-runtime checks, Grok and Antigravity admission, and the remaining native Codex output limitation.

The first Claude lifecycle's [recorded results](docs/native-lifecycle-results.md) include **1,338 tests and 16 subtests passing**, with zero failures, errors, or skips, under network and unrelated-home-file isolation. That report also records independent review, check-only formatting, package builds, and installed-wheel discovery of all 18 tools. Its original scope does not establish live admission for additional adapters.

The [Claude native admission](docs/verification/native-claude-2.1.304-admission.json) records genuine file edits, a failing-then-passing test, configured hooks/subagent, synthetic MCP environment inheritance, session follow-up, permission refusal, and cancellation after a known mutation with the synthetic descendant gone. Earlier incomplete attempts remain preserved. This establishes the recorded binary/recipe and synthetic project, not every personal configuration, model, platform, or detached/remote cleanup case.

The [Grok native admission](docs/verification/native-grok-1.0.70-admission.json) combines successful edit/test, hook, specialist, MCP/environment, follow-up and permission checks with a later successful cancellation after mutation. All nine top-level invocations and earlier failures remain recorded. Ordinal8's `uncertain/native_failed` cause remains unknown; ordinal9 passed without proving a production defect was diagnosed or fixed. See the [compatibility review](docs/native-grok-admission-review.md).

The earlier [17-tool live matrix](docs/verification/live-tools-2/README.md) and [separate actual lookup](docs/verification/native-search-1/README.md) cover consultation/local outcomes for all 18 tools. Later search-parser hardening was tested offline, without claiming a repeated live call or raw-output replay.

Install development requirements separately, then run existing tests and check-only formatting:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_native_adapters.py tests/test_grok_native_parser.py tests/test_agy_native_parser.py tests/test_codex_native_parser.py -q
.venv/bin/python -m pytest tests/test_native_tasks.py tests/test_native_server.py tests/test_native_runtime.py tests/test_native_runner.py -q
.venv/bin/python -m pytest tests/test_native_adapter_verifier.py tests/test_native_fixture_trust.py -q
.venv/bin/python -m pytest tests/ -v -m "not integration"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

Real Claude lifecycle verification uses the opt-in `scripts/verify_native_cli.py` harness; Grok, Antigravity, and Codex verification uses `scripts/verify_native_adapters.py`. Inspect their `--help` before an authorized run; model submissions can consume subscription/API quota. Candidate overrides exist only inside the verifier, and successful probes do not automatically enable production execution. The [native usage guide](docs/native-cli-tasks.md#verification-and-testing) explains the checks; historical Claude [reproduction commands](docs/native-lifecycle-results.md) retain their original scope.

The [historical PAL audit](docs/clink-only-audit.md), [baseline](docs/clink-only-baseline.md), and [earlier advisory design](docs/clink-only-design.md) describe earlier stages. The [native lifecycle plan](docs/native-cli-lifecycle-plan.md) and [delegation handoff](docs/native-cli-delegation-handoff.md) record the current direction. Use the configuration, migration, and native usage guides linked above for current behavior; legacy PAL/provider documents are historical.
