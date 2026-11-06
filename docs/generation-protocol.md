# Programmatic generation protocol v1

This contract defines one bounded text-generation attempt for application callers,
including desktop and command-line clients. It is separate from advisory MCP tools
and native agent tasks. The implementation and admission evidence determine
whether an installed client can execute it.

## Invocation and ownership

`clink generate --request-json -` reads one UTF-8 JSON object followed by EOF from
stdin and writes one terminal JSON object followed by a newline to stdout.
Diagnostics use stderr and exclude request/output text, credentials, commands,
and raw native streams. The equivalent module entry point is
`python -m clink.cli generate --request-json -`.

The CLI calls a shared generation service using Clink's existing process pool.
It does not launch a child MCP server. Existing consultation and native recipes
retain their behavior. Generation has its own capability policy, parser, output
budget, and admission evidence. One request means one attempt; there is no retry,
resume, backend fallback, or model substitution.

## Request

| Field | Type and meaning |
| --- | --- |
| `schema_version` | Required integer, exactly `1`. Booleans are not integers. |
| `request_id` | Required UUID string identifying this attempt. A retry uses a new UUID. |
| `cli` | Required string; v1 accepts `codex`. |
| `model` | Required explicit native identifier from the admitted generation catalog; `auto` is invalid. |
| `instructions` | Required nonblank string, at most 65,536 UTF-8 bytes. |
| `input` | Required nonblank source string, at most 262,144 UTF-8 bytes. |
| `timeout_seconds` | Optional finite JSON number in `[1, 1800]`, default `120`. Booleans are invalid. |

Reject unknown or duplicate keys, coercions, invalid UTF-8, nonfinite numbers,
multiple JSON values, NUL, and oversized input before submission. Preserve exact
instruction and input bytes, including whitespace, Unicode, braces, and
backslashes. Blank checking does not trim accepted content.

The raw request and fully assembled model prompt each have a 1 MiB UTF-8 limit.
Read at most the raw limit plus one byte. Stdin must reach EOF within ten seconds.
The request deadline includes elapsed intake time from process startup.

Fixed generation instructions distinguish the transformation instructions from
the source material in `input`. Neither can grant capabilities. Roles, tool
names, files, images, continuation IDs, native actions, arbitrary CLI arguments,
and permission overrides are outside this contract. Named prompt discovery,
including `--systemprompt`, is deferred. Applications supply their captured
instruction text directly; arbitrary Python prompt files are never imported.

Example request (the model must also be admitted and available to the account):

```json
{
  "schema_version": 1,
  "request_id": "66f96fc9-969d-4c0e-ab92-e355fa0e7588",
  "cli": "codex",
  "model": "gpt-6.1-sol",
  "instructions": "Improve the supplied prompt. Return only the improved prompt.",
  "input": "Explain the tradeoffs of a cache for this application.",
  "timeout_seconds": 120
}
```

## Terminal result

