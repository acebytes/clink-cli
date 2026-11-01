"""Describe unexpected verification failures without exception text or raw tracebacks."""

from __future__ import annotations

import asyncio
import builtins
from pathlib import Path
from typing import Any

from clink.contracts import BackendError

_PROJECT = Path(__file__).resolve().parents[1]
_FILES = {
    str(_PROJECT / name): name
    for name in (
        "clink/runner.py",
        "clink/native.py",
        "clink/native_adapters.py",
        "clink/tasks.py",
        "scripts/verify_native_adapters.py",
    )
}
_ASYNCIO_ROOT = Path(asyncio.__file__).resolve().parent
_FILES.update(
    {
        str(_ASYNCIO_ROOT / name): "asyncio/" + name
        for name in ("base_subprocess.py", "streams.py", "unix_events.py", "tasks.py", "futures.py")
    }
)
_FILES.update({str(_ASYNCIO_ROOT.parent / name): name for name in ("tempfile.py", "shutil.py")})
_FUNCTIONS = frozenset(
    {
        "execute",
        "_execute",
        "_cleanup",
        "_signal_group",
        "finalize",
        "_write",
        "_read",
        "run",
        "prelaunch",
        "prepare_native_launch",
        "persist",
        "started",
        "_finish_cleanup",
        "cancel",
        "_cancel_job",
        "wait",
        "_wait",
        "_try_finish",
        "_call_connection_lost",
        "_wait_for_data",
        "drain",
        "close",
        "_drain_helper",
        "cleanup",
        "_rmtree",
        "rmtree",
        "_rmtree_safe_fd",
        "_rmtree_unsafe",
        "_execute_child",
    }
)
_CLASSES = {
    value: name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException) and name == value.__name__
}
_CLASSES.update(
    {
        asyncio.CancelledError: "CancelledError",
        asyncio.InvalidStateError: "InvalidStateError",
        BackendError: "BackendError",
    }
)


def exception_diagnostic(error: BaseException) -> dict[str, Any]:
    """Return fixed class names and the innermost trusted relative function/line only."""
    result: dict[str, Any] = {"exception_class": _CLASSES.get(type(error), "unknown")}
    traceback = error.__traceback__
    for _ in range(128):
        if traceback is None:
            break
        code = traceback.tb_frame.f_code
        filename = _FILES.get(code.co_filename)
        if filename is not None and type(traceback.tb_lineno) is int and 0 < traceback.tb_lineno <= 10**7:
            result["exception_location"] = {
                "file": filename,
                "function": code.co_name if code.co_name in _FUNCTIONS else "unknown",
                "line": traceback.tb_lineno,
            }
        traceback = traceback.tb_next
    if isinstance(error, OSError) and type(error.errno) is int and 0 <= error.errno <= 4095:
        result["os_error_number"] = error.errno
    return result


def valid_exception_diagnostic(value: object) -> bool:
    """Accept only the same fixed technical schema when retaining prior receipts."""
    if (
        not isinstance(value, dict)
        or not value
        or set(value) - {"exception_class", "exception_location", "os_error_number"}
    ):
        return False
    if not isinstance(value.get("exception_class"), str) or value["exception_class"] not in {
        *_CLASSES.values(),
        "unknown",
    }:
        return False
    if "os_error_number" in value and (
        type(value["os_error_number"]) is not int or not 0 <= value["os_error_number"] <= 4095
    ):
        return False
    if "exception_location" in value:
        location = value["exception_location"]
        if not isinstance(location, dict) or set(location) != {"file", "function", "line"}:
            return False
        if (
            not isinstance(location["file"], str)
            or not isinstance(location["function"], str)
            or location["file"] not in set(_FILES.values())
            or location["function"] not in _FUNCTIONS | {"unknown"}
        ):
            return False
        if type(location["line"]) is not int or not 0 < location["line"] <= 10**7:
            return False
    return True
