# Synthetic native generation v2 golden fixture

These are protocol examples, not admission or execution receipts. The repeated-character recipe and executable hashes are synthetic. The v1 fixtures remain separate and unchanged.

The request adds explicit native mode and the canonical macOS directory `/private/tmp`. Its instruction and input strings exactly match the v1 fixture, including CRLF, whitespace, braces, backslashes, accented text and emoji; timeout 120.125 normalizes to 120125 milliseconds. `canonical-request.json` has no trailing newline. Tests can validate its bytes without requiring the example directory to exist on another platform.

The result records possible side effects after a native launch, limited permission observability, and no fulfillment verification. The actual model and catalog hash remain null. Before request validation, `execution.project_dir` is null and `side_effects_possible` defaults to false.
