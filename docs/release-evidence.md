# Release verification and private provenance

The public source tree keeps synthetic tests, packaging checks, and the exact
catalogs and admission resources used by the runtime. It omits historical test
logs, private workflow state, and the larger evidence archive. Runtime validation
does not read the private archive. A missing or modified packaged admission still
fails closed before a native process starts; Antigravity generation remains
unadmitted.

Run the public checks from the checkout after installing development dependencies:

```sh
.venv/bin/python -m pytest -c pytest.ini tests \
  -m "not integration and not real_cli and not private_evidence"
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m isort --check-only .
```

Pytest reports historical `private_evidence` cases as deselected in this command.
That result establishes the public runtime and synthetic checks; it does not
establish historical provenance. It never silently skips a missing private input.

Maintainers run the historical gate separately. Obtain both `private_archive`
and `private_manifest_sha256` from the independent private release record. The
archive directory is named by that digest. Do not compute the expected digest
from the archive being checked.

```sh
.venv/bin/python scripts/private_evidence.py \
  --root "$private_archive" --sha256 "$private_manifest_sha256"
.venv/bin/python -m pytest -c pytest.ini tests -m private_evidence \
  --private-evidence-root="$private_archive" \
  --private-evidence-sha256="$private_manifest_sha256"
```

This gate refuses missing pins, changed members, writable archives, symlinks,
extra files, and a selection containing no historical tests. The archive contains
only the dependency closure of the retained checks: original catalogs and bounded
admission/lineage evidence, the exact historical verifier source, and the original
source components needed by those proofs. It excludes credentials and raw native
sessions. Existing failed attempts and consumed submission counts remain intact.

Historical proof checks retain their original hashes and relative file topology.
They inspect the frozen original source rather than reinterpreting changed public
tooling as the historically reviewed source. A separate check compares every
released `clink/**/*.py` file (49 files) against a pinned source. Three files
(`clink/backend.py`, `clink/native.py`, and `clink/generation.py`) have release-specific
digests for the approved-directory symlink fix in PR #1. The scoped enrollment fix
adds a fourth pin for `clink/allowed_roots.py`. The other 45 match the frozen
originals byte for byte. Package mirror checks compare the released resources with
the original evidence. Changes to public
verifier tooling and test organization are validated by the current synthetic
suite and independent release review. These checks do not claim that a historical
model executed the new tooling.

The release conductor additionally compares `server.py`, `tools/`, and `utils/`
against the original runtime. Those comparisons are separate from the 49-file
historical test.

The public verifier scripts refuse live submissions. Removing private ledgers
must never create a fresh live-call allowance. Use the preserved original private
verifiers with their complete ledgers, original review authority, and existing
approval process for any continuation. Neither supplying a new archive nor
choosing another output path grants a new submission. The retired generation v1
profile retains its runtime refusal.

For macOS network and home-file isolation, the optional offline harness builds
its Seatbelt policy for the actual checkout and Python environment:

```sh
.venv/bin/python scripts/test_offline.py unique-offline-label
```

It checks isolation before pytest and writes durable, ignored output under
`test_output/offline/`. This harness requires macOS Seatbelt; other platforms need
an equivalent isolated environment. Do not publish its generated local paths or
test logs.

Pinned production resources can contain bounded historical relative locators.
Those bytes are preserved because changing them would invalidate their admission
hashes. A locator is provenance metadata, not a request to load private evidence
at runtime.
