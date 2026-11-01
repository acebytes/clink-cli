# Clink-only design

Status: Phase 2 proposal, awaiting explicit approval before product implementation.

This fork will preserve PAL's 18 MCP tools while replacing their hosted-provider calls with advisory calls to installed CLI agents. Codex is the default. The caller retains control of investigation, edits, workflow progression, and synthesis. Existing local workflows remain local.

The source is `7afc7c1cc96e23992c8f105f960132c657883bb1` (PAL 9.8.2, 2025-12-15). GitHub reused authenticated user `acebytes`' existing `zen-mcp-server` fork as `clink-mcp-server`. Branch `dex/clink-only-design` has the personal fork as writable origin; upstream pushes are disabled and the original checkout is untouched. Preserve Apache 2.0, existing notices, and 2025 Beehive attribution.

See the [audit](clink-only-audit.md) for current behavior and [baseline](clink-only-baseline.md) for exact evidence. Pristine MCP 2.3.0 caused seven collection errors. With an install-only MCP `<2` constraint (1.30.0), the corrected isolated run recorded 866 passed, 4 skipped, 16 deselected, and no failures. Earlier attempts remain recorded. CLI authentication, live model calls, and proposed safety contracts are unverified. Exclude four dirty provider-catalog additions; commit the MCP cap separately only after implementation approval.

## 1. Architecture decision

| Option | Effect | Decision |
| --- | --- | --- |
| (a) Turn every tool into a clink role or mode | Collapses schemas and caller-driven protocols into prompt conventions; makes local tools incur unnecessary delegation | Reject for v1 |
| (b) Retain MCP tools and replace actual model invocations through a shared clink backend | Preserves tool names, workflow state, prompts, and existing analysis hooks while sharing subprocess policy | Adopt |

The path is `server.py -> tool/workflow -> immutable CLI request -> runner -> adapter/parser -> normalized result`. Reuse `clink/registry.py`, `clink/models.py`, `clink/agents/`, and parsers. The backend exposes three operations: resolve a client/role, execute one request, and describe capabilities. Tools cannot bypass it with their own subprocess or provider fallback.

Resolve a backend only for delegated work. Startup, discovery, local tools, and conversation lookup must work without keys or installed CLIs. Malformed configuration fails startup; missing executables and unknown authentication status do not.

The server owns transport/state; tools own schemas/protocols; the runner owns launch policy, queueing, deadlines, cancellation, environment, and capture limits. Adapters own verified arguments, capabilities, and parsing. CLI responses cannot authorize edits or advance workflows.

Before async expert calls, replace mutable singletons with per-call tools. A continuation owns shared conversation history plus separate workflow namespaces by tool name and that tool's existing branch identifiers. Switching tools must preserve every other tool's state. Lock the continuation from state read through atomic recording; unrelated continuations may run concurrently. Clean up locks with records and share only immutable schema/registry metadata.

## 2. Prompt and authority contract

Runner-enforced policy takes precedence. The tool prompt, output rules, and consensus stance are authoritative task instructions. Roles supply subordinate expertise/style and cannot change those instructions, weaken policy, or add delegation. Delimit user input, history, files, and retrieved material as data.

Compose sections deterministically, including for single-system-prompt adapters. Output schemas override locale/style; the stance enum overrides conflicting custom stance guidance. Repository instruction text may influence an answer, but cannot grant capabilities beyond enforced policy. Disable hooks/plugins and inherited MCP definitions. Report whether repository-instruction loading is disabled, enabled, or unverified; claim isolation only after a separate adapter test.

Validate required output after parsing. Preserve existing tool-level text fallbacks, but never turn malformed adapter envelopes, error records, or nonzero exits into success. Test conflicting roles and output validation; prompts are not security enforcement.

## 3. All 18 tools

Tool files below are under `tools/`. Named prompt modules are under `systemprompts/`; the audit records detailed current hooks and parameters.

