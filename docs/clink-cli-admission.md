# Antigravity and Grok admission investigation

Confidence: ✅ High for the installed Antigravity/Grok identities and the decision to keep these two adapters unavailable; their live execution policy remains unverified.

This implementation-phase investigation records the earlier advisory consultation contract in [the advisory design](clink-only-design.md#7-advisory-execution-and-adapter-verification). Its suppression gates and no-model-call statements retain that scope; they do not define the separate native task objective. Installation and authentication reported by the operator do not prove that a subprocess excludes writes, inherited MCP servers, hooks, plugins, or recursive delegation. Neither `agy` nor `grok` has passed that advisory gate. Do not admit advisory execution from the partial controls below. Claude native task admission is separate; see the next section. Antigravity/Grok currently have parser/discovery support only, with no native execution admission.

## Native task admission is separate

The [native lifecycle plan](native-cli-lifecycle-plan.md) and [delegation handoff](native-cli-delegation-handoff.md) supersede the old capability-suppression objective for explicit native actions only. Omitted `clink.action` and `action: "consult"` retain advisory behavior; explicit `start`/`status`/`followup`/`cancel` use a separate profile. See [native task usage](native-cli-tasks.md).

The [Claude Code 2.1.304 native admission](verification/native-claude-2.1.304-admission.json) binds the exact recorded executable and launch recipe. A changed binary/recipe fails closed and requires reverification. Configuration/enablement, executable presence, operator-reported authentication, and synthetic tests are insufficient. Native tasks intentionally preserve configured tools, hooks, skills, plugins, MCP integrations, and subagents under native permissions; suppressing them is not the native acceptance criterion. No permission bypass or approval relay is authorized. The existing Codex recipe, catalog, defaults, and receipts remain specific to consultation.

Genuine attempts 2/3 establish file edits, a native failing-then-passing test, project instructions/hooks, a configured subagent, synthetic MCP/environment-name inheritance, same-native-session follow-up, `permission_needed`, and cancellation after known mutation with the synthetic descendant absent after cleanup. [Native lifecycle results](native-lifecycle-results.md) retain attempt 1's failure and attempt 2's incomplete native-test/cancellation coverage, rather than replacing them with the eventual passing gates. The evidence covers the recorded binary/recipe and synthetic configuration, not every model, user configuration, platform, detached descendant, or remote MCP operation. Server shutdown cleanup has separate synthetic coverage. Terminal execution success is distinct from task fulfillment, and agent-reported changes/checks remain unverified unless independently checked. Unknown mandatory behavior blocks native launch. Other adapters do not acquire native support from this Claude implementation.

Preserve prior advisory failures, invalidated acceptances, and raw-evidence exclusions. The historical findings below are not rewritten as native failures or native acceptances. Native cleanup must disclose detached/remote work that can survive process-group termination; context roots do not establish complete native read/write isolation.

## Separate Codex and Claude evidence

The historical sections below record the bounded `agy`/`grok` investigation; the native section above links its separate later evidence. The [Codex/Claude verification report](verification/cli-policy/README.md) records separate control and live probes. Its [current Codex 0.166.0 admission](verification/cli-policy/codex-0.166.0-admission-v5-search.json) binds the exact tested macOS arm64 binary, restrictive recipe, and frozen model catalog. Advisory text and explicit local images passed the strict production parser, and a real backend lookup returned 20 structured source URLs; only `gpt-6.1-sol` was exercised live. The 12 eligible exact native IDs and genuine captured default are frozen until explicit verified refresh. Earlier v1/v2 acceptances were invalidated for backend usability and remain preserved.

Native search is now verified from the [separate actual lookup](verification/native-search-1/README.md). Its parser hashes are recorded separately from later offline URL/Unicode and literal `is_error` hardening; the live call was not rerun under that final hardening and no unretained native-output replay is claimed. That advisory report records Claude 2.1.304 as `policy_unverified`, including unresolved managed hooks/policy authority, with no Claude model prompt sent in that investigation. The [17-tool matrix](verification/live-tools-2/README.md) and separate lookup cover all 18 tool outcomes; see [implementation results](clink-implementation-results.md) for integrated verification and limits. The no-model-call statements below apply only to this Antigravity/Grok investigation.

## Scope and provenance

- Workspace: `/Users/d/Tools/clink-mcp-server`; evidence gathered on 2026-11-01 UTC (2026-10-31 local).
- Read `AGENTS.md`, `CLAUDE.md`, and `docs/clink-only-design.md`; `CLAUDE.local.md` is absent.
- Local inspection used version/help, executable metadata, narrow embedded-string checks, and shipped public documentation. No credential files, credential values, or unrelated user configuration were read. Host CLI settings were not changed.
- Current documentation lookup started with Context7: `/websites/antigravity_google_cli` and `/xai-org/grok-build`. Primary documentation and installed documentation supplied the final evidence. Current web documentation is not proof of the installed binary's implementation.
- This `agy`/`grok` investigation performed no model calls, login checks, MCP connections, hook probes, or plugin enumerations. Model/authentication outcomes for those two CLIs therefore remain unknown, despite operator-reported authentication. This statement does not apply to the separate live Codex verification linked above.
- Delegation: admission investigation used explicitly selected `gpt-6.1-sol` / `ultra`; the Grok subtask used the same selection. Effective runtime model/effort evidence is unavailable and remains unverified.
- Only this document and synthetic files under ignored `test_output/cli-admission/` belong to this investigation. No commits were created. `/private/tmp` is not the sole location of any result.

## Installed identities and admission

| Executable | Identity observed locally | Fingerprint (SHA-256) | Admission |
| --- | --- | --- | --- |
| `/Users/d/.local/bin/agy` | Antigravity CLI `1.4.12`; arm64 Mach-O binary | `34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3` | Unavailable: `policy_unverified` |
| `/Users/d/.local/bin/grok` | Symlink to `/Users/d/.grok/bin/grok`; xAI Grok Build `1.0.70 (f50904eaee6c)`; arm64 Mach-O binary | `5c8a688251bf61e35370b828b6a353b0a20fcc3f64000de8a91d426039f29cec` | Unavailable: `policy_unverified` |

Grok Build is a distinct CLI from the third-party `superagent-ai/grok-cli`; flags and parsers must not be borrowed across that identity boundary. Discovery may report executable presence and these observations, but must not imply a verified read-only capability or verified authentication.

## Antigravity 1.4.12

### Verified interface, partial controls, and blockers

Local `agy --help` exposes `--print`/`-p`, `--input-format text|stream-json`, `--output-format text|json|stream-json`, `--model`, `--mode accept-edits|auto|plan`, `--sandbox`, `--disable-slash-commands`, and session-only `--settings`. The installed timeout help says `0` waits until completion; the current website says a five-minute default. A runner must enforce its own deadline rather than assume either default.

`plan` changes instructions by prefixing `/plan`; it does not independently establish a deny boundary. [Execution-mode documentation](https://antigravity.google/docs/cli/modes)

The documented permission engine gives deny rules precedence and supports global wildcards. A candidate session setting can deny `write_file(*)`, `command(*)`, `unsandboxed(*)`, `execute_url(*)`, and `mcp(*)`. These are documented rules, not a tested safe launch recipe. `command(*)` covers commands inside and outside the terminal sandbox. [CLI permissions](https://antigravity.google/docs/permissions?tab=cli)

Headless mode alone permits workspace writes. Approval-denied tool requests may still produce exit status zero, so a zero exit cannot establish either tool success or policy enforcement. [Headless permissions](https://antigravity.google/docs/cli/headless#permissions-in-headless-mode)

The remaining gates prevent admission:

| Gate | Evidence | Decision |
| --- | --- | --- |
| Exclude inherited MCP definitions | Global and workspace configurations are loaded, and enabled plugins may add servers. A tool deny does not establish that server processes/connections were never started. [MCP configuration](https://antigravity.google/docs/mcp?tab=cli) | Unverified; unavailable |
| Disable all hooks | Hooks load from global, workspace, and plugin files. Session-start hooks run before the first model invocation. No per-run global hook exclusion was found. [Hooks](https://antigravity.google/docs/hooks?tab=cli) | Unverified; unavailable |
| Make `--settings` replace inherited hook policy | The settings schema explicitly marks `hooks` as deprecated and not loaded from settings. Unknown keys are accepted, so an invented `disableAllHooks` key could silently fail. [Settings schema](https://antigravity.google/schemas/v1/settings.json) | Rejected as a workaround |
| Disable plugins and subagents | Enabled plugins contribute automatic hooks, rules, MCP servers, and background subagents. `--disable-slash-commands` only disables slash/skill expansion; no complete per-run exclusion was verified. [Plugins](https://antigravity.google/docs/plugins?tab=cli), [settings](https://antigravity.google/docs/settings?tab=cli) | Unverified; unavailable |
| Preserve login while isolating configuration | No verified auth-preserving isolated configuration root was established. Repointing `HOME`, copying credentials, or changing the user's global settings was not attempted. | Unverified; unavailable |

The installed executable is native, not a locally inspectable source package. Narrow string matches are supporting identification only; missing strings are not proof that a feature is absent. The public [Antigravity CLI repository](https://github.com/google-antigravity/antigravity-cli) provides documentation/examples rather than a reviewed implementation of this installed binary.

### Parser contract for future verification

Documented JSON output has `status`, `response`, optional `error`, `conversation_id`, duration/turn counters, and token usage. Require `SUCCESS`, nonempty text, and zero exit together; reject error or nonterminal states. NDJSON uses `event` discriminators `init`, `step_update`, and terminal `result`, with the final envelope nested under `result`. Prefer terminal response text over concatenating deltas. Do not treat the conversation ID as a server continuation token. The documented init model can reflect an explicit override; it does not prove the actual native default model. [Headless protocol](https://antigravity.google/docs/cli/headless)

Synthetic example, not a captured model response:

```json
{"conversation_id":"synthetic-conversation","status":"SUCCESS","response":"Synthetic advisory result.","duration_seconds":0.01,"num_turns":1,"usage":{"input_tokens":1,"output_tokens":1,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":2}}
```

Disposable parser examples are stored durably in the ignored project directory as `test_output/cli-admission/agy-success.synthetic.json`, `agy-error.synthetic.json`, and `agy-stream.synthetic.ndjson`. They contain no native session records or credentials. They are syntax-checked examples, not adapter tests or evidence of live safety.

## Grok Build 1.0.70

### Verified interface, partial controls, and blockers

The symlink chain ends at `/Users/d/.grok/downloads/grok-macos-aarch64`. Observed version JSON is `{"currentVersion":"1.0.70 (f50904eaee6c)","channel":"unknown"}`. The installed layout is consistent with xAI's [release installer](https://github.com/xai-org/grok-build/blob/main/README.md), but installation history and correspondence to a reviewed source revision are unverified. The installer was not executed.

Installed help confirms `--single`, `--prompt-file`, `--prompt-json`, output formats `json`, `streaming-json`, `streaming-messages-json`, `--sandbox`, `--permission-mode`, `--tools`, `--disallowed-tools`, `--deny`, and `--no-subagents`. Permission-mode names include `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, and `plan`. `--no-subagents` is a verified interface control; its live behavior has not been probed. None of these observations establish a safe full launch command.

| Gate | Installed-version evidence | Decision |
| --- | --- | --- |
| Exclude inherited MCP definitions | A tool allowlist retains always-on MCP meta-tools. Invalid tool names may warn without making the remaining tool set empty. [Installed headless guide](/Users/d/.grok/docs/user-guide/14-headless-mode.md:125) | Unverified; unavailable |
| Replace inherited configuration | `GROK_CONFIG`/`GROK_CONFIG_PATH` provide deep-merged, allowlisted soft-setting overlays, rather than complete configuration replacement. They cannot be assumed to clear native hooks/MCP discovery. [Installed configuration guide](/Users/d/.grok/docs/user-guide/05-configuration.md:23) | Rejected as an isolation shortcut |
| Disable hooks/plugins | Native global, config, plugin, and compatibility hooks merge. Compatibility toggles do not establish native hook exclusion. No verified per-run suppression of all such sources was found. [Installed hooks guide](/Users/d/.grok/docs/user-guide/10-hooks.md:63) | Unverified; unavailable |
| Enforce read-only execution | The documented read-only sandbox still permits reads broadly and writes in operational locations. Built-in sandbox enforcement can fail open; macOS child-network restrictions are ineffective. Stronger custom profiles were not configured or tested. [Installed sandbox limitations](/Users/d/.grok/docs/user-guide/18-sandbox.md:198) | Built-in profile alone is insufficient |
| Preserve native auth and prove all boundaries together | No full protective command was established, so no model call or adversarial probe was made. | Unverified; unavailable |

The corresponding primary references are the official [headless guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md), [configuration guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/05-configuration.md), [hooks guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md), and [sandbox guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/18-sandbox.md). The local shipped documents are the version-adjacent evidence; current `main` may differ from the installed release.

### Parser contract for future verification

The shipped [protocol reference](/Users/d/.grok/docs/user-guide/14-headless-mode.md:173) documents distinct dialects:

- `json`: response text is `text`, alongside `stopReason`, `sessionId`, `requestId`, and `cwd`, with optional accounting. Failure has `type: "error"`, `message`, and nonzero exit.
- `streaming-json`: NDJSON `text` records use `data`; the terminal record has `type: "end"` and `stopReason`. Error records use `type: "error"` and `message`.
- `streaming-messages-json`: terminal records use `type: "result"`, `subtype`, `is_error`, `result`, and `stop_reason`. Known error subtypes include `error_max_turns`, `error_during_execution`, and `error_max_structured_output_retries`, with `errors`. Model identity may appear in init/assistant records or remain `unknown`.

Do not apply Antigravity's `response`/`status` parser or Claude's protocol solely because a JSON key resembles one. Every supported dialect needs installed-version fixtures, a terminal-success predicate, and joint exit-status validation. Grok identifiers do not become server continuation tokens. Authentication, actual model, native policy, and cancellation remain unverified.

Synthetic error fixture, not captured output:

```json
{"type":"error","message":"Synthetic error."}
```

`test_output/cli-admission/grok-error.synthetic.json` holds the syntax-checked synthetic example. No invented success stop reason is presented as live evidence.

## Required verification before Antigravity/Grok advisory admission

1. Establish version-specific controls that exclude every inherited MCP source and hook/plugin execution before startup, while retaining the native login mechanism.
2. Establish enforced write/command/subagent denial with no escalation or fail-open path. Record native file-read authority honestly; workspace hashing is not read isolation.
3. Only then run explicit opt-in probes in a disposable workspace with synthetic files and hook/MCP canaries. Check hashes, denied write/shell attempts, canary absence, effective tool lists, cancellation, terminal/error records, and the actual model when reported.
4. Add installed-version parser and contract tests, then make an explicit admission decision. Keep failures, unavailable gates, and prior attempts in the evidence; do not automatically retry or fall back.

Risk & Mitigation: the advisory architectural boundary is subprocess authority. Hooks or MCP startup can execute independently of the model's allowed tools. Keep both advisory adapters unavailable with `policy_unverified` until their suppression boundaries pass live tests. The native profile instead needs affirmative capability/permission/lifecycle evidence as described above. Installation, authentication, and JSON support prove neither profile's admission.

## Verification performed for Antigravity/Grok

Only local metadata/help and documentation reads were needed for these two decisions. Synthetic JSON/NDJSON fixtures were parsed with Python's standard `json` module. Product tests, authenticated live calls, no-write probes, and hook/MCP canary probes are not claimed by this `agy`/`grok` investigation; the separately linked Codex evidence records its own tests and limitations.
