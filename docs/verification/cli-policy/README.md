# CLI admission verification

These records distinguish installed flags, no-inference runtime configuration, an authenticated protected turn, direct OS write denial, and active-process cancellation. They do not treat help text or an unchanged fixture alone as proof of enforcement. Raw account/configuration responses, credentials, prompts, model responses, session identifiers, and diagnostics are not retained.

The opt-in verifier is `scripts/verify_cli_policy.py`. The normal command sends only control requests to a fresh process using a synthetic profile. Production authentication locations remain unchanged in the policy recipe; the synthetic control probe deliberately does not test those credentials. `--live` can submit one Codex prompt only after exact version, disabled execution/delegation controls, unchanged hostile fixtures, process cleanup, and a direct native read-only sandbox write-denial test pass. The CLI may perform several inference calls or native retries while answering one prompt; the verifier cannot count those provider requests.

## Evidence and preserved failures

| Record | Observed result |
| --- | --- |
| `codex-0.166.0-attempt1.json` | Synthetic app-server effective config confirmed the initial control recipe; no model prompt. Live request stayed blocked. This historical record predates verifier hashes/timestamps and the additional shell/bridge restrictions. |
| `claude-2.1.304-attempt1.json` | Synthetic control initialization reported version 2.1.304 and no configured MCP servers. No model prompt. Agent definitions were present; definitions do not prove callable agent-tool exposure. |
| `codex-0.166.0-attempt2.json` | Protected exec exited 1 before JSON turn events under the outer tool sandbox. Raw diagnostics were discarded. Fixture unchanged; auth unknown. |
| `codex-0.166.0-attempt3-diagnostic.json` | A bounded diagnostic correction classified the same pre-turn startup failure as permission denied. Prior evidence was preserved. |
| `codex-0.166.0-attempt4-host.json` | Same restrictive controls through the host execution route: authenticated turn completed, exit 0, fixture unchanged, no shell/MCP/agent marker, process group reaped. No tool-attempt event was emitted, so this does not prove a particular `apply_patch` call was denied. |
| `codex-0.166.0-attempt5-cancellation.json` | Separate active turn reached `turn.started`, then was terminated with SIGTERM (exit -15); group reaped and content fixture unchanged. Native read-only sandbox also denied a real write. Its broad fixture hash included native synthetic-profile operational files and therefore changed; that fixture-layout defect was corrected next. |
| `codex-0.166.0-attempt6-sandbox.json` | No inference. Native `:read-only` profile allowed the sentinel read and denied an actual attempted write with EPERM/EACCES; forbidden file absent and corrected content-only fixture hash unchanged. |
| `codex-0.166.0-attempt7-search.json` | New exact base recipe passed all controls and direct native sandbox checks. Authenticated tiny public search request completed with unchanged fixture, but emitted no native search event and no official URL marker. Search capability remains false. |
| `codex-0.166.0-attempt8-image.json` | Same exact base recipe accepted only the explicit synthetic `tests/triangle.png`; completed response independently matched its black triangle. Authenticated, exit 0, unchanged fixture. |
| `codex-0.166.0-attempt9-v2-preflight.json` | No model prompt. Candidate v2 added explicit `web_search="live"`; effective config confirmed that mode and every retained restrictive control. |
| `codex-0.166.0-attempt10-v2-search.json` | v2 passed native sandbox checks and completed an authenticated public query with unchanged content. No native search item or official-source evidence appeared. |
| `codex-0.166.0-attempt11-v3-search.json` | A separately reviewed candidate also enabled the source-confirmed `standalone_web_search` feature. Both search gates and every restriction were verified before the one public query. No native search item or structured result URL appeared; v3 was not promoted. |
| `codex-0.166.0-attempt12-direct-preflight.json` | No inference. Verified the direct-catalog resource path/SHA, read-only/never, suppressed unstable-feature warning and every forbidden-tool/host control. |
| `codex-0.166.0-attempt13-direct-text.json` | Exact direct-catalog recipe: authenticated text turn, exit 0, zero native error items, unchanged content, and a pass from the exact production parser. |
| `codex-0.166.0-attempt14-direct-search.json` | Native search and open-page items finally appeared, with structured result fields. The strict parser rejected the stream and no returned-source URL was exported; the incomplete diagnostic did not establish the cause. |
| `codex-0.166.0-attempt15-direct-image.json` | Exact direct-catalog recipe: correct black-triangle image recognition, exit 0, zero native error items, unchanged content, and a pass from the production parser. |
| `../native-search-1/result.json` | One real backend lookup under the same mandatory controls succeeded, reported `search_performed=true`, and returned twenty structured source URLs. Native-call parser hashes are retained separately from final offline hardening hashes. No raw native output was retained or replayed. |