| MCP tool | File and prompt source | Planned behavior |
| --- | --- | --- |
| `chat` | `chat.py`; `chat_prompt.py` | One backend call. Return advice or generated code as content. Remove automatic `pal_generated.code` writes. |
| `clink` | `clink.py`; configured `systemprompts/clink/` role | Direct entry to the shared backend with identical limits, policy, and replay. |
| `thinkdeep` | `thinkdeep.py`; `thinkdeep_prompt.py` | Caller-driven steps; CLI expert analysis only at the final analysis hook. Honor `use_assistant_model=false`. |
| `codereview` | `codereview.py`; `codereview_prompt.py` | Preserve investigation fields, review constraints, and final expert-validation hook. |
| `precommit` | `precommit.py`; `precommit_prompt.py` | Preserve caller-provided Git evidence and final validation. Delegate advice, never commits or edits. |
| `debug` | `debug.py`; `debug_prompt.py` | Preserve hypothesis/evidence steps and final expert analysis. |
| `secaudit` | `secaudit.py`; `secaudit_prompt.py` | Preserve security findings and final expert analysis. |
| `analyze` | `analyze.py`; `analyze_prompt.py` | Preserve caller investigation and final expert analysis. |
| `refactor` | `refactor.py`; `refactor_prompt.py` | Return proposed improvements through the existing workflow; no delegated refactoring writes. |
| `testgen` | `testgen.py`; `testgen_prompt.py` | Return proposed tests and validation through the existing workflow; no delegated test-file writes. |
| `planner` | `planner.py`; `planner_prompt.py` | Keep local caller-driven planning, revisions, and branches. No new CLI call. |
| `docgen` | `docgen.py`; `docgen_prompt.py` | Keep local one-file-per-step guidance and counters. The authorized caller performs any writes. |
| `tracer` | `tracer.py`; `tracer_prompt.py` | Keep local trace guidance and caller-owned investigation. |
| `consensus` | `consensus.py`; `consensus_prompt.py` | Preserve sequential consultations by default; explicit parallel mode below. Caller synthesizes. |
| `apilookup` | `apilookup.py`; inline lookup instructions | Perform one delegated lookup using verified native web search, then return answer and sources. |
| `listmodels` | `listmodels.py`; no model prompt | List configured clients, roles, availability, defaults, and capabilities without model/auth calls. |
| `challenge` | `challenge.py`; inline challenge text | Remain a local prompt wrapper. |
| `version` | `version.py`; local metadata | Remain local; report fork provenance without requiring providers. |

The eight expert workflows retain individual skip rules and caller-driven loops. Intermediate steps never launch a CLI. Final analysis occurs at the existing completion predicate, normally `next_step_required=false`. Fix `thinkdeep`'s explicit expert opt-out and preserve each tool's findings/consolidation.

All 18 tools are enabled in the new default/example configuration. `DISABLED_TOOLS` remains supported, including existing protection for essential discovery/version tools.

## 4. Public selection and response contract

Retain tool names/required fields; add optional `cli_name` and `role` to delegated tools. Defaults are `codex` and `default`. Missing defaults fail without fallback. Local tools do not resolve a backend.

Preserve chat's required `working_directory_absolute_path`. Its canonical, existing absolute directory takes precedence over the selected client's `working_dir` for that chat invocation only, and must be inside an operator-approved root. Resolve symlinks before checking. An out-of-root request returns `path_not_allowed` with configuration guidance; it never enlarges the roots. Other tools use the configured working directory unless their existing contract explicitly supplies one. Executable/policy resolution remains independent of caller cwd.

`model` is a native identifier. Precedence is concrete request value, role `model`, client `default_model`, then native default. Omitted/`null`/`"auto"` use defaults. Remove provider alias translation/catalogs. Native unknown-model errors remain explicit. Record requested and reported models separately; absent actual identity stays unknown.

Retain legacy `temperature`/`thinking_mode` fields and deprecated `DEFAULT_THINKING_MODE_THINKDEEP`. Unsupported explicit settings are unapplied with `unsupported_parameter` warnings. V1 promises no native reasoning-budget mapping; add mappings only through documented adapter tests.

