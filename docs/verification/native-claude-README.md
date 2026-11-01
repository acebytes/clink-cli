# Native Claude lifecycle verification

The [combined evidence](native-claude-2.1.304-admission.json) records observed runtime checks for Claude Code 2.1.304. Its immutable capture status predates independent review; [implementation results](../native-lifecycle-results.md) record subsequent review closure and admission. The new native task profile preserves ordinary configuration and permissions; the earlier advisory receipt has a different contract and remains unchanged.

The tested recipe is `claude --print --output-format stream-json --verbose --permission-prompts none`, with text input on stdin and `--resume` for follow-up. No explicit model was passed; the observed native model was `claude-opus-5-5`. [Anthropic's headless documentation](https://code.claude.com/docs/en/headless) describes normal configuration discovery and subscription authentication; [permissions documentation](https://code.claude.com/docs/en/permissions) describes native approvals. Installed help and live observations established the exact local behavior.

## Evidence chain

1. [Attempt 1](native-claude-2.1.304-attempt1.json), four model invocations plus one invalid-flag launch: native hooks, resume/context, and permission denial worked. Synthetic shell permissions were denied, so edits, MCP, subagents and mutation/cancellation were unproved. This is a partial standalone check, not admission.
2. [Attempt 2](native-claude-2.1.304-attempt2.json), four model invocations: actual registry/runtime/pool/task-manager execution proved edits, project hook execution, native specialist delegation, MCP with inherited synthetic environment, native resume/context, and `permission_needed`. Bare `python` was unavailable; shell tests and the cancellation mutation were therefore unproved.
3. [Attempt 3](native-claude-2.1.304-attempt3.json), two model invocations: after an offline shell diagnosis, the fixture used its available managed interpreter's absolute path. Actual native tool results contained the known failing unittest and subsequent passing unittest. Independent checks confirmed the edit and passing test. Cancellation after a known mutation returned `cancelled`; the observed descendant and process groups were absent, with process slots and project leases released.

All failed/partial attempts and ten consumed top-level native invocations remain recorded. The observed native Agent invocation may make additional internal requests; those were not independently counted. No automatic retries occurred.

## Scope and interpretation

The production checks used `ClinkRegistry`, `CLIBackend`, `NativeRuntime.run`, `ProcessPool`, and `NativeTaskManager.start/status/followup/cancel`. Only the verifier's subclass supplied a candidate admission tied to the exact binary, recipe and prior genuine receipt. The global admission mapping remained unchanged during verification.

The generated project supplied a scoped [native permission hook](https://code.claude.com/docs/en/hooks): it approved exact synthetic operations, asked for a dedicated permission target, and denied unrelated tool use. Ordinary user configuration discovery, hooks, plugins, agents, MCP and HOME authentication locations remained enabled. The generated [specialist agent](https://code.claude.com/docs/en/sub-agents) and [stdio MCP server](https://code.claude.com/docs/en/mcp) were exercised. This does not establish that every installed integration was tested.

The key event contract is an initial `system:init` with private `session_id`, followed by a terminal `result`. `subtype=success`, `is_error=false`, and exit zero can coexist with `permission_denials`. The dedicated denial also emitted `system:permission_denied`; the production parser correctly returned `permission_needed`. An intermediate tool error can precede terminal success, as the deliberately failing unittest demonstrates.

Cancellation does not roll back the observed file mutation. The result retains possible partial effects. Observed cleanup does not guarantee cleanup of detached descendants or remote MCP work. Native session/cache persistence remains owned by Claude; the verifier never reads or exports credential material or raw native session files.

## Reproduction

Offline tests and format checks make no model requests:

```sh
.venv/bin/python -m pytest tests/test_native_verifier.py -q
.venv/bin/python -m ruff check scripts/verify_native_cli.py tests/test_native_verifier.py
.venv/bin/python -m black --check scripts/verify_native_cli.py tests/test_native_verifier.py
.venv/bin/python -m isort --check-only scripts/verify_native_cli.py tests/test_native_verifier.py
.venv/bin/python scripts/verify_native_cli.py --help
```

Live checks require explicit `--live`, use a new evidence destination, preserve ordinary HOME/configuration, and may consume subscription usage. They run only in generated disposable projects. The helper refuses to overwrite evidence and never automatically resends a failed request. Keep further live runs bounded and explicitly authorized; existing evidence does not authorize an unlimited probe loop.

The verifier retains bounded technical fields, hashes, known sentinel booleans and counts. Raw prompts, results, tool inputs, session identifiers and environment values are excluded from durable receipts. Synthetic fixtures are generated from the committed helper and removed afterward; durable evidence lives in this directory.
