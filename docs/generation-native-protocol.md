# Native generation protocol v2

The additive [schema 3 and four-family contract](generation-native-families.md)
defines typed model selection and read-only capabilities. The schema 2
Claude/Codex recipes and parser identities described here are preserved.

This amendment implements the user's explicit decision on November 1, 2026:
“Use Codex’s native tools and permissions; revise the contract explicitly.”
It defines a separate `generation_native_v1` execution profile. The original
[v1 protocol](generation-protocol.md), its refusal, and existing consultation and
native-task profiles remain unchanged. Implementation and admission are separate
gates; this document alone does not enable production execution.

## Request and result

Invoke `clink generate --request-json -`. V2 retains the v1 request fields and
byte limits, with `schema_version: 2` and two additional required fields:

- `execution_mode`: exactly `"native"`.
- `project_dir`: an existing absolute canonical directory. Reject symlink or
  noncanonical wire spellings; callers resolve the path before capturing it.
  The path is limited to 4,096 UTF-8 bytes. Clink does not silently rewrite the
  hashed request.

The canonical hash covers exactly `schema_version`, `execution_mode`,
`project_dir`, `cli`, `model`, `instructions`, `input`, and `timeout_ms`. Retain
v1's exact Unicode serialization and binary64 timeout rules. V1 requests never
gain native authority by migration or default.

Results retain v1's top-level fields, statuses, and exit codes, with
`schema_version: 2` and `execution.profile: "generation_native_v1"`. Add these
required execution fields:

| Field | Meaning |
| --- | --- |
| `execution_mode` | `"native"`. |
| `project_dir` | Validated canonical path, or `null` before validation. |
| `side_effects_possible` | False only when native launch is known not to have been attempted; true for possible or ambiguous launches, including failures. |
| `permission_observability` | `"limited"`. |
| `fulfillment_verified` | Always false. |

`catalog_sha256` is null because generation uses the selected CLI's native catalog.
The explicit requested/effective model is not proof of actual model identity. Preserve null
when native output does not report the actual model.

Recognized source-supported native refusals return failed / `permission_needed`
/ exit 1. Never infer a refusal from arbitrary assistant prose or infer granted
permission from the absence of a refusal event. Successful completion means a
validated final response was returned, not that every tool succeeded or the
transformation was fulfilled.

## Authority and project selection

One attempt means one top-level invocation of the selected native CLI. Native
tools, subagents, integrations, and internal provider retries may perform
additional work. Bounds on time and output are not transaction, filesystem, network, or cost limits.

Preserve native user/project/system/managed configuration, authentication,
tools, hooks, skills, integrations, and headless permissions. The new recipe
must not suppress tools, inject a projected no-tools catalog, ignore user
configuration/rules, override sandbox/approval policy, grant trust, relay
approvals, or bypass managed restrictions. Prompt wording separates the source
material from transformation instructions; it is not a capability boundary.

Codex exec applies native headless approval behavior, including its own default
and configured AutoReview path. This is not a promise of identical interactive
permission behavior. Only source-verified lifecycle/presentation flags belong
in the separate recipe.

Load Clink configuration independently of stdin request data. The selected
project must be within the selected client's independently configured
`allowed_roots`. Freeze its canonical path and directory identity before queuing
and revalidate before launch. Never default to the installation checkout, configured `working_dir`,
or the caller's temporary directory.

For this MVP, roots must be explicit in user-scope configuration (including the
legacy `~/.pal/cli_clients` location) or independently supplied
`CLI_CLIENTS_CONFIG_PATH`. Bundled or implicit startup-directory roots do not
admit native generation. Do not discover `.clink` manifests, expand roots, or
grant trust from `project_dir`. Project MCP setup is not automatically loaded by
this programmatic CLI. Native Codex may load its own configuration at the chosen
project under native trust checks. Roots constrain project selection, not all
native tool access or effects.

Use the native environment baseline. Additional integration environment names
are operator-owned, never request fields. A separate optional manifest section,
`generation_native`, contains only `inherit_env` and `required_env`, using the
existing native name-validation rules. Do not log values or forward unrelated
secrets by default.

## Final response and lifecycle

Use native JSONL plus `--output-last-message` with an owned private file. Supply
the same canonical project as the process working directory and native `--cd`
argument; this selects the project without changing its native permissions.
Completion requires process exit zero, a valid completed terminal event, and a
fresh nonempty bounded UTF-8 regular final-message file. The parent directory is
0700; the file is 0600. Validate identity and safe reads, and clean up on every
controlled outcome. Native file-write errors do not necessarily alter exit
status, so exit zero alone is insufficient.

Final-file reads have a separate 10 MiB defensive capture ceiling. Valid text
within that ceiling but beyond 131,072 UTF-8 bytes returns the v1-style
incomplete prefix. Files above the capture ceiling fail with `output_limit`
and no prefix because the complete file cannot be validated within the bound.
This bounds Clink's reads, not all disk writes performed by native tools.

Accept supported typed lifecycle/tool events, validating shapes, identity,
ordering, and terminal outcome. Do not return operational stdout, commands,
tool results, reasoning, or concatenated intermediate agent messages as the
answer. Codex's supported final-file output selects the last agent message with
a Plan fallback. JSONL notifications can be dropped under backpressure; do not
require final-file equality with the last observed streamed message.

Unknown or malformed terminal behavior, failed/interrupted turns, invalid or
missing final files, and recognized permission refusals cannot produce ordinary
success. Recoverable tool failures may coexist with native completion; expose
bounded warnings/counts where the supported interface provides them.

Preserve the existing process pool, deadlines, bounded streams/responses,
parent-loss handling, and controlled descendant cleanup. No automatic retry,
resume, fallback, or repair call. Cancellation does not undo prior effects or
guarantee detached/remote work stopped. A parse failure, timeout, or model
mismatch can occur after side effects.