Every result has exactly these top-level fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`. |
| `request_id` | Canonical UUID, or `null` if input did not establish a valid ID. |
| `status` | `completed`, `failed`, `cancelled`, `timed_out`, or `incomplete`. |
| `output` | Exact final text, bounded final-text prefix for `incomplete`, otherwise `null`. |
| `truncated` | True only when valid generated text exceeded the output budget and a prefix is returned. |
| `error` | `null`, or `{ "code": string, "message": string, "retry_after_seconds": number|null }`. |
| `duration_ms` | Nonnegative monotonic elapsed milliseconds, including intake and cleanup. |
| `request_sha256` | Effective validated request hash, excluding `request_id`, or `null` before validation. |
| `instructions_sha256` | SHA-256 of exact instruction UTF-8 bytes, or `null`. |
| `input_sha256` | SHA-256 of exact source UTF-8 bytes, or `null`. |
| `execution` | Execution identity object below. |
| `warnings` | Bounded array of `{ "code": string, "message": string }`; empty when none. |

Error messages contain no user content and are at most 1,024 characters. Clients
must accept unknown error codes as generic failures. Stable categories include
`invalid_request`, `input_limit`, `unsupported_model`, `policy_unverified`,
`cli_missing`, `authentication_required`, `rate_limited`, `timeout`, `cancelled`,
`output_limit`, `protocol_error`, `unexpected_tool_event`, and
`cleanup_unconfirmed`. More specific existing backend codes may be preserved.

`execution` always contains `cli`, `profile`, `requested_model`, `effective_model`,
`actual_model`, `recipe_sha256`, `executable_sha256`, `catalog_sha256`, and
`admission`. Unknown values are `null`. `profile` is `generation_v1`; `admission`
is `verified`, `unverified`, or `not_checked`. A requested/effective model is not
proof of a reported actual model. Preserve `actual_model: null` when unreported.

Decode timeout to IEEE 754 binary64 using round-to-nearest, ties-to-even, then
validate its finite `[1,1800]` range. Multiply by binary64 `1000.0`, round that
product to binary64 with the same rounding mode, then apply mathematical ceiling
to get integer `timeout_ms`. Use that deadline consistently. Exact-decimal
multiplication is not equivalent: `2.007` produces `2008` milliseconds.

The request hash covers exactly `schema_version`, `cli`, `model`, `instructions`,
`input`, and `timeout_ms`; it excludes `request_id`. Serialize equivalently to
Python `json.dumps(value, sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)`, then UTF-8 without BOM or trailing newline.
Escape quote and backslash, use short escapes for backspace/tab/newline/form
feed/carriage return, and lowercase `\u00xx` for other U+0000 through U+001F
characters. Emit all other Unicode scalars literally, including `/`, U+2028 and
U+2029. Do not normalize Unicode. Reject unpaired surrogate escapes in any
request string; valid pairs decode to their scalar. NUL remains invalid input.
Equivalent wire escape spellings must produce identical hashes.

Bound warnings to eight entries and the complete serialized result to 1 MiB.

| Exit code | Terminal meaning |
| --- | --- |
| `0` | Completed, nonempty validated final text, not truncated. |
| `1` | Admission, authentication, launch, transport, parser, model, or internal failure. |
| `2` | Invalid invocation/request or input limit. |
| `3` | Incomplete, including final-output truncation. |
| `124` | Timeout, including stdin intake timeout. |
| `130` | Cancelled after controlled cleanup. |

Generated text is limited to 131,072 UTF-8 bytes. Truncation retains a valid
Unicode prefix and returns `incomplete`, `truncated: true`, error `output_limit`,
and exit `3`. Do not return partial operational stdout after a timeout, parser
failure, or unexpected tool activity. A valid JSON envelope or exit zero from
the underlying CLI alone does not establish completion.

## Execution policy and lifecycle

Generation uses separately verified controls suppressing search, shell,
filesystem mutation, MCP, hooks/plugins/skills, delegation, and other tools.
Admission binds the exact binary, generation recipe, catalog, parser contract,
and supporting evidence. Existing consultation admission does not admit this
profile. Recheck executable/resource identity immediately before launching a
queued request. Unknown events and tool events fail the generation parser.

The current Codex 0.166.0 recipe has a trusted-code blocker,
`ordinary_mcp_runtime_ceiling_unavailable`. Its public interfaces do not establish
the required runtime exclusion of ordinary MCP. Supplied evidence, an observed
empty configuration, or candidate admission cannot clear this blocker. Production
and verifier live requests remain refused.

Use the existing bounded process pool, queue, stdout/stderr capture, and process
group cleanup. SIGINT and SIGTERM cancel the owning attempt and await cleanup
before reporting cancellation. EOF terminates the JSON body, not the attempt.
Loss of the original parent cancels owned work. A broken output pipe must still
permit cleanup. No result channel is guaranteed after the caller disappears.

Callers allow 15 seconds for graceful cancellation. Forced termination that
prevents confirmed cleanup is an interrupted/unknown outcome, not evidence that
all work stopped. Never automatically replay it. Cancellation does not undo
remote usage or guarantee revocation of an already submitted model request.
