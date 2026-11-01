# Offline project setup package check

Run from the checkout with the managed development dependencies installed:

```sh
.venv/bin/python docs/verification/project-setup-packaging-1/verify.py
```

This evidence directory and `test_output/project-setup-packaging-1` belong to this attempt; the verifier refuses an existing scratch directory. Preserve this attempt if a later run needs another directory.

The [summary](summary.json) records passing wheel/sdist resource validation, the source-distribution plugin files, no-index installation, installed setup idempotence, and two real MCP stdio servers using generated project registrations. The [commands](commands.json) and individual text logs retain commands, results, and build/install output. Each server discovered 5 clients and 18 tools with an empty native executable PATH and synthetic home.

All build/install/smoke subprocesses used the existing macOS offline sandbox profile, which denies networking and unrelated home access. Managed build/runtime dependencies were reused; no fresh dependency resolution, installed native CLI invocation, authentication probe, or model call occurred. Artifacts remain in the ignored `test_output/project-setup-packaging-1/dist` directory; their hashes are in the summary. Nothing was published.

This verifies installed setup and server transport, not Claude Code's interactive plugin UI or live native task admission. It also does not test standalone marketplace installation; the plugin deliberately expects a prepared local checkout.
