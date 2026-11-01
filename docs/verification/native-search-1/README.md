# Actual API-lookup capability probe

One authorized call passed through the actual `apilookup` MCP handler, shared backend, bounded runner, installed Codex 0.166.0 and strict production parser. It requested `gpt-6.1-sol`; actual provider-reported model identity remained unknown. The call returned `search_performed=true` and 20 structured search-result URLs, including official MCP documentation. No raw response, prompt stream, credentials or stderr is retained here.

Production search admission was still disabled. To break that verification dependency, the probe enabled only `native_search` on the independently reviewed policy snapshot inside its own short-lived process. Binary, recipe, catalog hashes and every mandatory execution control remained unchanged. This did not change server configuration or production admission. The backend was closed and the original in-process policy restored afterward.

`result.json` records the native-call parser hashes and outcome. `probe.py` is the exact audit copy of the script executed from `test_output/probe_lookup.py --live`; its fixed workspace/evidence paths are historical, not a supported reusable command. Reproduction after admission uses `scripts/smoke_cli.py --live --label <new-label> --tool apilookup`.

Native stdout was not retained. Later parser hardening is verified against separately identified sanitized/synthetic fixtures; it must not be described as replaying this call's unretained raw stream. The failed prior search attempts remain in the CLI-policy evidence.
