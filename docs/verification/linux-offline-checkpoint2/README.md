# Linux offline unit checkpoint 2

Actual Linux arm64 Python 3.12 container execution reproducing the manual CI unit job: **946 passed, 16 subtests passed**, exit 0. This follows the retained four-failure checkpoint 1 and the test-migration owner's fixture repairs, including explicit admitted temporary roots and two additional prompt-file admission cases.

The full suite used a read-only source mount/root filesystem, `--network none`, a synthetic HOME, no CLI executables or credential mounts, dependency installation from prepared local wheels, and only this evidence directory writable for JUnit. Preflight verified external networking unavailable and no native CLI/auth environment. `result.json` records the exact command and timing; `source-sha256.json` pins the source files and reports no changes during this run. `pytest.txt` and `junit.xml` preserve complete test results.

This checkpoint tests Linux Python 3.12, not Python 3.10/3.11 or native CLI authentication. It precedes the pending Codex model-resource amendment and final package/image rebuild, so final acceptance still needs those follow-up checks.
