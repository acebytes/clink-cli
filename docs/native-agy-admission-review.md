# Antigravity native admission evidence

Independent evidence review accepted the single
[attempt 10 lifecycle run](verification/native-agy-1.4.12-attempt10.json).
The [admission record](verification/native-agy-1.4.12-admission.json) preserves
all ten top-level submissions and their receipt/preflight hash chain. Accepted
gates come only from that final four-submission invocation; earlier failures
were not combined into a passing lifecycle or retrospectively reclassified.
Separate independent implementation and completion review accepted production
integration with no outstanding actionable findings. The immutable admission
receipt records accepted lifecycle evidence; this document records the later
integration verdict without changing its pinned bytes.

| Accepted check | Cumulative ordinal | Observed result |
| --- | --- | --- |
| Edit and capabilities | 7 | Native failure then success tests, arithmetic edit, independent final test, project hooks, environment MCP canary, named child start/exact read/strict idle stop; `completed` |
| Explicit follow-up | 8 | Same private native session and context token; `completed` |
| Permission refusal | 9 | Exact protected-target hook and untouched target; `permission_needed` despite exit 0 |
| Cancellation | 10 | Mutation and identified child readiness observed; child/process group absent, pool and project leases released; `cancelled` without emergency cleanup |

The final receipt has `native_verified=true`, no gates, ten consumed submissions
out of ten, zero automatic retries, and unchanged fixture configuration after
shutdown. Internal native subagent/model/tool work is not counted by the
top-level allowance. No further model calls were needed to prepare admission.

## Pins and scope

The verified CLI reports version **1.4.12**. Its binary SHA-256 is
`34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3`;
the native recipe SHA-256 is
`da740ab92a35b480344a4dc07a517681dfbd5768f18ea01fbb65074337659a4f`.
The original receipt does not record architecture. A separately labeled later
read-only context capture confirms Darwin/arm64 and the same arm64 Mach-O
binary hash; it is not presented as contemporaneous receipt metadata. Production
admission is restricted to that exact version/binary/recipe and Darwin/arm64.

The recipe preserves CLI-owned authentication and native user/project
configuration. It sends one bounded user NDJSON event on stdin followed by EOF,
and explicit continuation uses `--conversation` with the exact privately bound
session. No bypass flags, global permission overrides, automatic resend/resume,
or production approval hooks are added. Verification approval hooks applied only
to fixed synthetic operations while native explicit Ask/Deny rules retained
priority.

The conductor reported source checkpoint
`270d1e527eeca4d5ec0a8a0d782b70bb848bb653` for the final live run. Later file
snapshots match that checkpoint before admission integration. Original
receipts/preflights pin binary, version, recipe, project and synthetic fixture
configuration; they do not independently record historical runtime source-file
hashes. The admission record labels those later snapshots and that limitation.
The invocation began at 14:08:29 UTC; this commit was created at 14:08:47 UTC,
recording the already frozen source while the invocation ran. It is a source
checkpoint, not a claim that this commit was HEAD at process launch. Committing
did not change the running source files; historical per-file attestation remains
limited as stated above.
The production delta is the Agy policy/receipt/platform binding, bundled profile
enablement, focused binding-rejection tests, and package resources. Claude/Grok
receipts and recipes, the Codex advisory profile, and native execution behavior
remain separate.

## Preserved failures and limitations

- Ordinals 1–4 retain native permission refusals and ordinal 2's
  `uncertain/parse_error`. Its exact historical stream failure remains unknown;
  subsequent source/parser corrections are not proof of its original cause.
- Ordinal 5 retains the generic synthetic MCP refusal. The later
  [bounded diagnosis](native-agy-attempt6-diagnosis.md) directly identifies its
  requested operation, but does not establish an explicit configured user Deny.
- Ordinal 6 retains the child observer failure. Its later
  [bounded diagnosis](native-agy-attempt8-diagnosis.md) proves child execution
  and the installed stop-reason mismatch; the old hook's individual idle/error
  comparisons were not recoverable. Only fresh ordinal 7 establishes the
  corrected strict lifecycle predicate.
- Actual execution model identity is **unknown** in every original ledger row.
  The native configured model was retained; there is no independent model claim.
- Child lifecycle and exact successful canary read are observed. Returned token
  or final-response content is not verified, and the legacy
  `fixture_subagent_canary_matches` observation remains false. Successful read
  evidence must not be described as token-return proof.
- Cancellation does not roll back effects or establish that detached/remote
  work has stopped. Server-side session bindings are volatile across restart.

Risk & Mitigation: native execution can mutate files and external systems before
failure or cancellation. Exact admission pins fail closed; approved roots limit
supplied project/context rather than all native authority. Keep explicit native
permissions and bounded cleanup evidence, and retain every original failure.

## Final integration verification

[Candidate 14](verification/native-adapters-candidate14/) passed 2,135 tests and
16 subtests with network and unrelated-home-file isolation. Ruff, Black check,
and isort check passed. Independent review separately passed 55 focused runtime,
resource, and Docker tests and verified binary/recipe/receipt/platform changes
fail before launch.

[Packaging verification](verification/native-adapters-packaging2/summary.json)
passed for the 118-member wheel and 123-member source distribution. The reviewer
independently matched their contents to all 119 current package inputs and the
unchanged existing admission resources. Installed-wheel MCP discovery returned
18 tools, five clients, and four native profiles, with Claude/Grok/Agy admitted
and native Codex disabled. All were unavailable without installed CLIs. No
discovery subprocess, network, model, or authentication probe occurred. The
earlier nested-sandbox transport failure remains preserved; no Docker image was
built or package published.

Execution used explicitly selected Sol 6.1/ultra, planning Astra/ultra, and
independent review Sol 6.1/high. Effective runtime metadata was unavailable and
remains unverified. No additional native calls were made for activation.
