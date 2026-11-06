# Typed native generation and four-family discovery

Schema 3 adds typed selection to the separate `generation_native_v1` profile.
It does not change the schema 2 Claude/Codex request, result, recipe or parser
contracts. Invoke either schema with `clink generate --request-json -`.

The schema 3 request has exactly `schema_version`, `request_id`, `cli`,
`model_selection`, `instructions`, `input`, `execution_mode`, `project_dir` and
optional `timeout_seconds`. It replaces the schema 2 `model` field with one of:

```json
{"kind":"explicit","id":"gpt-6.1-sol"}
```

```json
{"kind":"native_default"}
```

Codex and Claude accept explicit selection, with the existing concrete-model
rules. Grok and Antigravity accept only native-default selection. Extra,
missing, mixed or unsupported selectors fail before launch. Native-default
does not select an invented model alias or emit a `--model` argument. The
request freezes the selection strategy; the CLI still owns its native default.

The canonical request hash replaces `model` with the exact closed selector;
all other schema 2 hashing, text, timeout, UUID and canonical-project rules
remain the same. Schema 3 results retain the existing fields and terminal exit
codes, set `schema_version` to 3, and add `execution.model_selection`.
Native-default requested/effective model IDs are null. Actual model is null
unless credible native metadata reports it. Antigravity's init model echo is
not such evidence. Grok requires one valid, unambiguous native usage entry.

Cross-language golden requests, results and canonical vectors are in
`tests/fixtures/generation_v3/`; schema 2 fixtures remain in their original
directory without changes. Completed schema 3 goldens carry explicitly synthetic
`a`/`b` executable/recipe digest values; they are protocol fixtures, not admission
evidence.

## New family transport and terminal contracts

The dedicated recipes follow the already inspected Grok 1.0.70 and Antigravity
1.4.12 native interfaces documented in [native CLI tasks](native-cli-tasks.md)
and their source audits. Their generation admission is separate from those
native-task receipts.

| Family | Native arguments | Input | Final output |
| --- | --- | --- | --- |
| Grok | `--prompt-file FILE --output-format json` | Bounded 0600 prompt file in an owned 0700 directory; empty stdin. | One successful JSON object, valid session UUID, `stopReason: end_turn`, exact nonblank `text`. |
| Antigravity | `--input-format stream-json --output-format stream-json` | One user NDJSON event, then EOF. | One matching conversation, init before steps, one final `result` with `SUCCESS` and exact nonblank `response`. |

The parsers validate strict UTF-8/JSON, reject duplicate keys/finals and failed
or incomplete terminals, and recognize only source-supported permission
refusals. New-family finals above 131,072 UTF-8 bytes fail with `output_limit`
and publish no prefix. They never publish reasoning, tool output or intermediate
text. Process exit zero is also required. These rules are independently bound
by the new parser and transport recipe digests.

All families use the shared process pool, deadline and cancellation ownership.
Grok's private file remains alive until process cleanup settles; cleanup only
removes the owned directory and its prompt entry. Replacement or unrelated
contents cause `cleanup_unconfirmed`. Requests do not create task/session
records, reuse sessions, retry, resume or fall back.

## Read-only capabilities

```sh
clink generation-capabilities --json
clink generation-capabilities --json --cli grok --executable /absolute/regular/executable
```

The command works before consumer setup and never invokes a native process,
reads credentials, creates grants or claims authentication. One bounded 64 KiB
schema 1 JSON object contains exactly `schema_version`, `generation_schemas`,
`families` and nullable `preflight`. Schemas are `[2,3]`. Each family row has:

- `id`, `display_name`, `executable_names`, `generation_schema`;
- `model_selection_kinds`, `default_model_selection`;
- `admission`: `status`, `admitted_version`, `executable_sha256`,
  `recipe_sha256`, `parser_sha256`, `evidence_sha256`.

Codex/Claude recommend schema 2 and their admitted explicit defaults.
Antigravity/Grok recommend schema 3 and native-default selection. Unverified
rows expose null admission pins. Preflight has exactly `cli`, `status` and
`executable_sha256`, with status `compatible`, `missing`, `invalid_executable`
or `admission_unverified`. It hashes one stable regular executable, without
starting it, using bounded reads and cancellation checkpoints. Changed or
unverified binaries cannot report compatible. Runtime admission rechecks
identities immediately before launch; discovery is not execution authority.

## Availability

The released native generation profile admits exact recorded builds of Codex 0.166.0, Claude Code 2.1.304 and Grok 1.0.70. Agy 1.4.12 has separate native task admission; generation remains unverified. A successful cancellation observation did not resolve earlier cleanup failures. Discovery does not authenticate an account, verify a model entitlement or override a refusal.

See the [evidence policy](release-evidence.md) for the boundary between package checks and historical observations.
