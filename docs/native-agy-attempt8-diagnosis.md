# Antigravity attempt 8 child observation diagnosis

This later read-only diagnosis preserves the unchanged
[attempt 8 receipt](verification/native-agy-1.4.12-attempt8.json), SHA-256
`e00d4fdc944a547ddc5fc13be8f481b93d68693b90f1c5921e527efca2444c49`.
Six submissions were consumed out of nine at the start of the investigation;
this diagnosis used no new CLI/model/authentication calls or host configuration
writes. It does not retroactively pass the failed lifecycle check.

## Exact synthetic-session correlation

The owned fixture's private hook fingerprints selected only the root and child
native databases. Both were inspected in SQLite read-only, immutable, query-only
mode after ownership, regular-file, single-link, and symlink checks. No native
identifiers, raw session/prompt/error bodies, or unrelated session content is
retained here.

- The root's `invoke_subagent` step is `DONE` without error details. Its typed
  `InvokeSubagent` payload has one `SubagentSpec` named `fixture-specialist` and
  one `SubagentResult`. That result's conversation identity exactly matches the
  non-root identity in the owned hook log.
- That child has `read_file` with exactly the canonical synthetic
  `specialist.txt` path, `DONE`, with no error details. Its hook log records two
  `SessionStart` events and two successful exact-canary `PostToolUse` events.
  Duplicate delivery is observed; its cause is not required for this diagnosis.
- Both root and child `ExecutorMetadata` report termination reason enum 4 and
  absent/empty execution error. All their recorded steps are `DONE`.
- Child `Stop` hooks were delivered twice, but the old combined stop predicate
  was false. The observer sets its start/read/stop booleans only when all three
  pass for the same named child, so the receipt's all-false observations do not
  mean that no child ran.

## Installed producer differs from the documented example

Source references bind only to CLI 1.4.12 and binary SHA-256
`34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3`.
Addresses use the same convention as the
[installed contract note](native-agy-installed-contract.md).

`hooks.NewStopHook.func1` begins at virtual address `0x1015aa960`. It loads the
executor termination enum at `0x1015aaa40`, calls
`ExecutorTerminationReason.String` at `0x1015aaa44`, and removes the exact
`EXECUTOR_TERMINATION_REASON_` prefix with `strings.TrimPrefix` at
`0x1015aaa54`. No lowercasing occurs before assignment to the hook argument.
Enum 4 therefore produces exactly **`NO_TOOL_CALL`**.

The [official Stop documentation](https://antigravity.google/docs/hooks/#stop)
uses `model_stop` as an example reason. The fixture incorrectly treated that
example as the installed producer's sole normal-completion token. The installed
`StopHookArgs` descriptor at file offset `0x391e1cb` confirms separate string
`termination_reason` (field 2), string `error` (3), and boolean `fully_idle` (4).

The direct native correlation uses these embedded descriptors:

| Descriptor | File offset | Relevant fields |
| --- | --- | --- |
| `InvokeSubagent` | `0x388a719` | repeated specs 1, repeated results 2 |
| `SubagentSpec` | `0x3e2ab94` | type name 1 |
| `SubagentResult` | `0x3e2ad85` | conversation identity 1 |
| `ExecutorMetadata` | `0x3e21218` | termination reason 1, execution error 12 |
| `GenericStepResult` | `0x3e2af8b` | typed `google.protobuf.Any` payload 6 |

The stored root step wraps `CortexStep.generic` field 140, whose result field 2
contains that typed payload. These structural fields bind the child to the
named invocation; proximity of unrelated text is not used as evidence.

## Minimal correction and remaining live gate

The fixture changes only the successful stop-reason equality to exact
`NO_TOOL_CALL`, while retaining `fullyIdle is True`, absent/empty error, exact
named-child identity, actual start, and exact successful read requirements.
Ignored hook records now retain three fixed booleans for idle/reason/error
comparisons so a later failure can distinguish them without raw field values.
Synthetic tests use the installed token and reject wrong reasons, non-idle or
wrongly typed idle values, errors, missing fields/start, and unrelated children.
No tool approval, permission, hook declaration, or production recipe changes.

Historical `fullyIdle` and individual Stop error fields cannot be recovered
from the old combined boolean. Native executor completion has no execution
error, but that does not substitute for the hook fields. A fresh authorized live
check must establish the corrected strict Stop predicate. Returned canary text
remains unverified; a successful exact read does not prove the child's final
response content. All prior failed/partial receipts remain unchanged.
