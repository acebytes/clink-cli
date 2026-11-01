# Codex attempt 3: bounded refusal diagnosis

✅ High confidence in the observed native MCP approval refusal. The annotation mechanism below is source-supported; the old trace does not separately retain its resolved per-tool approval mode. This diagnosis submitted **zero model turns** and made **zero configuration or fixture writes** before the separately authorized source repair.

The preserved [attempt 3 receipt](verification/native-codex-0.166.0-attempt3.json) records one consumed model call, `permission_needed`, passing edit/test observations, and missing MCP/specialist evidence. It remains a failed capability check; its contents and original fixture pins are unchanged.

Read-only native metadata lookup selected exactly one artifact by the exact synthetic fixture cwd from the [trust packet](native-codex-trust-packet.md). Before reading its tool outputs, the artifact's first session metadata was checked for that same cwd, CLI `0.166.0`, and the attempt's bounded start window. The receipt timestamp is invocation start, not completion. The matched artifact SHA-256 was `fc32c4830a7b104f1f3812826abb574f3ce418fdb9d48b25f1591450a73b52d3`. No unrelated session bodies were read, and no native IDs, artifact paths, prompts, commands, credentials, or raw session bodies are exported here.

Five existing native custom-tool outputs were inspected privately. Exactly one output leaf matched the native diagnostic:

```text
MCP tool call requires approval, but approval policy is never
```

Its private call correlation identified an input referencing the fixture's `native_fixture`/`canary`; the rollout wrapper did not provide a separate canonical server/tool projection. The persisted turn context also recorded approval policy `never`. No other recognized approval-denial signature or subagent-spawn call was observed. This establishes native MCP approval refusal, rather than the dedicated forbidden-printf check or a claimed child refusal. It does not establish the actual execution model or a complete effective permission profile.

At the pinned Codex revision, that exact diagnostic comes from the [MCP Never branch](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/mcp_tool_call.rs#L1655-L1662). MCP approval mode uses per-tool → server default → Auto precedence; Auto with absent destructive/open-world annotations requires approval. The original canary omitted annotations. Project/hook trust does not preapprove this separate MCP decision. [Policy lookup](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/codex-mcp/src/server.rs#L490-L517), [annotation predicate](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/mcp_tool_call.rs#L2545-L2575).

The authorized repair is limited to the Codex synthetic canary:

```json
{"readOnlyHint": false, "destructiveHint": false, "openWorldHint": false}
```

The MCP specification defines nondestructive updates as additive. Because the tool writes an observation, it cannot claim read-only behavior. The repaired Codex program exclusively creates its bounded observation file and fails on any existing target without changing its bytes. It neither deletes files nor contacts external services. This makes all three hints truthful; shared non-Codex program bytes remain unchanged. [Official annotation semantics](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/2026-07-28/docs/specification/2026-07-28/schema.mdx).

Native mandatory review and explicit policy remain authoritative: Codex's annotation-based early exits are guarded by `!strict_auto_review`. No permission mode, global setting, production recipe, or independent forbidden-command rule changes. [Native approval checks](https://github.com/openai/codex/blob/6ff1e1ca3b30fc8b914720681fcf5fe62e95f856/codex-rs/core/src/mcp_tool_call.rs#L1529-L1574).

The fresh replacement fixture requires a new prepare receipt and exact six-record binding; historical pins must not be repointed. The plan retains the failed call and records five cumulative allowed submissions: one consumed plus one reviewed four-check sequence, stopping at the first failed gate. New verifier diagnostics should project only fixed reason/tool categories and exact fixture-operation predicates from bounded native JSONL. Full MCP/child, follow-up, forbidden-command, and cancellation evidence remains required for admission.
