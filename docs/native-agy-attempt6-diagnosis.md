# Antigravity attempt 6 refusal diagnosis

This is a later, read-only diagnosis of the unchanged
[attempt 6 receipt](verification/native-agy-1.4.12-attempt6.json), SHA-256
`516f45532bfc2ac26ffd9000d0fe1de1d35376ae497b0db5e2d8a1a36414eef7`.
It does not replace that receipt or convert its failed capability checks into
success. No new CLI/model/authentication calls, configuration changes, or native
artifact writes were made. Five submissions were already consumed when the
diagnosis began; this investigation consumed none.

## Directly correlated native records

The owned synthetic fixture's private conversation fingerprint selected exactly
one native conversation database. Inspection used SQLite read-only, immutable
mode with query-only enabled after checking ownership, regular-file type,
single-link count, and absence of a symlink. Only that conversation's bounded
step records were decoded. No native identifier, raw prompt/session, error body,
or unrelated conversation content is retained here.

The tool name and parsed arguments below come from each step's own
`CortexStepMetadata.ToolCall`, rather than nearby assistant prose or token order.

| Step | Native state | Operation comparison | Stored hook decisions |
| --- | --- | --- | --- |
| 2 | `DONE` | `run_command`; existing receipt separately observes the exact test-command approval and failing arithmetic test | `auto_approve`, `allow` |
| 3 | `ERROR` | `call_mcp_tool`; exactly `ServerName="native_fixture"`, `ToolName="canary"`, `Arguments={}`, with no other keys | `allow` |
| 4 | `CANCELED` | `invoke_subagent`; exactly the configured synthetic child's flat `TypeName`, `Role`, and generated `Prompt`, with no other keys | None |

Step 3's own structured error details contain the fixed classifications
`user_denied` and MCP. Its hook result has no overwrite or permission overrides.
This establishes which requested operation failed. It does **not** prove that an
explicit configured user Deny rule caused the refusal: a hook `allow` delegates
to native permissions. The stored hook result's configuration origin was not
inspected. No evidence establishes child execution or lifecycle delivery; the
matching child request was canceled.

## Installed wire and tool contracts

These references bind to Antigravity CLI 1.4.12, Darwin arm64, binary SHA-256
`34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3`,
as recorded in the [installed contract note](native-agy-installed-contract.md).
Virtual addresses and descriptor file offsets refer only to that binary.

- `CortexStepMetadata` at virtual address `0x109c4a558`: protobuf field 4 is
  `ChatToolCall`; field 34 is repeated `hooks_pb.PreToolHookResult`.
- `ChatToolCall` at `0x109b2f970`: field 2 is `Name`, field 3 is
  `ArgumentsJson`. The diagnosis parses that JSON and exports fixed comparisons.
- `CortexErrorDetails` at `0x109b52ac0`: field 2 is `ShortError` and field 3 is
  `FullError`; only the fixed reason categories above were exported.
- Embedded `CortexStepStatus` descriptor at file offset `0x3e2d508` defines
  3=`DONE`, 6=`CANCELED`, and 7=`ERROR`.
- Embedded `exa.hooks_pb.PreToolHookResult` descriptor at `0x391dd29` defines
  string `decision` as field **4**, `reason` as field 5, `overwrite` as field 3,
  and repeated `permission_overrides` as field 6. A separate telemetry message's
  field-1 decision is not this type.
- `tools.callMcpToolArgs` at `0x1096fd850` requires `ServerName: string`,
  `ToolName: string`, and `Arguments: jsontext.Value`. The actual failed step's
  tool name is `call_mcp_tool`; its exact synthetic arguments match this type.

## Smallest fixture correction

The prior fixture matcher recognized `mcp_native_fixture_canary` but omitted
`call_mcp_tool`. It therefore could not emit its canary-specific approval for the
actual dispatcher call. The bounded correction adds that tool to the project
matcher and approves only the exact three-key synthetic call above. The prompt
now asks for that installed dispatcher contract. The old direct alias still
approves only the same canary with exactly `{}`.

Missing keys, extra keys, changed server/tool values, non-object or nonempty
arguments delegate to native permissions. The protected-target `force_ask`
check remains first, and explicit native user Ask/Deny rules retain priority.
No broader MCP grant, global mode, bypass flag, or production admission changes.
Focused tests cover the matcher, exact request, negative variants, metadata-only
observations, and protected-target precedence. A future separately authorized
live check must still establish MCP execution, edits, and actual child lifecycle;
this offline correction establishes none of those capabilities.
