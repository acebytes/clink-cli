# Project setup verification

Date: 2026-11-01. Baseline: `dcc1825e1b024106c7409b7f76a5518644d22589`.

Implemented the [project setup plan](../../project-setup-plan.md): explicit server project binding, project-over-user configuration precedence, deterministic portable manifests/prompts, project MCP registration, and checkout-local Claude `/clink:setup` skill. The package's public registry exports are now lazy so setup can avoid importing runtime `.env` configuration. No user project/home configuration, native CLI trust, admission resources, launch recipes, or credentials changed.

## Tests and checks

The authoritative full offline suite passed **2,219 tests and 16 subtests**, up from 2,147 tests and 16 subtests at baseline: 72 additional cases, no tests removed. It includes two simultaneously live MCP stdio processes launched from generated registrations, alternating discovery to verify independent roots and model configuration.

```sh
.venv/bin/python scripts/test_offline.py project-setup-1
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

All passed. The full suite used Python 3.12.15 with network access blocked, unrelated home-file reads denied, and a restricted environment. Retained files: [pytest output](pytest.txt), [JUnit](junit.xml), [isolation preflight](isolation.txt), and [exact command/result](result.json).

Focused checks also passed: 196 registry/binding/launcher tests, 39 setup tests, and the separate two-server stdio test. A preliminary worker run outside the authoritative isolation harness reported 296 passing tests and one native-runtime cleanup failure from sandbox `os.killpg` permission denial. An earlier focused run including that test passed, and the full isolated suite above passed without changing process cleanup code. This preliminary failure is retained here as an environment limitation, not a diagnosed production fix.

The [package verification](../project-setup-packaging-1/summary.json) built and checked the wheel/source distribution, installed the wheel with no index or dependency resolution, and ran setup/idempotence plus two generated MCP registrations against installed code. Each reported 5 configured clients and 18 tools with no installed native CLIs on its PATH. Native model calls, authentication probes, and package publication: zero. The local plugin is present in the source distribution; it is a checkout-local integration, not a standalone wheel plugin.

## Independent review

An independent read-only Sol 6.1/high reviewer inspected the complete binding, setup writer, lazy exports, launcher, tests, plugin, documentation, and package verification. It returned **no actionable findings**, then inspected the retained full-suite and packaging results for completion review. The reviewer did not execute native/model calls or mutate files.

| Work | Role | Explicit selection | Effective runtime evidence |
| --- | --- | --- | --- |
| Architecture and scope plan | `planning` | Astra / ultra | Unavailable; unverified |
| Source mapping and implementation | `explorer` / `default` | Sol 6.1 / ultra | Unavailable; unverified |
| Independent implementation/completion review | `default` reviewer | Sol 6.1 / high | Unavailable; unverified |

Standing user authorization covers routine development, bounded scope/verification repairs, and relevant review context. The lazy-export repair was necessary because importing the package otherwise imported runtime environment handling before the setup module ran.

## Remaining limits

Claude Code UI/plugin acceptance and live native execution in a user repository remain unverified; no user repository was configured during this change. Project setup does not grant native admission or change host approvals. Existing Claude local/managed registrations and explicit clink configuration overrides can shadow the generated project configuration; see [usage](../../project-setup.md).

File publication is atomic per file, not across the entire setup. Valid partial files remain after failure for an explicit rerun. Cooperative locking and identity/content rechecks detect concurrent changes, but an unrelated editor can race the final replacement of an existing `.mcp.json`; avoid concurrent editing. Generated manifests/prompts are portable; generated registration paths are machine-specific. Each project server has its own pool and volatile task records.