`codex-0.166.0-admission-v1.json` and `codex-0.166.0-admission-v2.json` are historical acceptance records invalidated for backend usability by `codex-0.166.0-admission-history-invalidated.json`. The initial harness counted a completed native turn without applying the production parser. Two native startup warnings were serialized as error items, which the strict parser correctly rejected. Those receipts retain their scoped authentication, content-hash and OS-denial facts, but do not establish a usable backend response. The replacement verifier applies the exact production parser to bounded raw streams held only in memory and requires its success before claiming backend usability. Individual probe records remain `policy_unverified`; producing a record alone cannot grant admission. Claude has no admitted aggregate.

`codex-0.166.0-admission-v4-direct.json` replaces those usability claims with clean exact-recipe text and image evidence. It binds Codex 0.166.0, executable SHA-256, recipe SHA-256, the bundled catalog resource SHA-256, the twelve exact model records and the captured native default `gpt-6.1-sol`. Only that model was exercised live. Native search remains false in this record pending a strict-parser and structured-source confirmation; no returned source is inferred from assistant prose or an open-page request.

The final binding is `codex-0.166.0-admission-v5-search.json`: the same binary, resource and recipe now also have a successful real backend search observation. Its twenty structured returned URLs pass the final stricter URL validator offline. The native call used its recorded parser revision; subsequent hostname/Unicode and literal `is_error` hardening passed 225 offline parser checks against source-shaped synthetic fixtures. The record explicitly distinguishes those scopes and makes no native raw-output replay claim.

Every later attempt records the verifier SHA-256, exact observed CLI version, resolved executable SHA-256, policy recipe and its SHA-256, environment key names, and synthetic content hashes. Earlier attempts are immutable. The base recipe subsequently includes `--skip-git-repo-check` so approved non-Git working directories are supported; the successful host challenge already used that flag as a safe probe argument. Model and reasoning selections are probe parameters rather than additions to execution authority.

## Security controls and limitations

Codex combines native read-only enforcement and approval `never` with ephemeral sessions, ignored user config and execpolicy rules, disabled MCP globally, hooks, plugins, notifications, both agent backends, shell/unified execution, code-mode and browser/computer bridges, and skill discovery. `project_doc_max_bytes=0` suppresses project instruction loading. An empty `mcp_servers={}` override is not sufficient because configuration tables merge. The exact accepted recipe is pinned in the aggregate and production `clink/policy.py`; production admission compares the bound recipe rather than an unfinished candidate.

The base recipe explicitly selects live search mode. A native search capability requires observed completed search items with recognized structured source URLs from returned `results`; model prose, requested action URLs, and remembered documentation links do not suffice. The bounded exporter distinguishes returned-source URLs from requested-action URLs and strips credentials, query strings and fragments. Direct mode restored native search items. Pinned upstream source also demonstrated duplicate `id` keys from an outer item plus flattened search payload. A narrow Codex-only handling of exactly that metadata duplication preserves strict rejection of duplicate semantic fields and genuine errors; the successful backend lookup establishes the resulting path. Empty recognized results are `no_results`; missing/unrecognized result shapes and open-page intent alone remain unsupported.

## Bundled native catalog projection

The installed binary's `codex debug models --bundled` command captured 745,260 bytes of genuine shipped metadata without refresh or inference. The original snapshot and `codex-0.166.0-catalog-provenance.json` are retained here. The 757,141-byte runtime resource `clink/resources/codex-0.166.0-models-direct.json` preserves all twelve native IDs, ordering, priorities, visibility, instructions and unknown fields; only `tool_mode` is set to the exact literal `direct`. Tests compare every other field against the original snapshot.

The catalog-mode selector takes precedence over feature flags. The genuine bundled entries requested code mode, which produced an unavailable-host warning even with code-mode features disabled; the CLI projected that warning as an error item. Selecting direct mode bypasses code-mode executor registration and the unavailable-host warning while the host remains disabled. `suppress_unstable_features_warning=true` separately removes the experimental-feature warning. The strict parser continues rejecting genuine error items.

Canonical argv uses `model_catalog_json="@CLINK_CODEX_MODEL_CATALOG@"` and a resource-relative path/SHA entry. The renderer verifies the pinned bytes before replacing that one placeholder with the installed absolute resource path. The backend also checks resource hashes before queueing and spawning. The catalog is authoritative and static: both captured ChatGPT and API default selection produce `gpt-6.1-sol`, but later remote defaults are not followed. The wrapper passes an explicit effective model, labels `default_origin=pinned_native_catalog`, and rejects native prefix/namespace aliases outside the exact admitted record set.

The success replay `tests/fixtures/clink/codex-0.166.0-direct-success.jsonl` is explicitly normalized from the observed four-event shape. Identifiers, response body and usage were replaced by controlled placeholders; the original stream was memory-only. Its provenance records that distinction. The search fixture and its provenance are explicitly source-shaped and synthetic. The successful real lookup did not retain a raw replay. The optional controlled-search exporter can preserve literal duplicate ID keys and result key/type structure while replacing bodies and identifiers; it was not used to claim an actual replay for the final native call.

