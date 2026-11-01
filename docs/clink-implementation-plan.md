# Clink implementation boundaries

The user explicitly approved Phase 3 after reviewing the Phase 1/2 documents. The pending-approval wording retained in [the design](clink-only-design.md) records the earlier gate; this subsequent approval satisfies it. This plan decomposes the approved design without changing its safety or compatibility commitments. It also scopes admission of the user-reported installed/authenticated `agy` and `grok` commands. Their product identity and advisory capabilities remain unverified.

Planning reference is documentation commit `53faf54` over PAL source `7afc7c1cc96e23992c8f105f960132c657883bb1`. No product implementation, CLI invocation, or review verdict is claimed here. The [baseline](clink-only-baseline.md) remains the comparison record: 866 passed, 4 skipped, 16 deselected after the install-only MCP compatibility constraint; pristine failures remain recorded.

## 1. Preserve the orchestration seams

Port actual invocation points while retaining their surrounding tool behavior:

| Existing seam | Change | Preserve |
| --- | --- | --- |
| `tools/simple/base.py::SimpleTool.execute` | Provider resolution, file budgeting, generation, response identity, and implicit retries become shared-backend operations | Request validation, `prepare_prompt`, formatting hooks, and public response envelopes |
| `tools/workflow/workflow_mixin.py::execute_workflow` | Import/export explicit state; remove provider prerequisites and provider-dependent file embedding | Step preparation, consolidation, required actions, continuation/completion predicates, and tool-specific response customization |
| `BaseWorkflowMixin._call_expert_analysis` | Await `backend.run(CLIRequest)` at the existing expert boundary | Tool prompt, expert context/instruction, accepted structured/plain-text result handling |
| `tools/consensus.py::execute_workflow` and `_consult_model` | Resolve CLI participants, isolated consultant payloads, bounded execution, explicit outcomes | Sequential one-consultation-per-step default, stance behavior, caller synthesis, and existing response shape |
| `tools/clink.py::CLinkTool.execute` | Thin bridge to the shared backend and transaction context | MCP entry point, file/image admission, role selection, response envelope |
| `server.py` dispatch/startup/continuation | Fresh tool per call, one state transaction, provider-free startup | Enabled-tool filtering, MCP transport, prompts, and structured errors |

Do not rebuild a generic workflow engine, collapse tools into roles, or emulate the old provider registry with a CLI-shaped fake provider. Remove provider assumptions where they occur. In particular, replacing only `generate_content` leaves early `ModelContext` creation, continuation reconstruction, file embedding, and schema discovery broken.

Keep the local `planner`, `docgen`, and `tracer` protocols intact, including planner revisions/branches and docgen counters. The eight expert workflows retain their individual completion predicates; only final eligible analysis calls the backend. Fix the explicit `thinkdeep` expert opt-out without rewriting guidance. `chat` keeps its required working-directory field and returns generated content without writing `pal_generated.code`.

## 2. Locked shared contracts

The backend worker owns the provider-free types in proposed `clink/contracts.py` and facade in proposed `clink/backend.py`. Consumers use these actual agreed names:

- `BackendTarget` contains the resolved client/role, requested/effective model, validated working directory, and warnings. Snapshot configuration deeply; a frozen wrapper around mutable lists/dictionaries is insufficient.
- `CLIRequest` contains `prompt`, `target`, `system_prompt`, tuple-valued files/images, `request_id`, admission time, absolute deadline, `tool_name`, and `require_search`. It contains no mutable workflow state or provider objects.
- `CLIResult` contains bounded content, status, request/backend identity, requested/actual model, duration, warnings, truncation, and safe public metadata. Raw streams and argv stay private to bounded parsing.
- `BackendError` contains stable `code`, safe message/remedy, and bounded metadata.

The three facade operations are `get_backend().resolve(...)`, `await get_backend().run(CLIRequest)`, and `get_backend().describe()`. The implementation uses `run` as the agreed invocation name; no competing `execute` API is required. Resolution accepts optional client, role, model, working-directory override, images, and legacy tuning values. It applies the approved precedence and warnings, without model calls or fallback.

The server establishes request ID and a monotonic absolute deadline at MCP admission, before locks or backend resolution. Every participant and queue waiter uses that deadline. Resolve/describe may report unavailable adapters; no live probing occurs during discovery. Mandatory no-write/MCP-isolation policy is validated before process launch.

