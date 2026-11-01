# CLI-only implementation verification

This record covers the implementation explicitly approved after the audit/design gate. The original approval boundary in `clink-only-design.md` is historical. Routine verification repairs, scoped review exports and bounded architecture refinements proceeded under the user's standing development authorization.

## Provenance and delivery

The fork is `acebytes/clink-mcp-server`, derived from PAL commit `7afc7c1cc96e23992c8f105f960132c657883bb1`. Apache 2.0 licensing and Beehive Innovations/Fahad Gilani attribution remain. The development branch is `dex/clink-only-design`; the upstream remote has push URL `DISABLED`, and the writable origin is the personal fork.

The original `/Users/d/Tools/pal-mcp-server` was not used as a writable development checkout. Its six preexisting modifications were excluded. Its recorded diff SHA-256 is `87161dde0f327e82cd9245680323e895544f9618aadacf5ce05a76e1ac53dc0b`. No original `.env`, credentials or virtual environment were copied.

Commit `115637f` contains only the separately approved MCP compatibility cap. Subsequent commits are coherent integration slices: shared CLI execution, transactional state, tool/server cutover, provider removal/test migration, packaging, and documentation. Verification describes the integrated working tree; intermediate slice commits are not represented as independently green installations.

| Implementation slice | Commit |
| --- | --- |
| MCP compatibility cap | `115637f` |
| Shared advisory CLI execution | `7cb236a` |
| Transactional continuation state | `7caab44` |
| Tool/server cutover | `5df94e4` |
| Provider removal and test migration | `759a605` |
| CLI-only packaging | `8bc411a` |

The final documentation commit contains this report and the complete verification records. Before delivery, the original PAL diff was rehashed and still matched the value above; its six preexisting modified files remained the only reported changes. The personal origin and disabled upstream push URL were rechecked.

## Implemented behavior

- All 18 MCP tools are enabled by default. Discovery and local utilities start without provider SDKs, API keys or installed CLIs. `DISABLED_TOOLS` remains supported; version/listmodels remain essential.
- Chat/clink and eligible final expert calls use one bounded, advisory CLI backend. Eight expert workflows retain caller-driven steps and honor `use_assistant_model=false`. Planner/docgen/tracer remain local.
- Native model identifiers and requested/actual identity remain separate. CLI/role defaults, atomic client replacements, configuration precedence, unsupported-control warnings and capability failures are explicit.
- UUID continuation updates are serialized and bounded. Workflow state is per call, successful-message capacity is reserved before launch, and exact accepted step repeats reuse recorded outcomes. Replay does not use native session resume.
- Consensus supports sequential and explicit parallel consultation, blinded inputs, ordered individual outcomes, caller-owned synthesis, and bounded file-change checks before/after consultation and after queue wait. Invalidated verdicts and downstream summaries cannot be reused as valid context.
- Declared regular files are read through anchored descriptors and supplied inline once. Required context is never silently truncated. Private bounded image copies prevent later source-path swaps. Replay pruning is visible.
- Process admission, deadlines, queue limits, output limits, cancellation and descendant cleanup are shared. There are no automatic retries or CLI fallbacks. Routine diagnostics exclude prompt text, raw stderr, commands and credentials.

Current interfaces and limits are documented in [CLI configuration](cli-configuration.md), [migration](migration.md) and the server's tool schemas. Historical upstream guides carry explicit notices.

## Test evidence

The pristine dependency run failed collection with MCP 2.3.0: 830 collected, 16 deselected, seven collection errors and no executed tests. The installation-only `<2` run with corrected isolation passed **866 tests**, skipped four real-provider cases and deselected 16, with 19 passing subtests. Both failures and the corrected baseline remain in [baseline evidence](clink-only-baseline.md).

| Integrated checkpoint | Result | Evidence |
| --- | --- | --- |
| Compatibility cap | 866 passed, 4 skipped, 16 deselected; 19 subtests | [Compatibility](verification/compatibility/) |
| Replacement backend/tool/state checkpoint | 465 passed | [Bridge checkpoint](verification/bridge-checkpoint/result.json) |
| Generic migrated behavior | 337 passed; 16 subtests | [Migration checkpoint](verification/migration-generic-2/result.json) |
| Initial full collection | One stale legacy import prevented collection | [Preserved failure](verification/implementation-full-1/result.json) |
| Second full suite | 919 passed, 1 failed; 16 subtests. The policy-probe harness was temporarily testing a different recipe from production. | [Preserved run](verification/implementation-full-2/result.json) |
| Privacy/state integration | 940 passed, 4 failed; 16 subtests. Legacy fixtures lacked the new approved-root contract and safe error expectation. | [Preserved run](verification/implementation-full-3/result.json) |
| Linux offline checkpoint | 946 passed; 16 subtests, with read-only source/root, no network, credentials or native CLIs | [Linux evidence](verification/linux-offline-checkpoint2/README.md) |
| Catalog integration | 932 passed, 20 failed; 16 subtests. Generic fake transports inherited production's newly pinned model restrictions. Fixtures were subsequently isolated without changing production policy. | [Preserved run](verification/implementation-full-4/result.json) |
| Final macOS offline suite | **1008 passed; 16 subtests**, zero failures, skips or deselections, Python 3.12.15 arm64 | [Final macOS evidence](verification/implementation-final/result.json) |
| Final Linux offline suite | **1008 passed; 16 subtests**, zero failures, skips or deselections, Python 3.12.16 arm64 | [Final Linux evidence](verification/linux-packaging-final-checkpoint1/README.md) |
| Frozen package/container verification | Wheel/sdist, fresh minimal installation, actual MCP stdio, read-only non-root Docker, resource relocation/hash and provider SDK absence passed | [Final package evidence](verification/packaging-final-checkpoint1/README.md) |