Native `apply_patch` registration is independently model-catalog gated. Disabling shell features does not itself remove that tool. The accepted advisory contract concerns inaccessible mutation authority: source-supported read-only controls, effective config, the protected hostile request, and a direct native OS-denial probe together support that boundary. There was no emitted write-tool attempt in the completed host challenge. Read-only native profiles can read beyond the workspace; the production adapter supplies inline context and explicit admitted images, while file/command-reading tools are disabled. This is not a claim of OS read isolation.

Authentication success means the native CLI completed a real request. The verifier never examines the credential source, token or account identity. Requested model identity is recorded separately from actual model identity, which the observed JSON stream did not report. Cancellation proves local group termination, not cancellation of remote work already submitted or reversal of usage. Managed policy can change externally; changing the admitted executable, exact version or policy recipe invalidates its content-addressed admission.

Claude remains `policy_unverified`. Its candidate recipe uses restricted mode, the five read/search tools, safe mode, no Chrome/slash commands, empty strict MCP, no permission prompts, no session persistence, and `disableAllHooks`. It intentionally omits `--bare`, which disables OAuth/keychain auth. Official documentation says safe mode still applies managed hooks and other managed policy, including remote policy that outranks CLI settings. The no-inference observation did not establish that all such authority is absent, or validate live write denial/file scope; no Claude model prompt was sent.

## Reproduction

Use a new durable evidence filename for each run. The verifier refuses to overwrite a record or store evidence only in a temporary directory. Ordinary preflight is free of model prompts:

```sh
.venv/bin/python scripts/verify_cli_policy.py codex --output docs/verification/cli-policy/codex-new-preflight.json
.venv/bin/python scripts/verify_cli_policy.py claude --output docs/verification/cli-policy/claude-new-preflight.json
.venv/bin/python scripts/verify_cli_policy.py codex --sandbox-only --output docs/verification/cli-policy/codex-new-sandbox.json
```

The native sandbox may require the approved host route because an enclosing macOS sandbox cannot apply a nested sandbox. Native authentication/cache maintenance also needs its normal operational access. Those requirements do not justify changing the restrictive CLI flags or host configuration.

Each explicitly requested live mode submits at most one harness prompt and never retries or substitutes a CLI:

```sh
.venv/bin/python scripts/verify_cli_policy.py codex --live --output docs/verification/cli-policy/codex-new-challenge.json
.venv/bin/python scripts/verify_cli_policy.py codex --live --cancel --output docs/verification/cli-policy/codex-new-cancellation.json
.venv/bin/python scripts/verify_cli_policy.py codex --live --capability search --output docs/verification/cli-policy/codex-new-search.json
.venv/bin/python scripts/verify_cli_policy.py codex --live --capability image --output docs/verification/cli-policy/codex-new-image.json
.venv/bin/python scripts/verify_cli_policy.py codex --live --capability text --output docs/verification/cli-policy/codex-new-text.json
```

Probe records remain `policy_unverified` until an independently reviewed aggregate binds the accepted evidence and explicit limitations. A command-line flag or operator-authored manifest cannot promote them into production admission.

## Primary references and delegation receipt

Retrieved through Context7 `/openai/codex` and `/websites/code_claude` during this verification:

- [Codex config schema](https://learn.chatgpt.com/docs/config-schema.json) and [configuration layering](https://github.com/openai/codex/blob/main/codex-rs/config/src/loader/README.md).
- [Exec controls and native-auth retention](https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs).
- [Core tool planning, including independently gated patch tools](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/spec_plan.rs).
- [Canonical temporary thread controls](https://github.com/openai/codex/blob/main/codex-rs/tui/src/temporary_structured_request.rs).
- [Native read-only permission profile](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs).
- Version-pinned direct-catalog correction: [catalog mode precedence](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/tools/mod.rs#L66), [warning condition](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/session/turn_context.rs#L1422), [direct-mode executor early return](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/tools/spec_plan.rs#L1018), and [static catalog manager](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/models-manager/src/manager.rs#L858).
- Version-pinned search research at [release commit 6ff1e1c](https://github.com/openai/codex/tree/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856): [standalone registration gate](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/tools/spec_plan.rs#L1641), [native result DTO fixture](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/codex-api/src/endpoint/search.rs#L139), and [JSONL action/results preservation](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/exec/src/event_processor_with_jsonl_output.rs#L303). Source support was distinguished from the failed installed-runtime capability probes.
- [Claude CLI restricted/safe/strict MCP controls](https://code.claude.com/docs/en/cli-reference), [managed precedence](https://code.claude.com/docs/en/managed-settings), and [all-hooks setting](https://code.claude.com/docs/en/settings-reference).

The implementation/research delegation requested Sol 6.1/ultra; the independent read-only correctness review requested Sol 6.1/high. Effective agent runtime metadata was unavailable and remains unverified. The native Codex challenge explicitly requested `gpt-6.1-sol`/`high`; this is separate from delegation-runtime metadata and does not prove the provider's actual model identity.