Consumers supply deliberately composed prompt content. The backend enforces policy and may add the mandatory policy preamble and the single admitted file-content section described in section 8, but must not independently inject history or replace the authoritative tool instructions. Preserve distinct policy/tool/output/stance/role/data sections. Model strings are opaque native identifiers; remove server-side provider `model:option` parsing from this path.

## 3. One state transaction per tool call

The state worker owns the agreed API in `utils/call_state.py`:

`conversation_call(tool_name, validated_arguments, *, deadline=None, request_id=None)` is an async context manager. `get_current_call()` exposes its current call. Fields are `continuation_id`, `context`, `cached_result`, and `workflow_state`. Methods are `history()`, `set_workflow_state(dict)`, and `commit(result, *, success=True, user_content=None, assistant_content=None, files=None, images=None, backend_identity=None, workflow_state=None)`. `StateError` exposes code/message.

The integration sequence is fixed:

1. The server validates original arguments and retains them before any replay. It creates the deadline and fresh tool instance, then enters the transaction.
2. State admission acquires the continuation lock, reserves capacity, validates the tool/branch transition, and checks the canonical input digest. Exact terminal repeats may return cached results; changed input must follow a valid revision/branch transition or fail visibly.
3. The tool imports its namespace snapshot, computes existing guidance/consolidation, and requests canonical history explicitly when appropriate. It calls the backend only at its existing eligible invocation seam.
4. The tool stages its updated namespace and response. The server/coordinator commits history, namespace, terminal response, and attempt outcome atomically, then releases the lock/reservation.
5. Exception/cancellation finalization records failure metadata, releases capacity/locks, and never turns uncertain prior spending into a hidden repeat.

During migration, existing `create_thread`/`add_turn` helpers stage into the active transaction rather than persisting independently. Commit must publish each turn once, whether staged by a compatibility helper or passed explicitly. Remove the server's old history-mutation/enrichment path; do not use textual history markers as coordination. Direct in-process tool tests must enter the same transaction helper or a fixture that does so. Compatibility helpers are not a second commit authority.

Workflow snapshots remain namespaced by tool and existing branch identifiers. Export/import `work_history`, initial request, and tool-specific state through explicit hooks; reuse existing consolidation rebuild methods rather than reconstructing domain semantics in storage. Preserve other namespaces when switching tools. Store JSON-ready results and reconstruct the same MCP `TextContent` shape when returning cached responses.

Consensus has a strict exception to canonical replay: freeze original `step` before enrichment. Consultant prompts contain only authoritative instructions, frozen role/stance, original proposal, and declared context. They never call `history()` to build consultant input. The caller's synthesis history can still be recorded normally.

## 4. Parallel ownership

Only the named owner edits a shared path. Shared contract changes are announced to consumers before integration; the conductor resolves changes spanning owners. Workers do not commit, reformat, or revert another worker's files. Existing shared test fixtures such as `tests/conftest.py` belong to the conductor. New tests should use owner-specific files to avoid collisions.

| Owner | Exclusive implementation scope | Required handoff |
| --- | --- | --- |
| `/root/backend` | `clink/` including contracts, facade, registry, models, runner, adapters/parsers, agent/parser registries; `conf/cli_clients/`; CLI role prompt assets and backend tests | Importable contracts first, then verified fake-runner behavior and per-adapter capability/admission status |
| `/root/state_replay` | `utils/conversation_memory.py`, `utils/storage_backend.py`, proposed `utils/call_state.py`, state/replay tests | Published transaction API, staged compatibility behavior, namespace/capacity/digest rules, failure-finalization evidence |
| `/root/tool_bridges` | Shared/simple/workflow bases and schema builders; `tools/shared/base_models.py`, `tools/models.py`; chat/clink and the eight expert-tool schema/bridge changes; matching tests | Existing workflow hooks preserved, backend request construction, state import/export, no double history writes |
| Dedicated consensus worker | `tools/consensus.py` and consensus-only tests | Sequential parity first, then optional parallel mode, frozen identity/context, invalidation, blinding tests |
| `/root` conductor | `server.py`, `config.py`, metadata/lookup tools, environment/package/setup/deployment files, provider-removal integration, shared fixtures, documentation, Git operations | End-to-end integration, native independent review, provider-free acceptance, final results |