Examples show proposed interfaces, not implemented APIs. The first is `clink`; chat additionally requires `working_directory_absolute_path` under the rules above:

```json
{"prompt":"Explain this change", "cli_name":"codex", "role":"codereviewer", "absolute_file_paths":["/work/project/change.py"]}
```

```json
{"step":"Investigate the timeout", "step_number":1, "total_steps":2, "next_step_required":true, "findings":"Observed after reconnect", "cli_name":"codex", "use_assistant_model":false}
```

Preserve response envelopes/continuation fields; add request ID, backend identity, requested/actual model, duration, status, warnings, truncation, and error code. Native session IDs are not continuation tokens. `listmodels` reports sorted clients/roles, executable availability, verification status, defaults, image/search capability, and read scope. Discovery never logs in or calls models; authentication starts `unknown`.

`apilookup` uses one native-search delegation, with no hidden follow-ups. Success requires adapter evidence of search and source URLs; verified empty searches return no-results. Without search evidence, capability is unavailable. Retain bounded research instructions and never label unsourced model recall as current research.

## 5. Consensus protocol and file consistency

Add `execution_mode` with values `sequential` (default) and `parallel`. Retain `models`, step fields, `findings`, and continuation fields. Require two to eight participant entries. Each entry accepts `cli_name`, `role`, optional `model`, `stance` (`for`, `against`, or `neutral`, default `neutral`), and optional `stance_prompt`.

```json
{"step":"Evaluate splitting the queue worker", "step_number":1, "total_steps":3, "next_step_required":true, "findings":"My initial assessment", "execution_mode":"parallel", "models":[{"cli_name":"codex","stance":"for"},{"cli_name":"claude","stance":"against"}]}
```

Resolve each entry independently using role/client/native defaults for omitted/`null`/`"auto"` models. The inherited top-level `model` receives an ignored-parameter warning. Assign immutable position IDs `participant-1`, etc. Reject duplicates of canonical client, role, effective model selection, stance, and custom stance text. Normalize custom text by line endings/outer whitespace only. Use an internal sentinel for an unknown native default; report actual model identity separately.

Freeze the caller's original `step` proposal before any history enrichment, along with resolved targets, prompt content, stance guidance, order, and declared manifest. Consultant payloads bypass generic canonical replay. Their allowlist is enforced policy/tool instructions, frozen role/stance guidance, original proposal, and explicitly declared files/snippets/images. Neither shared conversation history, caller findings, nor peer responses may enter a consultant prompt, including on later sequential calls. Canonical history may still serve the caller's synthesis. Legacy `model_responses`/`current_model_index` cannot override recorded outcomes or transitions.

Sequential mode preserves one consultation per MCP step. Parallel mode runs the set through the global pool and returns input-ordered outcomes. The caller synthesizes in both modes. Failures are terminal participant outcomes; retain successes with partial status rather than claim complete consensus.

V1 has no participant-retry API. Exact duplicates of completed steps return recorded outcomes only after required file checks. Changed input for an occupied step is a transition conflict, never a stale cache hit. After failure, the caller may continue remaining sequential participants or explicitly start a new consensus with new identity/spending. Cancellation leaves incomplete status. Never automatically retry, restart, or substitute backends.

Use change detection, without persistent source snapshots. Admit at most 256 explicit regular files/images and 64 MiB total bytes; reject directories. Enforce context roots and record logical path, canonical symlink target, and SHA-256 in an ordered manifest. Hash incrementally and reject changes during initial admission.

Recheck before each launch/sequential advancement, after each completion, and before the completed aggregate. Changed/missing/inaccessible files or retargeted symlinks invalidate the consensus. Stop pending work, cancel children, mark existing responses invalid, and require a new consensus. Never mix detected file states.

Report `unchanged_at_checks`, not snapshot consistency. Hashes cannot detect changes restored between checks and cover declared files only. Instruct consultants to request undeclared context; this is not read isolation. Prefer exact model selection for reproducibility because native defaults can change externally.

## 6. Context and conversation continuity

