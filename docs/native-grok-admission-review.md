# Grok native admission review

**Status: independent proof review passed; Grok native admission integrated under existing standing development authorization.** This document explains the composite [admission](verification/native-grok-1.0.70-admission.json). It does not authorize more verification submissions. All original receipts remain unchanged, including their `native_verified=false` values and failed checks.

The proposed scope is the exact recorded **Grok 1.0.70 (`f50904eaee6c`) executable on macOS arm64**, with executable SHA-256 `5c8a688251bf61e35370b828b6a353b0a20fcc3f64000de8a91d426039f29cec` and native recipe SHA-256 `b2e236f6490d3d00b51dac2a634c8b4f845b649ad9386c6510c6ae902f730cd3`. The observed model on accepted completed and permission checks was `grok-4.7-build`, reported by native terminal metadata. No explicit model was passed. Cancellation has no terminal model evidence; its model remains unknown. Architecture was rechecked from the verification host; the original receipt schema does not separately capture architecture.

## Accepted evidence and retained failures

The composite combines ordinals **3, 4 and 5** from [attempt4](verification/native-grok-1.0.70-attempt4.json) with ordinal **9** from [attempt7](verification/native-grok-1.0.70-attempt7.json). These ran through the actual registry, backend, runtime, shared process pool and task manager. The candidate override was confined to the verification harness. All receipt and preflight hashes, fixture bindings, allowance changes and exact ledger prefixes are pinned in the admission JSON.

| Ordinal | Check | Recorded result and admission use |
| --- | --- | --- |
| 1 | Edit | `permission_needed`; edit/tests, hooks and MCP observed, but specialist execution not established. Failed gate retained. |
| 2 | Edit | `completed`; specialist lifecycle observed, but exact canary proof missing from the observer. Failed gate retained. |
| **3** | **Edit** | **Accepted:** actual failing test, native edit, native passing test, independent passing test, hook, specialist lifecycle and exact canary, MCP inherited-environment canary. |
| **4** | **Follow-up** | **Accepted:** explicit resume preserved the same private session and context token. |
| **5** | **Permission** | **Accepted:** `permission_needed`, protected file untouched, no retry. Exit status zero did not override the denial. |
| 6 | Cancel | `cancelled`; mutation, process-group absence and pool/lease release observed. Exact descendant identity and absence were not established. |
| 7 | Cancel | `cancelled`; readiness added, but the interpreter identity observer still could not establish exact child identity. |
| 8 | Cancel | **`uncertain/native_failed`; cause UNKNOWN.** Child identity/absence and pool/lease release observed; no recorded reason or process-group absence. |
| **9** | **Cancel** | **Accepted:** mutation and readiness observed, exact synthetic child identified then absent, CLI process group absent, pool and project leases released, no emergency cleanup requested. |

There were **nine top-level native invocations**, nine consumed allowance slots and zero automatic retries. Native turns, tool calls, subagents and internal model requests were not separately counted and may exceed nine. Preparing this composite made no new model or native CLI calls.

The seven original receipt/preflight pairs are [prepare1](verification/native-grok-1.0.70-attempt1-prepare.json) / [preflight](verification/native-grok-1.0.70-attempt1-prepare.preflight.json), [attempt2](verification/native-grok-1.0.70-attempt2.json) / [preflight](verification/native-grok-1.0.70-attempt2.preflight.json), [attempt3](verification/native-grok-1.0.70-attempt3.json) / [preflight](verification/native-grok-1.0.70-attempt3.preflight.json), [attempt4](verification/native-grok-1.0.70-attempt4.json) / [preflight](verification/native-grok-1.0.70-attempt4.preflight.json), [attempt5](verification/native-grok-1.0.70-attempt5.json) / [preflight](verification/native-grok-1.0.70-attempt5.preflight.json), [attempt6](verification/native-grok-1.0.70-attempt6.json) / [preflight](verification/native-grok-1.0.70-attempt6.preflight.json), and [attempt7](verification/native-grok-1.0.70-attempt7.json) / [preflight](verification/native-grok-1.0.70-attempt7.preflight.json).

## Source and fixture compatibility

The reviewed candidate core and Grok parser match commit `9bdd273d7ac399b3a1fd635066308369f10f76b5`; the admission pins each file hash. Production integration adds the Grok policy/receipt, exact Darwin/arm64 admission gate, enabled bundled profile and required package resources; it does not change the launch recipe, parser, runner, task manager or Codex advisory policy. The production runner and task manager also remain byte-identical to preparation commit `45e4e73`. The conductor reports no gate-relevant core changes during ordinals 3/4/5/9. Original receipts themselves record binary, version, recipe and fixture hashes, **not historical per-file runtime hashes or a Git commit**. Current source binding is therefore distinct from contemporaneous evidence. Verifier/helper files have a separate snapshot captured when Grok admission was assembled and are not claimed to belong to that core commit. The JSON field `current_verifier_snapshot` retains that historical capture; later Antigravity-only fixture changes do not update or repin this immutable Grok admission.

