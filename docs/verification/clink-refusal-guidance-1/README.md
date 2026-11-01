# Agy refusal guidance and discovery verification

Date: 2026-11-01. Baseline: `3e0fae1678c22f811da7898b3989d1d02b4aa6ad`.

The reported Agy refusals were correct admission decisions. This change makes them actionable without launching a CLI, widening roots, changing native permissions, or admitting consultation.

- Native `path_not_allowed` now returns fixed public project/context guidance and an operator-manifest remedy. Arbitrary exception messages, remedies, metadata, and paths remain redacted. Rejection creates no task, request-key reservation, job, or project lease.
- Consultation `policy_unverified` explicitly identifies consultation and retains the verification-harness guidance. Its conditional native route requires an explicit `action: "start"` and separately admitted profile; it does not switch modes automatically.
- Advisory discovery separates `installed` from provisional `available`, which additionally requires verified consultation policy and POSIX support. Exact launch validation and authentication remain outside discovery.
- The configuration guide explains startup-directory defaults and contains a complete Agy manifest for an external project, preserving both sets of roles. Regression tests load that actual example through the user and explicit-file override locations using isolated fixtures.

## Verification

```sh
.venv/bin/python -m pytest tests/test_clink_backend.py tests/test_native_tasks.py tests/test_native_server.py -q
.venv/bin/python -m pytest tests/test_clink_registry.py -q
.venv/bin/python scripts/test_offline.py clink-refusal-guidance-1
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

Focused results: 126 backend/task/server tests and 157 registry tests passed. The full nonintegration suite passed **2,147 tests and 16 subtests**, compared with the baseline's 2,135 tests and 16 subtests: 12 additional regression cases, no removed tests. All repository-wide lint/format checks passed. Python was 3.12.15. The immutable [test output](pytest.txt), [JUnit report](junit.xml), [command/result](result.json), and [isolation preflight](isolation.txt) record the full run.

The full suite ran with network access blocked, unrelated home-file reads denied, and a restricted environment. No installed model CLI was launched, no live submission budget was consumed, and no user manifest, allowed root, authentication, or host trust configuration was changed. Live native execution in the user's external project remains untested by this change. Existing admission records, recipes, and historical receipts were preserved.

## Independent review and delegation

The independent read-only implementation/completion reviewer reported **no actionable findings** in the nine changed source/test/documentation files. It confirmed pre-reservation refusal handling, diagnostic redaction, explicit native routing, process-free discovery, and documentation consistency. The reviewer did not rerun tests or invoke a live CLI; the conductor supplied the verification results above.

Reviewed patch: `git diff HEAD -- README.md clink/backend.py clink/tasks.py docs/cli-configuration.md docs/native-cli-tasks.md tests/test_clink_backend.py tests/test_clink_registry.py tests/test_native_server.py tests/test_native_tasks.py` against the baseline above, SHA-256 `6db70eabcedaf00d7e50b9ecd51c1e8295fa6e4765d2e9fa512d4509231322d2`. This evidence summary was added after review and is outside that patch hash.

| Task | Agent role | Explicit selection | Effective runtime evidence |
| --- | --- | --- | --- |
| Focused scope plan | `planning` | `gpt-6-astra` / `ultra` | Unavailable; unverified |
| Source tracing and implementation | `explorer` / `default` | `gpt-6.1-sol` / `ultra` | Unavailable; unverified |
| Independent review | `default` reviewer | `gpt-6.1-sol` / `high` | Unavailable; unverified |

Standing user authorization covers routine same-scope development, verification repairs, and independent review. No new host-setting or native execution authorization was needed. Risk & mitigation: public diagnostics could expose private paths and discovery could overstate readiness; fixed text, no raw diagnostic forwarding, separate installation/admission fields, and explicit provisional launch semantics address those risks.