Keep instructions/findings/diffs/snippets inline; supply validated paths for file-reading adapters. Resolve paths and symlink targets within the configured workspace/approved context roots. These constrain server-supplied context, not every child read. Do not embed whole repositories.

Replace provider-window allocation with a 1 MiB UTF-8 assembled prompt, including at most 128 KiB history. MCP transport limits remain independent; token estimates are estimates. Preserve required instructions/current request/frozen proposal, trim oldest complete history turns with omission metadata, and reject oversized required content before launch. No hidden summarization call. Direct file manifests share the 256-file/64-MiB bounds. General tools may accept directory references without recursive embedding or consensus consistency claims.

Retain server UUID continuations and in-memory storage, default 50 turns/three hours. Store structured turns, bounded outcomes, workflow state, file references, backend identity, and attempt status. Bound admission to 256 active conversations/64 MiB serialized data. Expire old records first; reject capacity overflow without evicting live threads. Store no raw streams/source snapshots.

Reserve turn/storage capacity before launch and atomically commit terminal outcomes, distinguishing failures from successful turns. Unknown/expired/full continuations fail explicitly. Restart loses memory. Canonical replay supports cross-tool/CLI continuation; native resume is out of scope.

Within each tool namespace, address a transition by step number and existing branch/revision fields (including `revises_step_number`), and attach a digest of canonical validated input. An exact address/digest repeat may return its terminal outcome. A different digest at an occupied address must be an explicitly valid revision/branch under the tool's existing protocol or return `workflow_conflict`. Preserve planner revisions/branches without returning stale content. Server-owned backend results never come from caller-supplied history fields.

Inject replay into the consumed field, including workflow `step`, without provider `ModelContext`, except that consensus consultant payloads bypass replay as specified above. Reload references and disclose changes. If a new adapter cannot accept retained images, fail before launch and offer a new text-only conversation.

## 7. Advisory execution and adapter verification

Advisory-only prohibits delegated content edits, write tools, mutating shell access, and recursive delegation. Native auth/cache maintenance may write operational files. Installation/help evidence is not authentication or live-policy evidence.

| Adapter | Required execution policy | Read authority and current evidence |
| --- | --- | --- |
| Codex | JSON output; read-only sandbox; approval policy `never`; ephemeral sessions; ignore user configuration and execpolicy `.rules`; exclude inherited MCP definitions | Installed 0.166.0 help supports the main controls. Read access may extend beyond the workspace. Project-MCP isolation and live no-write behavior require tests. `--ignore-user-config` alone does not isolate MCP; `--ignore-rules` does not suppress `AGENTS.md`/repository instructions. |
| Claude | Print/JSON mode; `--restricted`; tools `Read,Glob,Grep,WebSearch,WebFetch`; empty `--strict-mcp-config`; `--permission-prompts none`; safe mode; no session persistence | Installed 2.1.304 help supports the proposed controls. Restricted file-tool scope must be recorded for the supported version. This is tool restriction, not an OS sandbox. Authentication and live behavior are unverified. |
| Gemini | Verified headless policy denying writes, shell execution, other MCP tools, and escalation; extensions disabled | Not installed and unavailable for v1 execution until version-specific contract tests pass. `--yolo` is prohibited. Plan mode alone is insufficient because escalation behavior can broaden authority. |

