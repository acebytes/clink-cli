# Antigravity installed native contract

This note records read-only inspection of `/Users/d/.local/bin/agy`, Antigravity
CLI **1.4.12**, Darwin arm64 Mach-O, SHA-256
`34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3`.
Addresses below are virtual addresses in that exact binary, whose image base is
`0x100000000`. They identify Go reflection metadata and bounded disassembly, not
published source code or a guarantee for another version. No credentials,
user configuration values, raw sessions, or model calls were used for this research.
This evidence does not admit the native adapter.

## Why two tool contracts matter

The binary contains both classic Cortex converters and separate oneharness
tools. The [official CLI hook examples](https://antigravity.google/docs/hooks/)
describe several classic contracts. A shared tool name does not establish shared
arguments. The retained [fourth live submission diagnostic](verification/native-agy-1.4.12-attempt5.json)
proved an exact command/wait match, missing `Cwd`, and unexpected keys. It did not
retain those keys and does not prove that `toolSummary` was observed.

| Operation | Classic contract | Installed oneharness contract |
| --- | --- | --- |
| Command | `run_command`: required `CommandLine: string`, `Cwd: string`, `WaitMsBeforeAsync: uint64` | `run_command`: required `CommandLine: string`, `toolSummary: string`; async variant also requires `WaitMsBeforeAsync: uint64` |
| Whole-file write | `write_to_file`: required `TargetFile: string`, `Overwrite: bool`, `CodeContent: string`, `Description: string`; optional `Append: bool`, `ArtifactMetadata: object` | `write_file`: required `Path: string`, `Content: string`; optional `Overwrite: bool`, `Append: bool`; artifact variant adds optional `ArtifactMetadata: object` |
| Child invocation | `invoke_subagent`: required `Subagents: array`, whose entries require `TypeName: string`, `Role: string`, `Prompt: string` | `invoke_subagent`: one flat object requiring `TypeName: string`, `Role: string`, `Prompt: string`; no `Subagents` array |
| Synthetic child read | Classic fixture uses `view_file` and `AbsolutePath` | `read_file`: required `Path: string`; optional `StartLine: integer`, `EndLine: integer`, `ByteOffset: integer` |

This table enumerates the researched command/write/invocation fields and the
oneharness read schema. It does not enumerate unrelated classic read options.
An optional field is not permission to accept arbitrary additional keys.

## Commands and working directory

Classic `tools.RunCommandArgs` metadata is at `0x109a7c298`;
`RunCommandToolConverter.ToolCallToCortexStep` is at `0x101b428d0`. Its five
schema-hidden fields are still JSON fields:

| Field | Go type | JSON omission |
| --- | --- | --- |
| `NotificationTimeoutSeconds` | uint64 | `omitempty` |
| `IsDaemon` | bool | Not omitted |
| `RunPersistent` | bool | Not omitted |
| `RequestedTerminalID` | string | Not omitted |
| `BypassSandbox` | bool | Not omitted |

These tags alone do not prove that a hook receives added zero values.
`PreToolHookConverter.applyPreToolHooks` reads the current
`ChatToolCall.ArgumentsJson` at offset `0x28`, decodes a JSON map, and constructs a
`structpb.Struct` (`0x1015a8c6c`–`0x1015a8cd0`). This precedes the underlying
typed converter (`0x1015a869c` versus `0x1015a8880`). It does not read the distinct
`OriginalArgumentsJson` field at offset `0x80` or itself insert command defaults.

The oneharness types are `runCommandArgs` (`0x1095e9da8`),
`asyncRunCommandArgs` (`0x1097230c8`), `runCommandWithSandboxArgs`
(`0x1095e9e38`), and `asyncRunCommandWithSandboxArgs` (`0x1098291b8`).
`RunCommandTool.ParameterSchema` (`0x101e35b70`) selects these variants.

- `toolSummary` is a required descriptive string, documented in its field tag as
  a brief two-to-five-word noun phrase.
- Async `WaitMsBeforeAsync` is 500–10000 milliseconds; execution clamps to that
  range. A value of 10000 does not guarantee a long command never backgrounds.
- Async `IsDaemon` is optional, defaults to false, and uses `omitempty`. True is
  intended for indefinitely running support processes.
- Sandbox variants expose optional `BypassSandbox: bool`, using `omitempty`.
  True requests execution outside the sandbox and elevated native permission.
- The async sandbox type also contains schema-hidden optional
  `NotificationTimeoutSeconds: uint64`, using `omitempty`.
- These variants have no `Cwd`, `RunPersistent`, or `RequestedTerminalID` field.

The oneharness command tool chooses a native workspace directory, rather than a
per-call `Cwd`. Its constructor calls `utils.PrimaryWorkspacePath` at
`0x101e35684`, then stores the result in `RunCommandTool.workspace` (offset
`0x58`). `buildCommandOptions` copies that value into both
`command.Options.WorkingDir` (offset `0x18`) and `Workspace` (offset `0x28`) at
`0x101e38a44`–`0x101e38a80`.

The hook's `workspacePaths` comes from `WorkspacePathsFromInfos`
(`0x1015a680c`), using the same environment workspace information and filesystem
as the constructor. `PrimaryWorkspacePath` (`0x1012cd870`) selects a workspace
URI and resolves it through `workspacePath`; `WorkspacePathsFromInfos`
(`0x1012cd3c0`) resolves the same paths for the hook. For one local workspace,
requiring `workspacePaths == [canonical_fixture_root]` binds that selection to
the fixture. Missing, different, or multiple paths do not establish the binding.
A fixed absolute interpreter/script command and a script assertion of the actual
working directory provide an additional execution observation. No actual
execution directory was proved by the failed diagnostic alone.

## Files and the protected target

Classic `code.WriteToFileArgs` (`0x1095d0ed8`, embedded base `0x1098c6550`)
contains the fields listed above; it has no `IsArtifact` field.

Oneharness `WriteFileTool.Name` (`0x101e52d90`) returns `write_file`.
`writeFileArgs` is at `0x109829578`; its artifact extension is at
`0x1095ea228`. `Path` describes an absolute file path and has minimum length one;
`Content` is the full file contents. `Overwrite` replaces an existing file's
entire contents. `Append` appends, creating the file if needed, and cannot be
combined with `Overwrite=true`. `Execute` resolves `Path` with `fs.AbsolutePath`
at `0x101e530cc`, validates arguments at `0x101e53210`, then builds/writes the
file edit at `0x101e53248`/`0x101e53288`.

The smallest synthetic replacement is exactly `Path`, `Content`, and
`Overwrite=true`, with the canonical `arithmetic.py` path and complete expected
source. Existing ownership, regular-file, single-link, and symlink checks still
apply. Do not grant append, artifact, replacement, or unknown fields merely to
handle this variant. The protected `permission-needed.txt` check must recognize
oneharness `Path` before any approval; classic `TargetFile` protection remains
separate. The hook matcher must include the actual `write_file` name.

Oneharness `ReadFileTool.Name` (`0x101e31b40`) returns `read_file`;
`readFileArgs` is at `0x109829338`. The smallest child canary read is exactly
`{"Path": "<canonical specialist.txt>"}`. Omit range/offset keys; a successful
read at an arbitrary offset does not prove the canary was read.

## Named child invocation and lifecycle gap

Classic `shared.InvokeSubagentArgs` is at `0x109478348`, with entry type at
`0x109a03888`. Entries also permit optional `Workspace`; `Model`, `Fork`, and
`Count` are schema-hidden.

Oneharness `InvokeSubagentTool.Name` (`0x101e25430`) returns `invoke_subagent`.
`Execute` decodes flat `tools.invokeSubagentArgs` (`0x109a90458`) at
`0x101e254a4`–`0x101e254e8`. Optional `Workspace` defaults to `inherit`;
`Model`, `Fork`, `Count`, and `WorkspaceUri` are schema-hidden. The bounded
fixture uses only the three required fields, the exact configured
`fixture-specialist` name, and one fixed read-only task. `launch` resolves the
name through `findAgentByName` at `0x101e267a8`. Its configured tool and prompt
must use `read_file` for this provider.

CLI child `SessionStart`/`Stop` hook inheritance remains **unverified**. The
[SDK hook documentation](https://github.com/google-antigravity/antigravity-sdk-python/blob/main/google/antigravity/hooks/README.md)
promises parent pre/post callbacks for child tool calls, and the
[CLI changelog](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md#131)
describes explicitly declared child hook paths. Neither establishes every CLI
JSON lifecycle event's inheritance. A later authorized live check may test this
unknown. Require a distinct child identity correlated to its named parent stream
entry, actual child `SessionStart`, exact successful `read_file` PostToolUse, and
`Stop` with `fullyIdle=true`, `terminationReason="model_stop"`, and no error.
Missing evidence stays unverified; do not invent `SubagentStart`/`SubagentStop`
event keys. This establishes an observed read/lifecycle, not return of the token
in the child's final response.

The final synthetic child explicitly declares `hooks: ["../hooks.json"]`,
resolving from `.agents/agents/fixture-specialist.md` to the existing
`.agents/hooks.json`. Installed `customizations.AgentFrontmatter` at
`0x109bb9490` contains a `Hooks: []string` field tagged
`yaml:"hooks,omitempty"` (field row `0x109bb96a0`); the v1.3.1 changelog above
documents resolution relative to the agent directory. This avoids relying on
implicit inheritance, while actual child hook delivery remains a live gate.

## MCP canary

The provider's `newToolCallerWithInstance` (`0x101e0c920`) clones server tool
definitions, retaining their parameter schema. It prefixes names unless
`McpServerSpec.SkipToolNamePrefix` is true (offset `0xac`). The prefix callback
stored at `0x10ba03518` resolves to `mcpcore.GetMcpToolNameWithPrefix`
(`0x1014c9070`): `mcp_<server>_<tool>`. The fixture's own configuration does not
request prefix suppression. Its `native_fixture` server and `canary` tool give
`mcp_native_fixture_canary`. The server-owned input schema in
[native_mcp_fixture.py](../scripts/native_mcp_fixture.py) is an object with no
properties and `additionalProperties=false`; the direct call arguments are
exactly `{}`. Do not broaden this into a generic `call_mcp_tool` grant.

## Verification boundary

These are installed static contracts and bounded fixture recipes, not successful
live capability observations. Preserve all prior failed submissions and the
remaining call allowance in [native adapter results](native-adapters-results.md).
Explicit native Ask/Deny rules, protected-target `force_ask`, independent review,
real child observations, continuation, cancellation, and cleanup remain required.
