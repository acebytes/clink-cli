"""Verify metadata-only exception observations without live native CLI calls."""

import asyncio
import errno
import json
import sys
import time
from pathlib import Path

import pytest

from clink.contracts import BackendError
from scripts import native_diagnostics as diagnostics
from scripts import verify_native_adapters as verifier


def test_trusted_location_never_exports_message_source_locals_or_absolute_path():
    filename = str(Path(diagnostics.__file__).resolve().parents[1] / "clink" / "runner.py")
    namespace = {}
    exec(
        compile(
            "def _cleanup():\n    private = 'secret local'\n    raise PermissionError(1, 'secret message', '/secret/argv')\n",
            filename,
            "exec",
        ),
        namespace,
    )
    try:
        namespace["_cleanup"]()
    except PermissionError as exc:
        result = diagnostics.exception_diagnostic(exc)
    assert result == {
        "exception_class": "PermissionError",
        "exception_location": {"file": "clink/runner.py", "function": "_cleanup", "line": 3},
        "os_error_number": 1,
    }
    for private in ("secret", str(Path(diagnostics.__file__).resolve().parents[1]), "raise ", "argv"):
        assert private not in json.dumps(result)


def test_untrusted_exception_type_and_traceback_names_are_not_exported():
    secret_type = type("SecretCredentialValue", (Exception,), {})
    namespace = {"secret_type": secret_type}
    exec(
        compile(
            "def private_function():\n    raise secret_type('private message')\n", "/private/secret/path.py", "exec"
        ),
        namespace,
    )
    try:
        namespace["private_function"]()
    except Exception as exc:
        result = diagnostics.exception_diagnostic(exc)
    assert result == {"exception_class": "unknown"}


@pytest.mark.parametrize(
    "error,name",
    [
        (RuntimeError("private"), "RuntimeError"),
        (asyncio.InvalidStateError("private"), "InvalidStateError"),
        (BackendError("private", "private"), "BackendError"),
    ],
)
def test_known_exception_class_names_are_fixed_and_no_message_is_observed(error, name):
    assert diagnostics.exception_diagnostic(error) == {"exception_class": name}


def technical_diagnostic():
    return {
        "exception_class": "PermissionError",
        "exception_location": {"file": "clink/runner.py", "function": "_signal_group", "line": 121},
        "os_error_number": 1,
    }


def prior_receipt(tmp_path, monkeypatch, entry):
    monkeypatch.setattr(verifier, "PROJECT", tmp_path)
    directory = tmp_path / "docs" / "verification"
    directory.mkdir(parents=True)
    path = directory / "prior.json"
    path.write_text(
        json.dumps(
            {
                "scope": verifier.SCOPE,
                "adapter": "grok",
                "submission_ledger": [{"ordinal": 1, "check": "cancel", **entry}],
                "submission_allowance_total": 8,
                "submissions_consumed_total": 1,
            }
        )
    )
    return path


def test_sanitized_diagnostics_survive_prior_lineage_without_resetting_consumption(tmp_path, monkeypatch):
    diagnostic = technical_diagnostic()
    entry = {
        "reason": "unexpected_exception",
        **diagnostic,
        "cleanup_exception": diagnostic,
        "cleanup_observation_exception": {"exception_class": "ProcessLookupError", "os_error_number": 3},
        "process_group_absent_after_cleanup": True,
    }
    path = prior_receipt(tmp_path, monkeypatch, entry)
    before = path.read_bytes()
    ledger, allowance, previous_hash = verifier.load_history(path, "grok")
    assert ledger == [{"ordinal": 1, "check": "cancel", **entry}]
    assert allowance == 8
    assert previous_hash == verifier.digest(before)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "entry",
    [
        {"reason": None},
        {"reason": "cancelled"},
        {"reason": "backend_error", "error_code": "timeout"},
        {"reason": "exited", "exit_status": 0},
    ],
)
def test_prior_receipts_without_diagnostics_keep_original_contract(tmp_path, monkeypatch, entry):
    path = prior_receipt(tmp_path, monkeypatch, entry)
    assert verifier.load_history(path, "grok")[0] == [{"ordinal": 1, "check": "cancel", **entry}]


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        None,
        [],
        {"exception_class": "private_message"},
        {"exception_class": []},
        {"exception_class": "OSError", "message": "private_message"},
        {"exception_class": "OSError", "os_error_number": True},
        {"exception_class": "OSError", "os_error_number": -1},
        {"exception_class": "OSError", "os_error_number": 4096},
        {
            "exception_class": "OSError",
            "exception_location": {"file": "/private/path", "function": "_cleanup", "line": 1},
        },
        {
            "exception_class": "OSError",
            "exception_location": {"file": "clink/runner.py", "function": "private_message", "line": 1},
        },
        {"exception_class": "OSError", "exception_location": {"file": [], "function": "_cleanup", "line": 1}},
        {
            "exception_class": "OSError",
            "exception_location": {"file": "clink/runner.py", "function": "_cleanup", "line": True},
        },
        {
            "exception_class": "OSError",
            "exception_location": {"file": "clink/runner.py", "function": "_cleanup", "line": 0},
        },
        {
            "exception_class": "OSError",
            "exception_location": {"file": "clink/runner.py", "function": "_cleanup", "line": 10**7 + 1},
        },
    ],
)
def test_exception_schema_rejects_arbitrary_or_malformed_fields(invalid):
    assert diagnostics.valid_exception_diagnostic(invalid) is False


@pytest.mark.parametrize(
    "entry",
    [
        {"reason": "unexpected_exception"},
        {"reason": "unexpected_exception", "exception_class": "private_message"},
        {"reason": "unexpected_exception", "exception_class": "RuntimeError", "os_error_number": -1},
        {"reason": "cancelled", "cleanup_exception": {"exception_class": "RuntimeError", "message": "private_message"}},
        {"reason": "cancelled", "cleanup_observation_exception": {"exception_class": []}},
    ],
)
def test_prior_lineage_rejects_private_or_incomplete_diagnostics(tmp_path, monkeypatch, entry):
    path = prior_receipt(tmp_path, monkeypatch, entry)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="technical .*diagnostic") as caught:
        verifier.load_history(path, "grok")
    assert "private_message" not in str(caught.value)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_verifier_records_unexpected_cleanup_failure_without_changing_core_cleanup(tmp_path, monkeypatch):
    evidence = {"adapter": "grok", "submission_ledger": [], "submission_allowance_total": 4}
    path = tmp_path / "technical-receipt.json"
    pool = verifier.VerificationPool(evidence, path)
    cleanup = verifier.ProcessPool._cleanup

    async def fail_after_cleanup(self, process):
        await cleanup(self, process)
        raise OSError(errno.EIO, "private cleanup diagnostic", "/private/secret/path")

    monkeypatch.setattr(verifier.ProcessPool, "_cleanup", fail_after_cleanup)
    with pytest.raises(OSError):
        await pool.execute(
            (sys.executable, "-c", "pass"),
            content=b"",
            cwd=str(tmp_path),
            env={},
            deadline=time.monotonic() + 3,
            request_id="synthetic-diagnostic",
        )
    entry = evidence["submission_ledger"][-1]
    assert entry["reason"] == "unexpected_exception"
    assert entry["exception_class"] == "OSError"
    assert entry["os_error_number"] == errno.EIO
    assert entry["cleanup_exception"]["exception_class"] == "OSError"
    assert entry["process_group_absent_after_cleanup"] is True
    assert pool.running == 0 and not pool._children
    serialized = path.read_text()
    for private in ("private cleanup", "/private/secret", "sys.executable", "argv", "stderr"):
        assert private not in serialized