Every original preflight has the same executable, version and recipe identity. All seven retained synthetic fixture aggregates and canonical project-path hashes were rechecked against their respective receipts. Attempt2 reuses the preparation fixture. Other attempts use distinct disposable paths, so project hashes and path-embedded configuration hashes differ.

The per-file attempt4/attempt7 comparison is included in the JSON. The fixture marker, instructions, native role, hook scope, specialist definition and specialist canary are byte-identical. Grok config and hook JSON are identical after replacing only their canonical fixture-root literals. The substantive changes were confined to these verification concerns:

- **Specialist observation before ordinal3:** recognize native `target_file` and typed `ReadFile.FileContent` output for the exact specialist child and canary path. The canary assertion stayed strict; ordinal2 remains failed.
- **Readiness before ordinal7:** add a marker from the long-lived synthetic child before it sleeps. Arithmetic test assertions remain unchanged.
- **Interpreter identity before ordinal8:** recognize the current process's macOS kernel interpreter reference alongside `sys.executable`. Proof still requires exact `fixture.py child` arguments and the observed process fingerprint. The independent [identity diagnostic](verification/native-process-identity-check.json) matched and reaped its owned child without model calls.
- **Safe MCP marker IO before ordinal8:** retain benign stdio initialization/tool/synthetic sentinel behavior while using nofollow, nonblocking opens and regular-file/owner/single-link validation before truncating or reading markers. Bounded protocol and strict boolean parsing reject unsafe inputs. Offline tests cover symlinks, hardlinks, FIFOs and invalid ownership. **Ordinal9 was cancellation-only; it did not repeat live MCP execution.** Ordinal3 supplies live MCP evidence; current benign-protocol compatibility is checked offline.
- **Diagnostics before ordinal9:** retain only recognized exception class, trusted relative source location and bounded OS error number. Strict lineage validation preserves previous ledger entries. Production runner/task-manager cleanup behavior was not changed.

## Transport, permissions and effects

Grok receives `--prompt-file <private file> --output-format json`, empty stdin, optional `--model`, and validated exact UUID `--resume`. The prompt is bounded to 1 MiB, stored as a 0600 file inside a 0700 directory, checked again before launch and removed after shared-pool cleanup on ordinary outcomes or cancellation. It is absent from argv and persisted evidence. Abrupt host/server termination can prevent context cleanup; private permissions protect possible leftovers.

Normal native HOME, configuration, authentication, hooks, specialist and MCP discovery remain authoritative. The Grok-specific environment baseline preserves `GROK_HOME`; further inherited names remain operator-controlled. No permission bypass, global allow-all, configuration suppression or copied authentication was used. Fixture permissions included the native generic task route to allow its configured specialist; that rule is not a named-agent security boundary or OS sandbox.

Recorded runs temporarily granted **only the exact synthetic fixture trust entry**, then removed it. The receipts report restored host configuration and intact fixture configuration after shutdown. Preparing this composite read only repository evidence and synthetic fixtures; it did not inspect host trust/auth stores. Native CLI-owned authentication, cache and session activity remains outside exported evidence.

## Verification and remaining risk

The current focused parser/adapter/runtime/MCP suite passed **111 tests**. The isolated [candidate4 result](verification/native-adapters-candidate4/result.json) and [JUnit report](verification/native-adapters-candidate4/junit.xml) record **1,746 tests plus 16 subtests**, with zero failures, errors or skips, network blocked and unrelated home reads denied. Their hashes are pinned in the admission.

The [cancellation diagnostic](verification/native-cancellation-diagnostic.json) records 30 successful offline real-pool synthetic cancellations across three leader behaviors and single/repeated cancellation. A separate post-cleanup EIO injection proved safe diagnostic capture. **Neither experiment reproduced or diagnosed ordinal8.** Ordinal9 demonstrates a later successful cancellation; it does not establish that an unknown production defect was fixed.

Cancellation preserves partial effects and does not roll back mutations, prove remote MCP cleanup or cover detached descendants. First-attempt cancellation may leave no resumable session. The checks cover synthetic configured capabilities, not every installed integration, model or platform. Image transport and interactive approval relay remain unverified. Raw native streams were not retained, so parser compatibility uses normalized recorded outcomes and offline cases rather than replay. A completed task establishes execution outcome; fulfillment requires independent assertions.

Independent proof review returned GO through `native_execution_review`, with Sol 6.1/high explicitly selected; effective runtime model evidence is unverified. The conductor integrated the reviewed production admission under existing user authorization. Claude native admission and Codex advisory admission are unchanged; Agy and Codex native profiles remain disabled.
