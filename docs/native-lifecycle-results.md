# Native task lifecycle implementation results

Confidence: high for the reviewed implementation and exact verified Claude binary. This completes the [approved first lifecycle plan](native-cli-lifecycle-plan.md). Native admission applies to Claude Code 2.1.304 on the tested macOS installation; other binaries require fresh verification.

## Delivered behavior

`clink` now accepts `action: start`, `status`, `followup`, and `cancel`. Start requires an explicit CLI, approved project directory, prompt and unique request key, then promptly returns a task handle and attempt ID. Background work belongs to the server. Follow-up resumes the recorded private native session using new input; these actions never enter PAL replay. Omitted action and `consult` preserve the working Codex consultation path and analytical workflows.

Native tasks retain configured tools, hooks, skills, plugins, MCP integrations and subagents under native permissions. Separate native roles, model defaults, environment-name configuration and admission keep the restrictive consultation contract intact. Headless permission requests become `permission_needed`; no bypass or automatic retry is added. Missing required environment names are reported without values. `listmodels` describes native profiles without inference.

Task state is bounded to 64 tasks, 16 attempts per task, 8 MiB retained metadata/results and a 20,000-character latest result. Terminal records expire after three hours without polling extensions. Request-key redelivery deduplicates across the server, including after project renames or deletions. Project/session ownership prevents overlapping native writers; cancellation and shutdown retain ownership until process cleanup completes. The shared pool retains two running processes, eight waiters, a 30-second queue wait and at most a 1,800-second deadline; shorter client or request deadlines win.

Completed execution never asserts task fulfillment. Results distinguish execution state, agent-reported claims, unavailable results and possible partial effects. Restart loses task records and mappings; unknown handles do not recreate work.

The preserved agy/grok groundwork adds strict JSON parsers, synthetic fixtures and discovery manifests. It does **not** admit either CLI for native execution. Native images, interactive approval relays, PTY/tmux/viewers, durable recovery and general recursion scheduling remain outside this MVP.

## Verification

| Check | Result |
| --- | --- |
| [Baseline](verification/native-lifecycle-baseline/result.json) | 1,182 tests and 16 subtests passed. |
| [First implementation suite](verification/native-lifecycle-candidate1/result.json) | 1,310 tests and 16 subtests passed. |
| [Final isolated suite](verification/native-lifecycle-final/result.json) | 1,338 tests and 16 subtests passed; zero failures, errors or skips. |
| Check-only formatting | Ruff, Black and isort passed; whitespace checks passed. |
| [Distribution verification](verification/native-lifecycle-packaging/artifacts.json) | Wheel and sdist contain native roles/admission and retain the pinned Codex resource; no provider packages/catalogs. |
| [Installed-wheel smoke](verification/native-lifecycle-packaging/wheel-smoke.txt) | All 18 tools load; local discovery/version work without installed CLIs, provider imports or API keys. Zero model calls. |

The final suite adds 156 tests over the baseline; no baseline tests were removed. All three suite runs used the existing Seatbelt harness, which verifies blocked networking and denied unrelated home reads before pytest. Python 3.12.15, MCP 1.30.0, Pydantic 2.14.2 and pytest 9.2.0 were used. Check tools were Ruff 0.17.3, Black 26.10.1 and isort 9.0.2.

The first packaging attempt found missing development build dependencies. Its [failure log](verification/native-lifecycle-packaging/build.txt) remains retained. Installing the declared setuptools/wheel build tools into the development environment resolved it; the successful build used build 1.6.1, setuptools 84.2.1 and wheel 0.48.0. Runtime dependencies did not change.

Commands, from this checkout:

```sh
.venv/bin/python scripts/test_offline.py native-lifecycle-final
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
.venv/bin/python -m build --no-isolation --outdir dist/native-lifecycle
.venv/bin/python scripts/verify_distribution.py dist/native-lifecycle
```

The isolated harness preserves evidence labels: choose a new label when rerunning. On macOS it needs permission to invoke Seatbelt. Ordinary focused tests use `.venv/bin/python -m pytest tests/test_native_tasks.py tests/test_native_server.py -q`.

## Live evidence and review

The [combined native receipt](verification/native-claude-2.1.304-admission.json) pins all three source receipts. Attempts 1 and 2 preserve genuine partial failures: fixture permission rules initially denied operations, then an unavailable bare `python` prevented native test execution and the cancellation mutation. Corrections were diagnosed and bounded before further calls. Attempt 3 used a verified absolute interpreter and completed the remaining checks. Ten top-level native model invocations were consumed, with no automatic retry; internal subagent model requests were not independently counted.

The production registry, runtime, shared process pool and task manager proved edits; failing then passing native tests; configured hooks, subagent and MCP inheritance; private native session follow-up; permission denial without writing the protected target; and cancellation after a known mutation. The observed descendant/process groups were absent afterward, with pool slots and project leases released. No explicit model was passed; Claude reported `claude-opus-5-5` from native configuration. This establishes no coverage claim for other models or every installed integration.

Independent Sol 6.1/high reviews closed findings concerning malformed terminal success, numeric timeout validation, escaped-output capacity accounting, redelivery after filesystem changes, missing-environment diagnostics and verification candidate identity. The separately identified shorter configured-timeout fix was independently reviewed. Final admission review found the exact binary/recipe justified within recorded limits. The immutable receipt says review was pending **at capture**; this report records subsequent independent closure without rewriting that evidence.

Final independent completion review confirmed the MVP exit criteria, retained baseline cases, full-suite/packaging evidence and 116 local documentation links, with no required work or material findings remaining.

Execution agents selected Sol 6.1/ultra; the planning agent selected Astra/ultra; independent reviewers selected Sol 6.1/high. Selection was explicit; effective runtime metadata was unavailable and remains unverified.

## Risk, preservation and next steps

Native tasks can change files or external systems before failing. Cancellation does not undo effects. Observed process-group cleanup does not guarantee cleanup of detached descendants or remote MCP jobs. Approved context roots are not an OS sandbox, and external writers are outside server coordination. The known-depth guard cannot police recursion through an already-running MCP server. These limits are explicit in [native task usage](native-cli-tasks.md).

The original PAL checkout and its six local modifications were preserved; its diff SHA256 remains `87161dde0f327e82cd9245680323e895544f9618aadacf5ce05a76e1ac53dc0b`. Codex's consultation recipe, model catalog and admission remain unchanged. Apache licensing, copyright and Beehive Innovations attribution remain intact. No host native CLI configuration was edited or credential material copied. Native authentication/session maintenance remains CLI-owned; no production deployment occurred.

Required implementation work for this MVP is complete. Restart the MCP server to load it and configure an approved project root before starting work outside the checkout. Optional next work is native agy/grok adapters using the same lifecycle contract and their own genuine admission evidence. Standing authorization continues to cover routine development amendments, verification repairs and scoped independent review; deployment, unrelated disclosure and unlimited live probe loops are not implied.
