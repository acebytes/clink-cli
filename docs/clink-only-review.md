# Phase 2 review and handoff

Recorded 2026-10-31, America/Los_Angeles. **Phase 1 and Phase 2 are complete. Product implementation is not approved.**

## Deliverables and verification

- [Source audit](clink-only-audit.md): all 18 tools, clink internals, provider dependencies, source discrepancies, licensing, and provenance.
- [Design](clink-only-design.md): selected architecture, interfaces, configuration/migration, context, safety, implementation sequence, and acceptance criteria.
- [Baseline report](clink-only-baseline.md): pristine collection failure and compatibility-adjusted results, including unsuccessful isolation/harness attempts.
- [Provenance](baseline/2026-10-31/provenance.json): source commit/tree, branch/remotes, original PAL status, and product-tree verification.

The final unit baseline is **866 passed, 0 failed, 4 skipped, 16 deselected**, with 19 passed subtests and 2 warnings, using Python 3.12.15 and MCP 1.30.0. Pristine MCP 2.3.0 produced seven collection errors. The constraint exists only in the audit environment; product manifests remain unchanged.

Documentation links and four JSON examples were checked. The audit runner passes Ruff, Black, and isort checks. Raw pytest captures retain eight trailing-space progress lines exactly as emitted; those captures are excluded from the documentation whitespace check rather than rewritten as if they were original output.

Source, tests, executable configuration, dependency manifests, and licensing remain at `7afc7c1cc96e23992c8f105f960132c657883bb1`. This change set contains documentation and audit evidence/tooling only. The original PAL checkout remains separate with its six pre-existing modifications. No real CLI inference/authentication or remote provider tests were performed.

## Independent review history

The baseline and design received separate read-only reviews. Review findings were corrected in documents, without product repairs or manufactured implementation evidence.

| Review | Findings and disposition |
|---|---|
| Planning review before document preparation | Prompt authority, consensus participant identity/defaults, mutable file context, and the distinction between validated context roots and actual CLI read access required clarification. Incorporated in the design. |
| Baseline evidence review | No blocking discrepancies. Counts agree across console logs, JSON, and JUnit; unsuccessful attempts are retained; isolation claims are supported by the profile/probes; no credentials were identified. |
| Initial document review | Required precise Codex `--ignore-rules` wording, chat working-directory precedence, per-tool/revision workflow state, explicit exclusion of shared replay from consultants, and additional obsolete environment names. Source audit had no additional blocker. |
| Bounded amended-design confirmation | All prior document findings resolved; no material new contradictions identified. The reviewer confirmed the design hash below. |

Reviewed design SHA-256:

```text
30ab47786e1c060c8e6d9ceda8be9ccd49d8bfe85bb7f31ae3ecf4c6db0f56e6
```

This is a record of actual document review, not a claim that the proposed CLI policies or implementation have passed tests. The baseline reviewer did not re-run tests or verify remote fork history; the amended-design reviewer confined confirmation to the changed design sections. The conductor separately verified remotes, source identity, and recorded test execution.

Delegation configuration:

| Work | Agent role | Requested model / effort | Selection evidence |
|---|---|---|---|
| Source audit and writing | Execution/default, with focused read-only scouts | `gpt-6.1-sol` / `ultra` | Explicit spawn configuration |
| Design authorship and amendments | Dedicated `planning` | `gpt-6-astra` / `ultra` | Configured planning role |
| Baseline and design review | Independent default review agents | `gpt-6.1-sol` / `high` | Explicit spawn configuration; same design reviewer retained for confirmation |

Effective runtime model/effort metadata was unavailable for these agents, so effective selection remains **unverified**. No self-description is treated as runtime proof.

## Approval boundary and next work

**Required next decision: explicit approval of the design before Phase 3 product-code changes.** Approval of fork/audit/document preparation does not grant that approval. The MCP dependency-manifest fix is also deferred until that gate.

Once approved, follow the design's small-commit sequence: compatibility cap, shared runner/contracts, state/replay separation, tool ports, consensus/search, then provider removal and final documentation/verification. Preserve the baseline and compare retained behavior as well as totals. Keep the personal fork as writable origin; upstream pushes remain disabled. Do not open upstream pull requests.

Standing development authorization covers routine scope amendments and bounded verification/review repairs within approved work, including necessary scoped technical review materials. It does not override this task-specific implementation gate or authorize deployments, credential disclosure, reviewer mutations, fabricated evidence, or blind retries. Preserve genuine review history and the user's model preferences in subsequent handoffs.

Required implementation verification remains explicit: actual Codex/Claude login and advisory/MCP-isolation behavior, process cleanup, concurrency/replay behavior, and per-tool smoke results. Gemini remains unavailable until version-specific contract tests establish support. These are future execution gates, not missing claims to fill with assumptions.

Optional audit-tool improvement: the baseline runner currently overwrites a repeated label's outputs. Its reproduction documentation warns to preserve evidence first. A future hardening change could reject existing labels or require a fresh output directory; no saved attempt was overwritten in this work.