Codex requires `--sandbox read-only`, `--ask-for-approval never` (or verified exec equivalent), `--json`, `--ignore-user-config`, `--ignore-rules`, and `--ephemeral`. See the [command](https://learn.chatgpt.com/docs/developer-commands) and [security](https://learn.chatgpt.com/docs/agent-approvals-security) references. `--ignore-rules` skips execpolicy `.rules` only. Repository text cannot weaken sandbox/tool controls; suppressing instruction loading requires separate verified controls. Preserve native auth and refuse execution unless both user/project MCP definitions are excluded.

Claude must retain OAuth, without `--bare`; verify empty MCP, file scope, and hooks/plugins against its version. Gemini policy research is not proof of safe execution. Permission failure never permits automatic policy escalation.

Set immutable child `CLINK_DELEGATION_DEPTH=1`; a nested server refuses delegation. This cannot block access to an already-running server, so empty/disabled MCP configuration is also mandatory. Exclude subagent tools/external bridges; callers/roles cannot change this policy.

Allowlist child environment: `PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `TMP`, `TEMP`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`; add verified auth-location variables such as `CODEX_HOME`/`CLAUDE_CONFIG_DIR` and validated operator additions. Exclude provider keys, unrelated secrets, and policy bypasses. Native stores own OAuth/login; never copy credentials to prompts or normal diagnostics.

## 8. Runner limits and failure semantics

Use argv without a shell, resolve the admitted executable once, close stdin after the bounded prompt, and parse incrementally. Role/client arguments cannot override policy. Terminal/error records and exit status jointly determine success.

| Bound | V1 default/contract |
| --- | --- |
| Running delegated processes | 2 globally; configurable positive integer |
| Waiting launches | 8 globally; queue-full rejects immediately |
| Queue wait | At most 30 seconds, within the overall deadline |
| Request deadline | 1,800 seconds from MCP admission; client `timeout_seconds` may lower it |
| Parallel consensus | One shared deadline; every participant uses the global pool |
| Child cleanup | On cancellation/timeout, terminate the process group; after 5 seconds kill remaining members, reap, and release permits in `finally` |
| Captured stdout/stderr | 10 MiB each as enforced byte ceilings; overflow cancels the process and returns an error |
| Returned delegated content | 20,000 characters; explicit truncation metadata, with existing MCP output budget also enforced |
| History / assembled prompt | 128 KiB / 1 MiB UTF-8 as described above |

Lock waits consume the deadline. Recheck after state acquisition and before launch; queued cancellation removes the waiter. Ordinary shutdown terminates children. Test descendants, permit release, and draining; buffer size is not total-output enforcement. Built-ins use stdout; reject generic output-file capture until bounded, symlink-safe capture/cleanup is implemented by an adapter.

Return a code/remedy for `invalid_config`, `cli_missing`, `unsupported_platform`, `unsupported_capability`, `policy_unverified`, `recursion_blocked`, `authentication_required`, `rate_limited`, `queue_full`, `queue_timeout`, `timeout`, `cancelled`, `nonzero_exit`, `parse_error`, `empty_response`, `output_limit`, `input_limit`, `path_not_allowed`, `workflow_conflict`, `continuation_unavailable`, `conversation_capacity`, and `consensus_invalidated`. Classify authentication/rate limits only from known signatures; bound/redact excerpts.

Never automatically retry, fall back, or repair output through another model call. Return observed retry-after guidance without acting. Explicit new calls can incur usage. Native CLIs may retry internally; killing them cannot guarantee cancellation of submitted remote work or undo usage.

Rotate metadata-only logs: request ID, tool/client/role, timing, queue wait, outcome/exit status, byte counts, truncation. Exclude contents, prompts, argv, credentials, and raw session/receipt bodies.

## 9. Configuration and environment migration

Keep JSON manifests/registry. Add `schema_version: 1`; absent versions receive legacy migration diagnostics. Infer missing adapters only for the three built-in names. Reject unknown fields/unsafe arguments. Custom names select a registered adapter; no dynamic imports or caller-defined commands.

Example operator-owned manifest; paths and model choices are illustrative:

```json
{
  "schema_version": 1,
  "name": "codex",
  "adapter": "codex",
  "command": "codex",
  "working_dir": "/work/project",
  "allowed_roots": ["/work/project"],
  "default_model": null,
  "timeout_seconds": 1800,
  "additional_args": [],
  "env": {},
  "roles": {
    "default": {"prompt_path": "/work/prompts/default.txt"},
    "codereviewer": {"prompt_path": "/work/prompts/reviewer.txt", "model": null, "role_args": []}
  }
}
```

Keep `command`, `working_dir`, `additional_args`, `env`, role `prompt_path`/`role_args`/`description`, subject to policy validation. New fields are version, adapter, roots, and model defaults. Relative paths resolve against the declaring file. Omitted working directory uses server startup cwd; omitted roots allow that configured/startup directory only, even when chat supplies a different cwd. Configure workspace roots explicitly; caller paths cannot alter executable/policy configuration.

Parse legacy commands without a shell. Allow only recognized safe extra/role arguments; reject conflicting model/output/permission/config/resume/MCP options. Use structured model fields. Reject built-in `output_to_file`; use native login stores, not hardcoded secrets. Operator wrappers are trusted code requiring the same contract tests.

Precedence: bundled `conf/cli_clients` < deprecated `~/.pal/cli_clients` < `~/.clink/cli_clients` < `CLI_CLIENTS_CONFIG_PATH` (file/directory). Last source replaces the whole client; duplicates within one source fail. Validate atomically with stable diagnostics. V1 reload requires restart. Document changed explicit-path precedence.

| Existing environment setting | V1 treatment |
| --- | --- |
| `CLI_CLIENTS_CONFIG_PATH` | Keep; highest-priority explicit manifest source |
| `DISABLED_TOOLS` | Keep; default empty |
| `LOCALE` | Keep |
| `LOG_LEVEL` | Keep; new example defaults to `INFO` |
| `CONVERSATION_TIMEOUT_HOURS`, `MAX_CONVERSATION_TURNS` | Keep; defaults 3 hours / 50 turns; align contradictory example comments |
| `MAX_MCP_OUTPUT_TOKENS` | Keep transport budgeting; not a native CLI context-window claim |
| `PAL_MCP_FORCE_ENV_OVERRIDE` | Keep existing `.env` precedence semantics for compatibility, default false; deprecated name, no new alias in v1 |
| `DEFAULT_THINKING_MODE_THINKDEEP` | Deprecated compatibility setting; no unverified native reasoning mapping |
| `DEFAULT_MODEL` | Drop provider-default meaning; warn when set and direct users to client/role `default_model`/`model` |
| `COMPOSE_PROJECT_NAME`, `TZ` | Keep deployment controls; example compose name becomes `clink-mcp` |
| `LOG_MAX_SIZE` | Existing container setting, not a new Python logger guarantee; document the actual rotation configuration |
| `PYTHONUNBUFFERED`, `PYTHONPATH` | Keep normal deployment/runtime use; never treat them as backend selection |

| New server setting | Definition |
| --- | --- |
| `CLINK_DEFAULT_CLI` | Default `codex`; must name a configured client |
| `CLINK_MAX_CONCURRENT` | Default 2; positive integer; does not bypass queue/deadline limits |
| `CLINK_DELEGATION_DEPTH` | Internal recursion marker; inherited nonzero depth disables delegation; not an operator relaxation switch |

Other bounds are v1 constants. Invalid new settings fail validation; retained legacy conversation settings preserve documented correction/fallback diagnostics.

Remove these server provider settings after all ports; warn by key name only if obsolete settings are found, never print values:

| Removed group | Exact names |
| --- | --- |
| Credentials | `GEMINI_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `XAI_API_KEY`, `DIAL_API_KEY`, `OPENROUTER_API_KEY`, `CUSTOM_API_KEY` |
| Legacy template-only keys | Remove `GOOGLE_API_KEY` and `ANTHROPIC_API_KEY` from server examples/config guidance; do not forward them to children or reinterpret them as subscription login |
| Endpoints/version/defaults | `GEMINI_BASE_URL`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `DIAL_API_HOST`, `DIAL_API_VERSION`, `CUSTOM_API_URL`, `CUSTOM_MODEL_NAME` |
| Model restrictions | `OPENAI_ALLOWED_MODELS`, `GOOGLE_ALLOWED_MODELS`, `XAI_ALLOWED_MODELS`, `DIAL_ALLOWED_MODELS`, `OPENROUTER_ALLOWED_MODELS`, `AZURE_OPENAI_ALLOWED_MODELS`, `AZURE_ALLOWED_MODELS`, `CUSTOM_ALLOWED_MODELS` |
| Catalog paths | `OPENAI_MODELS_CONFIG_PATH`, `GEMINI_MODELS_CONFIG_PATH`, `XAI_MODELS_CONFIG_PATH`, `DIAL_MODELS_CONFIG_PATH`, `OPENROUTER_MODELS_CONFIG_PATH`, `AZURE_MODELS_CONFIG_PATH`, `CUSTOM_MODELS_CONFIG_PATH` |
| Provider HTTP controls | `CUSTOM_CONNECT_TIMEOUT`, `CUSTOM_READ_TIMEOUT`, `CUSTOM_WRITE_TIMEOUT`, `CUSTOM_POOL_TIMEOUT`, `OPENROUTER_REFERER`, `OPENROUTER_TITLE` |

Keep child filtering separate from `.env` loading/native login. Remove installer/Docker API-key checks and provider bootstrap. Never auto-login, copy credentials, or silently rewrite user CLI/MCP configuration.

## 10. Compatibility and removals

| Surface | Migration decision |
| --- | --- |
| MCP tools and workflows | Retain names/required workflow fields and existing response envelopes; add optional routing fields |
| Chat working directory | Retain required `working_directory_absolute_path`; request cwd wins within operator roots, with explicit rejection/migration guidance outside them |
| All-tools default | Six tools disabled by the old example become enabled; explicit `DISABLED_TOOLS` still works |
| Images | Codex accepts validated absolute local paths via verified `--image`; base64 inputs are rejected in v1 |
| Claude/Gemini images | Reject before launch; do not silently drop images |
| Consensus images | Preflight all participants and reject the entire request before launch if any selected adapter cannot accept required images |
| Consensus identity/context | Two to eight entries, full target/stance/custom-guidance identity, explicit files and bounded change detection |
| Provider aliases/controls | Native model identifiers replace provider routing; unsupported legacy tuning receives warnings |
| Generated code | Content returned to caller; no server-created `pal_generated.code` |
| Config overrides | Explicit config path becomes highest priority; dangerous legacy arguments require migration |
| macOS/Linux | V1 delegated execution targets verified POSIX process-group cleanup |
| Windows | Delegation returns `unsupported_platform` until equivalent descendant cleanup is implemented/tested; local tools may operate |
| Python | Preserve declared Python 3.9+ pending baseline verification; record tested versions separately and disclose any minimum-version blocker |

After replacement coverage and zero retained imports, delete `providers/`, provider enums/registries/capabilities/restrictions/retry/cache logic, provider-only `conf/*_models.json`, obsolete tests/scenarios, and API-key validation. Keep `conf/cli_clients/`.

Replace coupling in `server.py`, `tools/simple/base.py`, `tools/workflow/workflow_mixin.py`, `tools/consensus.py`, `tools/shared/base_tool.py`, `utils/model_context.py`, `utils/model_restrictions.py`, and `utils/conversation_memory.py`. Retain useful file/image/token/storage helpers and relocate genuinely shared response types before deleting providers.

Then remove `google-genai`/`openai`; retain `mcp>=1.0.0,<2`, `pydantic`, `python-dotenv`, and verified packaging compatibility dependencies. Update README, setup/examples, tool docs/prompts, metadata, and tests while preserving upstream attribution.

## 11. Implementation order, verification, and rollback

After approval, separate preparation from behavior changes in small commits. Execution uses configured non-Astra models (standard Sol/ultra); independent Codex review uses Sol/high. Record effective model settings from metadata, not role names.

1. Commit the MCP `<2` compatibility fix separately, referencing pristine and constrained baseline results.
2. Introduce backend request/result contracts, safe adapter configuration, bounded runner, fake-executable tests, and policy gates. No workflow protocol changes in this preparation commit.
3. Isolate per-call tool state and provider-independent continuation storage/replay, with concurrency and expiration tests.
4. Port `clink` and `chat`; implement default selection, metadata, image admission, and content-only code generation.
5. Port final expert hooks and each affected workflow in small groups; preserve intermediate steps and expert opt-outs. Verify local planner/docgen/tracer remain free of delegation.
6. Port sequential consensus, then add explicit parallel mode and file-change invalidation in a separate commit. Add real lookup and client discovery.
7. Prove provider-free startup and every-tool behavior, then delete providers/catalogs/dependencies and update installers/docs. Do not leave runtime provider fallback paths.
8. Run final nonintegration, packaging/import, and opt-in CLI checks. Report every tool's smoke coverage and compare against the recorded baseline.

Use fake executables/output fixtures without credentials. Cover all parsers' valid/streamed/error/nonzero/malformed/empty/oversized output; all tools' launch counts, prompts, schemas, completion/skip predicates, and local behavior; and residual provider imports.

State tests cover per-tool state survival across tool switches, serialization/concurrency, exact repeats versus changed inputs, planner revisions/branches, capacity/expiry, replay, cancellation, and atomic outcomes. Runner tests cover chat cwd/root/symlink boundaries, queues, descendants/permit cleanup, overflow, recursion, unsafe config, and redaction. Consensus tests cover identities/defaults/stances, ordering, partial results, duplicates, between-step/mid-parallel mutations, deletion/symlinks, and no automatic retry/synthesis. A sequential-continuation regression must assert that prior chat history, caller findings, and earlier consultant answers are absent from every later consultant payload.

Real CLI tests are opt-in, using disposable workspaces and recording versions, requested/actual models, auth outcomes, and workspace hashes. Probe edits, nested MCP, hooks/plugins, and cancellation. Help output is insufficient. Report manual smoke results for every tool, naming at least one real CLI for each delegation and local checks for local tools. Mark unavailable coverage unverified. Use no production/user records or secrets.

Run `python -m pytest tests/ -v -m "not integration"`, check-only Ruff/Black/isort, and packaging/import validation in isolation. Retain baseline failures and dependency details. Port provider simulator scenarios before counting them as clink coverage. Real tests require explicit opt-in and disclose possible usage.

Rollback uses focused revert commits and the pinned revision/environment. Never automatically restore dangerous flags or hosted-provider fallback. Operator-controlled deployment rollback/restart may lose ephemeral conversations. No database migration or durable snapshot store is introduced.

Risk and mitigation: isolate/lock mutable state; enforce subprocess policy; filter environments and bound logs; prohibit automatic retries after uncertain remote completion; describe boundary hashing honestly. CLI-version differences in auth, retries, and read scope remain verification gates.

## 12. Decisions, verification gates, and approval

| Question | Recommended answer/status |
| --- | --- |
| Which architecture? | Keep MCP entries and shared backend, option (b) |
| Default CLI and permission posture? | Codex; advisory-only, already confirmed |
| Local planner/docgen/tracer behavior? | Preserve caller-driven local workflows, already confirmed |
| Consensus behavior? | Sequential default with explicit parallel opt-in, already confirmed |
| Continuity and retries? | Canonical replay; no native resume or server-managed retries in v1 |
| Project name? | `clink-mcp-server`, requested and recommended. Alternatives: `Clink Agent Bridge`, `CLI Council`; no further rename is proposed. |
| Gemini availability? | Keep unavailable until installed-version policy tests pass; do not delay other supported adapters for speculative flag recipes |
| Codex/Claude readiness? | Require authentication and live policy/smoke evidence during approved implementation; currently unverified |
| Test baseline? | Use the linked baseline report; distinguish pristine blockers, compatibility-constrained runs, and final results |
| Minimum Python/platform claims? | Preserve declaration while verifying it; support only tested execution-policy platforms and disclose gaps |

No further product-preference interview is needed. If an adapter fails this contract, disclose the limitation and keep it unavailable while completing independent work.

Planning provenance: dedicated planning role, requested Astra/ultra; effective runtime evidence unavailable/unverified. This is proposed behavior, not implementation or an independent review verdict.

**Stop after Phase 2. The conductor must present the audit, baseline, this design, and genuine independent review results, then obtain explicit user approval before any Phase 3 product-code change. Authorization to prepare the fork and these documents does not approve implementation.**
