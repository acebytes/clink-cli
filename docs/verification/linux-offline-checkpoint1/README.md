# Linux offline unit checkpoint 1

Actual Linux arm64 Python 3.12 container execution reproducing the manual CI unit job: **940 passed, 4 failed, 16 subtests passed**. This is a failed checkpoint, retained for repair and comparison.

Dependency wheels were downloaded during a separate preparation step. The test container then used `--network none`, read-only source/root filesystems, an executable temporary filesystem for test executables, a synthetic HOME and no native CLI or authentication mounts. Only this evidence directory was separately mounted writable to collect JUnit. Preflight proved no external route, no CLI binaries on PATH and no API-key/token environment names. The source hash snapshot did not change during the run. Exact command/status/timing are in `result.json`; failures are in `pytest.txt` and `junit.xml`.

The four failures identify test fixtures that need to follow the current contracts: two prompt-file cases use an incomplete recording target; the prompt-file byte-limit case supplies no admitted temporary root; and one metadata test expects the older error-status spelling. They have been routed to the test-migration owner. This checkpoint does not establish platform compatibility for Python 3.10 or 3.11, and includes no real CLI agent calls.