The local planner/docgen/tracer leaf files are preserved by default. If a necessary compatibility repair touches one, the conductor assigns that exact path before editing. The consensus worker consumes shared schema/state/backend APIs without editing their owner files. Adapter-specific optional work must not concurrently edit registry/factory files owned by the backend worker; hand over focused patches or sequence registration after the implementation lands.

## 5. Integration and commit dependencies

These are bounded delivery slices, not permission gates. The user has approved implementation; the conductor proceeds through routine fixes and genuine configured reviews.

| Slice | Work and owner | Dependencies / acceptance before integration |
| --- | --- | --- |
| 0 | Conductor commits MCP `<2` constraint separately | Preserve pristine/constrained baseline evidence; import/collection validation |
| 1 | Backend publishes types/facade; state publishes transaction API; bridge owner adds necessary typed hooks | Freeze contracts before consumers spread assumptions. Test immutable snapshots, errors, transactions, and JSON response round trips |
| 2A | Backend implements config, bounded runner, existing adapters and fail-closed admission | Slice 1; fake executables test deadlines, concurrency/queue bounds, cancellation/descendants, output ceilings, parser failures, env/argument policy |
| 2B | State implements bounded replay/store, namespaces, reservations, transition identity | Slice 1; no dependency on real CLIs. Test exact repeats, changed inputs, revisions/branches, TTL/capacity, failures and history deduplication |
| 2C | Bridge owner prepares state import/export and backend construction using fakes | Slice 1; preserve old step guidance with characterization tests. Keep preparation separate from behavior-changing commits |
| 3 | Bridge owner ports clink/chat, then shared expert hook and small workflow groups | Integrate 2A/2B/2C. Test all intermediate/final/opt-out paths, cwd/roots/images, return envelopes and content-only generation |
| 4A | Consensus owner ports sequential protocol | Slices 2/3 shared seams; prove original proposal before replay and one consultation per accepted step |
| 4B | Consensus owner adds explicit parallel execution and manifest invalidation | 4A; order, partial failure, duplicate requests, shared deadlines, visible file changes, and no automatic retry/synthesis |
| 4C | Conductor ports listmodels/version/apilookup and server cutover | 2/3 available; fresh tools, one transaction, lazy backend resolution, zero-key startup, all 18 tool schemas |
| 5 | Conductor removes provider-only imports/code/catalogs/dependencies and updates setup/docs | All 18 tools pass replacement/local-path checks. No remaining provider routing or fallback imports; package/import tests pass |
| 6 | Conductor runs opt-in real-CLI/policy smoke suite and final independent review | Core safe paths integrated; every tool has an outcome and supported CLI identity, or an explicit verification gap |

Slices 2A, 2B, and 2C may proceed concurrently after contracts are published. Consensus work and conductor metadata preparation may use fake dependencies while waiting for integration. Provider deletion is deliberately late. Do not publish a release from an intermediate branch that mixes old unsafe CLI defaults and new routing.

Tests should assert behavioral contracts rather than reproduce implementation. Retain the useful upstream tool tests while replacing provider-specific fixtures with the agreed fake backend. Record which provider-only tests are retired and which behavioral tests replace them; raw test totals alone cannot establish parity. Run focused checks per slice, then the full nonintegration command `python -m pytest tests/ -v -m "not integration"` at integration/commit boundaries, plus configured check-only lint/format and packaging checks. Repeat broad checks when subsequent changes or failures justify them.

## 6. Bounded agy/grok admission extension

The user reports both commands installed and authenticated. Record this as user-reported status; do not infer binary identity, vendor, model names, endpoint, or permissions from their basenames. This extends adapter discovery under the approved architecture, not blanket execution support and not a new hosted-provider layer.

The conductor assigns a Sol execution owner a bounded discovery packet for each command: resolve the executable and symlink target, identify package/vendor/version, inspect version/help and only directly relevant subcommand help, and consult authoritative documentation for the identified product. Do not read full shell profiles, credential stores, or session logs. Never install/update, log in again, copy credentials, or run a model merely to discover identity. A clear missing capability is a terminal admission finding, not a reason for repeated flag guessing.

Each adapter must establish all of these before delegation is enabled:

