# Clink-only fork: baseline evidence

Recorded **2026-10-31**, America/Los_Angeles. Confidence: **high for the recorded isolated unit results**; real CLI/authentication/provider integration is unverified.

This report establishes the pre-implementation baseline. Product code, test files, `requirements.txt`, and `pyproject.toml` remain at PAL commit `7afc7c1cc96e23992c8f105f960132c657883bb1`. The MCP constraint below changes only the audit virtual environment. See the [audit](clink-only-audit.md) and [proposed design](clink-only-design.md).

## Provenance and scope

- Checkout: `/Users/d/Tools/clink-mcp-server`; working branch: `dex/clink-only-design`.
- GitHub writing identity was verified as `acebytes` with `gh api user --jq .login`.
- `gh repo fork BeehiveInnovations/pal-mcp-server --fork-name clink-mcp-server --clone=false` reported the existing `acebytes/zen-mcp-server` fork and renamed/reused it as [acebytes/clink-mcp-server](https://github.com/acebytes/clink-mcp-server). It did not create an unrelated second fork. Both the old redirected URL and new URL subsequently resolved to the requested fork with the correct PAL parent.
- The clone's original `main`, the working branch's starting commit, and the supplied PAL HEAD all matched the pinned commit. No existing remote branch was rewritten.
- `origin` points to the personal fork. `upstream` retains its fetch URL but has push URL `DISABLED`; `remote.pushDefault` and the GitHub CLI default repository point to the personal fork.
- The original `/Users/d/Tools/pal-mcp-server` remains separate. Its four provider-catalog modifications and two dependency-manifest modifications were not copied. The actual source-level MCP cap is deferred until design approval.
- The test environment is a new `.venv`, created with Python 3.12.15. It does not reuse PAL's installed site packages or copy `.env`, credentials, or CLI login state.
- Apache 2.0 `LICENSE`, upstream source notices, and the 2025 Beehive Innovations attribution are unchanged.

## Results, including unsuccessful attempts

| Attempt | Result | Passed | Failed | Skipped | Deselected | Collection errors |
|---|---|---:|---:|---:|---:|---:|
| Original PAL environment, planning inspection | Missing `pytest`; nothing collected | — | — | — | — | — |
| New clone, pristine requirements / MCP 2.3.0 | Collection interrupted; exit 2 | Not executed | Not executed | Not executed | 16 | 7 |
| Installation-only MCP <2 / MCP 1.30.0, HOME omitted | Isolation harness compatibility failure; exit 1 | 865 | 1 | 4 | 16 | 0 |
| **MCP 1.30.0, original HOME preserved** | **Authoritative compatibility-adjusted baseline; exit 0** | **866** | **0** | **4** | **16** | **0** |

The final run collected **886 items**, selected **870**, and also reported **19 passed subtests** and **2 runtime warnings**. Pytest reported **7.84 seconds**; the runner's total subprocess duration was **8.661 seconds**. JUnit's `tests=889` includes the 19 subtests; it must not be presented as 889 independent pytest items.

The pristine run collected 830 items before its seven module-collection errors. Every error came from `server.py:630`, where MCP 2.3.0's `Server` lacks `list_tools`. Affected modules were conversation field mapping, conversation memory, disabled tools, MCP error handling, model metadata continuation, model option parsing, and server tests. This validates the need for the proposed MCP compatibility cap; it is not a tool-implementation regression.

The first MCP 1.x run failed only `TestPipDetectionFix.test_setup_env_file_does_not_create_bsd_backup`: sourcing `run-server.sh` failed with `HOME: unbound variable`. The harness, rather than product code, was corrected to retain the existing HOME value. The OS sandbox continued denying credential-file reads and all networking. The failed run and its exact environment-key list remain in the evidence directory.

The four skipped cases are all in `tests/test_provider_utf8.py`:

- `test_gemini_provider_handles_api_encoding_error`: requires real Gemini API access.
- `test_gemini_provider_utf8_request`: requires real Gemini API access.
- `test_openai_compatible_o3_pro_utf8`: requires real OpenAI API access.
- `test_openai_provider_utf8_logging`: requires real OpenAI API access.

The final runtime warnings concern unawaited `AsyncMock` coroutines in a consensus context test and an analyze UTF-8 workflow test. Pytest-asyncio also emits a startup deprecation notice because `asyncio_default_fixture_loop_scope` is unspecified. These were recorded without source repairs.

## Isolation and its limits

The [runner](baseline/2026-10-31/run_baseline.py) constructs an allowlisted environment: PATH contains only the new `.venv`, `/usr/bin`, and `/bin`; other entries are locale, Python isolation, plugin loading, and PAL environment-precedence settings. The successful final run retains HOME's existing value, without redirecting it or copying its contents. No API keys, provider endpoints, user Python paths, or CLI session variables are inherited.

The [macOS Seatbelt profile](baseline/2026-10-31/offline.sb) denies all network operations, user-directory file contents outside the clone, and user-directory writes outside the clone. Directory metadata and the two necessary ancestor directories remain readable for Python path resolution. System files and disposable temporary directories are available. Tests can write ignored logs/caches and temporary fixtures.

Before every actual pytest run, the sandboxed child proved that:

1. A connection to numeric loopback address `127.0.0.1:9` failed with EPERM/EACCES, rather than merely encountering a closed port.
2. Reading the original PAL checkout's non-sensitive README failed with `PermissionError`.
3. The target clone's source remained readable.

The original sandbox probe could not be nested inside the tool sandbox (`sandbox_apply: Operation not permitted`). Baseline launch therefore used the approved host execution route, immediately entering the explicit Seatbelt profile. A first profile denied Python's path resolution too broadly; its failed preflight is preserved as `pristine-isolation-attempt1.txt`. The narrowed ancestor/metadata exceptions passed all checks before tests ran.

Plugin autoload is disabled; `pytest_asyncio.plugin` and `pytest_mock` are loaded explicitly. This avoids unrelated installed plugins. The normal `not integration` selector alone is insufficient isolation: some unmarked tests exercise provider code with dummy keys. OS networking denial is the enforcement layer. This baseline is deliberately not evidence of external provider connectivity, CLI login, real CLI execution, or advisory-policy enforcement.

## Resolved environment

| Component | Pristine | Compatibility-adjusted |
|---|---|---|
| Python | 3.12.15, macOS | Same |
| MCP | 2.3.0 | 1.30.0 |
| pytest / pytest-asyncio / pytest-mock | 9.2.0 / 1.4.1 / 3.17.0 | Same |
| google-genai / openai | 2.34.0 / 3.42.0 | Same |
| Pydantic / python-dotenv | 2.14.2 / 1.2.4 | Same |
| httpx | 0.28.1 | Same |

The constrained install additionally introduced `httpx-sse==0.4.3` and `pydantic-settings==2.16.2`. It reused the new baseline environment, so already-installed packages such as `mcp-types==2.3.0` remain recorded; this is not a separately minimized clean-install environment. Full exact package snapshots are retained for both runs. No language-version or cross-platform compatibility beyond this interpreter/platform was tested.

## Reproduction

Run from the clone. The Seatbelt profile contains this checkout's absolute path and is specific to this macOS audit; adjust a copied profile for a different checkout before reproducing. The commands below install packages from public PyPI, then block networking during tests. `sandbox-exec` must be launchable from the host; an outer sandbox may prevent nesting.

```sh
cd /Users/d/Tools/clink-mcp-server
python3.12 -m venv .venv
env -i PATH=/usr/bin:/bin .venv/bin/python -m pip --isolated install \
  --disable-pip-version-check --index-url https://pypi.org/simple \
  -r requirements.txt -r requirements-dev.txt
.venv/bin/python docs/baseline/2026-10-31/run_baseline.py pristine
```

The pristine test command is expected to fail collection with the recorded MCP 2.3.0 environment. Then apply the installation-only constraint:

```sh
env -i PATH=/usr/bin:/bin .venv/bin/python -m pip --isolated install \
  --disable-pip-version-check --index-url https://pypi.org/simple \
  -r requirements.txt -r requirements-dev.txt \
  -c docs/baseline/2026-10-31/mcp1-constraints.txt
.venv/bin/python docs/baseline/2026-10-31/run_baseline.py mcp1-home
```

The runner executes the existing suite equivalent to:

```sh
.venv/bin/python -m pytest tests/ -v -m 'not integration' \
  -p pytest_asyncio.plugin -p pytest_mock --junitxml=<evidence-file>
```

Dependency ranges may resolve differently later. For the recorded package set, use the corresponding `*-packages.txt` as a pip freeze manifest in a disposable environment. Preserve this evidence before rerunning the same label: the runner writes that label's output files.

## Evidence index

All evidence is durable under [`docs/baseline/2026-10-31/`](baseline/2026-10-31/):

- [`pristine-install.txt`](baseline/2026-10-31/pristine-install.txt), [`pristine-packages.txt`](baseline/2026-10-31/pristine-packages.txt), [`pristine-pytest.txt`](baseline/2026-10-31/pristine-pytest.txt), [`pristine-run.json`](baseline/2026-10-31/pristine-run.json), and [`pristine-junit.xml`](baseline/2026-10-31/pristine-junit.xml).
- [`mcp1-install.txt`](baseline/2026-10-31/mcp1-install.txt), [`mcp1-pytest.txt`](baseline/2026-10-31/mcp1-pytest.txt), and [`mcp1-run.json`](baseline/2026-10-31/mcp1-run.json) preserve the first constrained attempt and harness failure.
- [`mcp1-home-packages.txt`](baseline/2026-10-31/mcp1-home-packages.txt), [`mcp1-home-pytest.txt`](baseline/2026-10-31/mcp1-home-pytest.txt), [`mcp1-home-run.json`](baseline/2026-10-31/mcp1-home-run.json), and [`mcp1-home-junit.xml`](baseline/2026-10-31/mcp1-home-junit.xml) are the final baseline.
- `*-isolation.txt` preserve the successful enforcement probes; `pristine-isolation-attempt1.txt` preserves the initial profile failure.

Future implementation results must be compared with this baseline by retained behavior as well as raw totals. Separately account for removed provider-only tests, migrated tests, added CLI tests, changed skips, and newly verified integrations. Do not describe intentionally deleted tests as newly passing tests.

**Phase 3 product implementation has not started. Approval of the design remains required.**