Final checks used the frozen production tree; all package inputs remained unchanged during verification. Ruff, Black and isort checks also passed. The full integrated suite was rerun before each implementation-slice commit, with separate immutable records under `verification/commit-*/`. Earlier failures are not overwritten. The [migration ledger](clink-test-migration.md) accounts for all 870 baseline-selected items: 357 hosted-provider contract cases retired, with the remaining 513 retained or explicitly replaced. The four former real-provider skips belong to the retired group. Packaging separately replaced 119 obsolete cases with 33 focused checks, then added native resource checks. All 16 baseline-deselected node IDs are reconciled separately in the ledger. The provider-only simulator scenarios were outside that unit baseline; an offline MCP/state/consensus harness replaces them.

Offline evidence uses a fresh Python 3.12.15 environment, MCP 1.30.0, a clean environment allowlist, and macOS Seatbelt network/unrelated-home-read denial with enforcement probes. Dependency installation was a separate networked preparation step. Ordinary tests do not invoke authenticated CLIs. Reproduce with a new immutable label:

```sh
.venv/bin/python scripts/test_offline.py new-verification-label
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/isort --check-only .
```

## Independent review

Independent read-only reviews found and drove repairs for process-shutdown ownership, queue permit accounting, anchored file/image reads, queued consensus file changes, authoritative stance handling, attachment forwarding, expert current-step preservation, partial-result replay, invalidated downstream summaries, workflow-state restoration, log redaction, and complete error-response bounds. These are verification repairs within the approved scope.

The completion reviewer reproduced a 259,020-byte validation failure from a 40,048-byte request. After repair, the same failure is 383 bytes; an oversized caught error becomes a 206-byte `output_limit` response. The malformed-list sentinel no longer appears in logs. Dedicated regressions preserve both fixes.

Delegation selection was explicit: execution/audits used `gpt-6.1-sol` with `ultra`; planning and bounded architecture refinement used the dedicated planning agent with `gpt-6-astra`/`ultra`; independent reviews used `gpt-6.1-sol`/`high`. Effective agent runtime model/effort metadata was unavailable and is **unverified**. Native CLI requested-model records are separate and do not establish agent-runtime identity or the provider's actual model.

## Native integration and limits

The user explicitly authorized opt-in real-CLI testing and reported installed/authenticated `agy` and `grok`. Their observed identities and missing protection gates are recorded in [CLI admission](clink-cli-admission.md). They remain unavailable; installation/authentication does not bypass policy admission. Claude and Gemini are also unavailable until their required protection checks pass.

Codex policy verification and every prior failed attempt are retained in [the native-policy record](verification/cli-policy/README.md). The [first actual-tool attempt](verification/live-tools-1/README.md) exposed native configuration errors preceding terminal events; the strict parser rejected them, and the loop was stopped. The reviewed correction changes only `tool_mode` in a genuine bundled model catalog. Its 12 exact model IDs and native `gpt-6.1-sol` default are frozen and content-bound together with the binary and launch recipe. Unknown identifiers, native prefix/namespace aliases and changed artifacts fail before inference; nothing silently translates or falls back.

The [second actual-tool matrix](verification/live-tools-2/README.md) passed all 17 selected tools: chat/clink, eight actual final expert calls, two parallel consensus participants, and six local tools. Every source-file comparison remained unchanged. The [separate actual lookup](verification/native-search-1/README.md) passed through the real handler/backend/runner/parser and returned `search_performed=true` plus 20 structured source URLs. Together these cover all **18 tool outcomes**. The search probe temporarily enabled only the search capability bit inside its own verification process; mandatory controls and resource pins were unchanged. Independent read-only review accepted the [v5 admission](verification/cli-policy/codex-0.166.0-admission-v5-search.json), and its exact pin now enables production text, images and native search. Later URL and error-flag parser hardening was verified offline: the successful live lookup predates that hardening, and native raw output was neither retained nor replayed.

Only `gpt-6.1-sol` was exercised live. Actual provider-reported identity remains unknown. Cross-tool/cross-CLI state behavior is covered deterministically; real cross-CLI behavior remains unavailable while other families are blocked. Native images passed a strict-parser synthetic-image probe. The built-in model catalog and default are deliberately frozen; later model/catalog versions require renewed admission. Python 3.12 on macOS arm64 and Linux arm64 are exercised; Windows native delegation and other native executable builds are not admitted.

Risk & Mitigation: native authority, state races, stale comparative findings and resource exhaustion are the main risks. Version/content-bound policy admission, native write restrictions, state transactions, explicit invalidation, anchored reads and bounded process/response handling address these. Native operational auth/cache writes are distinct from project edits. OS read authority may extend beyond approved project roots; inline context and disabled native shell/file tools constrain what this adapter supplies to the model. Local process cancellation cannot undo remote work already submitted or subscription usage already consumed.