- Noninteractive input/output with an explicit parser and reliable nonzero/error semantics; bounded capture and process-group cleanup through the existing runner.
- Enforced advisory operation: a verified read-only sandbox or explicit tool allowlist/chat-only mode, without write tools, mutable shell access, automatic escalation, hooks/plugins, subagents, or inherited MCP. A prompt promising not to edit is insufficient.
- A documented native authentication path using the filtered environment, truthful read-scope disclosure, model-selection semantics, and version-specific policy evidence.
- Deterministic argv/environment construction that cannot be weakened by role/client extras, and passing fake-output/error/cancellation/recursion tests.
- An opt-in real policy/smoke probe in a disposable workspace, retaining only bounded technical evidence. User-reported authentication is not a passing probe receipt.

No generic fallback agent is admitted for unknown adapter names. If a command is a wrapper, evaluate the wrapper and underlying runtime as one trusted executable surface. Reuse an existing adapter only when its actual argument/output/policy contract is verified compatible, not because its model or branding resembles another CLI.

Initially declare image and web-search capabilities unsupported. Enable each only after native input/evidence contracts are verified; otherwise image/search requests fail before launch. A text-only advisory adapter may be usable for chat/workflow consultation while remaining ineligible for lookup/images. A CLI lacking mandatory controls remains unavailable with a precise reason, while Codex/Claude and local-tool work continue. Do not weaken the design to make a newly installed command pass.

Keep adapter additions in separate commits after core contract stability. The backend owner integrates proposed adapter/parser files, explicit registration, safe manifests, and capability fixtures. The conductor updates discovery/migration docs. Gemini remains subject to the same fail-closed gate. No per-participant retry API or automatic cross-CLI fallback is added.

## 7. Completion and risk checks

Required final evidence covers all 18 MCP schemas; provider-free startup/local operations; preserved intermediate/final workflow decisions; zero-launch expert opt-outs; chat cwd/root enforcement; per-tool state through tool switches; planner revisions/branches; exact-repeat versus changed-input behavior; consensus sequential continuation blinding; parallel partial failure and file invalidation; cancellation without leaked processes/permits; and safe parser/error/log behavior.

Keep the approved limits: two running/eight queued processes, 30-second queue wait, 1,800-second total deadline, five-second termination grace, 10 MiB per stream, 20,000 output characters, 128 KiB replay within a 1 MiB inline prompt, 256 files/64 MiB manifest, and 256 conversations/64 MiB serialized state with legacy retention defaults. One runner and one transaction owner enforce these; leaf tools must not introduce independent pools or stores.

The largest integration risks are double history persistence, stale responses for revised steps, consensus contamination by replay, and provider prerequisites left in schemas/startup. The single-writer ownership and contract tests above target them directly. Adapter availability can remain incomplete without claiming unsupported tools passed. No hidden retry spends usage after uncertain completion.

Rollback uses focused revert commits, the pinned source and baseline environment, and operator-controlled restart with disclosed loss of ephemeral conversations. Never restore dangerous old CLI arguments or provider fallback automatically. No upstream push, production deployment, credential export, or manufactured review verdict is authorized by this implementation plan.

## 8. Release amendment: Codex inline-only context

The execution owner reports verification of Codex 0.166.0 configuration controls for write denial, hooks, MCP, and agent tools. Shell access still permits invoking other CLIs, so the release policy also disables `shell_tool`, `unified_exec`, `code_mode`, `deferred_executor`, browser, and computer capabilities. Record this as `read_scope="inline-only"`; native project-file reading is unavailable. This is a bounded implementation amendment under standing development authorization, preserving the approved advisory/no-delegation objective without another approval gate. Execution and independent review own the final verification evidence.

This restriction specializes the design's file-references-where-supported rule. Keep required chat cwd/root validation, but do not imply that a valid cwd grants native file access. The common backend admits canonical regular-file paths under operator roots and includes each attachment once as delimited text. Tool bridges must stop provider-style file embedding, so the backend is the sole automatic attachment reader/composer. Deduplicate canonical attachment paths, including symlink aliases; do not rewrite caller-pasted text merely because it resembles an attachment.

The final combined UTF-8 input, including policy, tool/role instructions, request, replay, file text, paths, and delimiters, must fit the existing 1 MiB ceiling. The 128 KiB history limit and manifest path/byte limits also remain. Oversized required input fails before launch; do not silently omit files or truncate the task. Directories return `unsupported_capability` without recursive expansion. Unsupported non-text attachments fail explicitly. Native images remain separately capability-gated and are never smuggled through text embedding.

