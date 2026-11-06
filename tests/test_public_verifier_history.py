"""A public checkout cannot restart live proof accounting without retained private history."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFIERS = (
    "verify_cli_policy",
    "verify_native_cli",
    "verify_native_adapters",
    "verify_generation_policy",
    "verify_native_generation",
    "verify_claude_generation",
    "verify_family_generation",
)


@pytest.mark.parametrize("name", VERIFIERS)
def test_public_live_cli_refuses_before_output_configuration_or_dispatch(name, monkeypatch, capsys):
    module = importlib.import_module("scripts." + name)
    # No output, fixture, executable, or source arguments are supplied: live
    # refusal must happen before any of those fields can drive an operation.
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda *_args, **_kwargs: argparse.Namespace(live=True))
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 2
    diagnostic = capsys.readouterr().err.lower()
    assert "live" in diagnostic and "histor" in diagnostic


@pytest.mark.parametrize("name", VERIFIERS[2:])
@pytest.mark.asyncio
async def test_public_live_verify_api_refuses_before_preflight_or_history_reservation(name, monkeypatch):
    module = importlib.import_module("scripts." + name)
    monkeypatch.setattr(module, "PROJECT", ROOT)
    if name == "verify_claude_generation":
        monkeypatch.setattr(module, "_assert_execution_provenance", lambda: None)
    with pytest.raises(ValueError, match="[Hh]istor"):
        await module.verify(argparse.Namespace(live=True))