Preserve supported `prompt.txt` input indirection, but read it only after canonical path/root/type admission. Its contents are required task input rather than optional history, and count toward the combined ceiling. Do not append the same canonical file again as an attachment. Consensus continues to freeze its original admitted proposal before replay and to bypass canonical history for consultants; existing file-change detection still applies. Do not replace it with a source-snapshot subsystem.

Acceptance tests must establish:

- Disabled execution/delegation features cannot be re-enabled by client/role arguments, repository instructions, or fallback behavior; attempts to invoke another CLI do not execute it.
- Regular files are admitted and included exactly once across simple tools, expert workflows, and consensus; tool bridges do not duplicate backend embedding.
- Out-of-root and symlink-escape paths fail before reads; directories fail without enumeration; admitted `prompt.txt` becomes required input and is not duplicated.
- Combined-input boundary tests include multibyte UTF-8 and all instruction/replay/file overhead; oversize failures launch no process and lose no required content silently.
- Image/search requests obey their separate verified capabilities. Neither capability failure nor missing file access restores shell, browser, computer, or another CLI.
- Discovery reports inline-only read scope accurately, while local planner/docgen/tracer behavior and consensus blinding/invalidation remain intact.

`agy`, `grok`, and Claude remain unavailable while their mandatory policy contracts are uncertain; installation/authentication does not bypass admission. Gemini retains its existing gate. Complete independent work and report unavailable capabilities explicitly rather than broaden authority.

## 9. Approved bounded refinement: pinned direct-mode catalog

The conductor accepts this refinement under the user's standing development authorization. The backend owner implements it; no additional permission gate is required. Live compatibility and policy proof remain required and are not claimed complete by this amendment.

Pinned Codex 0.166.0 source (`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`) establishes that model `tool_mode` overrides feature-selected mode. A catalog entry requesting code mode can therefore require the disabled host despite the feature flags. Supported direct mode skips code-mode executor registration without enabling that host. Keep strict handling of native error records; neither ignoring the error nor enabling the host is an acceptable repair.

Use the supported, startup-only `model_catalog_json` setting with absolute resource path `clink/resources/codex-0.166.0-models-direct.json` resolved from the installation. Obtain the complete genuine `ModelsResponse` through `codex debug models`, without inference. Preserve every native descriptor, identifier, field, and array order. Change only literal `tool_mode="direct"` on explicitly admitted model records; do not synthesize descriptors or translate provider aliases.

Admission binds the native-source and projected-resource SHA-256 values, executable version/hash, complete policy recipe, exact admitted IDs, and captured default ID. Validate nonempty schema and exact projection differences before use. Check the literal enum explicitly because the native decoder can treat an invalid string as absent. Recheck binary, recipe, and resource hashes before launch. No request-time catalog synthesis, automatic refresh, retry, or fallback is introduced.

Enforce exact admitted-ID matching before invoking Codex; native metadata lookup also accepts prefixes and namespace variants that this admission does not cover. Always pass an explicit model selected from request, role, client default, or the genuine captured native default for the verified isolated configuration/authentication profile. This prevents ambient `cfg.model` from bypassing admission. Report `default_origin=pinned_native_catalog` for that last case and keep requested/effective/reported model identities distinct.

Compatibility decision: catalog defaults, visibility, and capabilities are frozen at the captured snapshot. Future remote catalog updates do not automatically apply, and new model IDs or aliases require deliberate admission. This changes client-side tool dispatch, not model identity. Preserve all existing host, shell, unified/deferred/code execution, browser/computer, MCP, agent, and hook denials, together with inline-only context and separate image/search gates.

Acceptance requires structural projection tests, unchanged non-tool-mode metadata/order, rejection of invalid/empty/tampered resources and unadmitted/prefix IDs, and exact explicit/default selection despite ambient model configuration. Genuine direct-mode text execution must complete without host-required or other native error records. Policy probes must still deny shell/other-CLI, agent, hook, and MCP execution, and existing parser, cancellation, output-limit, inline-file, and image regressions must pass. Schema/source evidence establishes feasibility only; keep execution unavailable until the required live admission evidence is recorded.

Planning receipt: dedicated planning role; requested Astra/ultra; effective runtime metadata unavailable/unverified. Implementation remains with configured Sol execution agents and genuine configured Sol/high review. This plan is ready for conductor application under the user's explicit Phase 3 approval.
