"""Generic resolution preserves opaque identifiers independently of pinned adapter catalogs.

The synthetic resolve-only fixture has no admitted catalog and cannot launch.
Production Codex exact-ID/catalog/default guards are covered in test_clink_backend.
"""

import sys
from types import SimpleNamespace

import pytest

from clink.backend import CLIBackend
from clink.contracts import AdapterPolicy


@pytest.fixture
def selection_backend(tmp_path, monkeypatch):
    role = SimpleNamespace(
        name="default", prompt_text="Advice only", role_args=(), model="role-native", description=None
    )
    client = SimpleNamespace(
        name="codex",
        adapter="codex",
        runner="codex",
        executable=(sys.executable,),
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        config_args=(),
        env={},
        timeout_seconds=30,
        parser="codex_jsonl",
        default_model="client-native",
        get_role=lambda name: role,
    )
    monkeypatch.setenv("DEFAULT_CLI", "codex")
    registry = SimpleNamespace(get_client=lambda name: client)
    backend = CLIBackend(registry)
    # A Python executable is a transport double, not the admitted Codex binary.
    monkeypatch.setattr(
        backend,
        "_policy",
        lambda adapter: AdapterPolicy(adapter, verification_status="test_fixture_no_catalog"),
    )
    return backend


@pytest.mark.parametrize(
    "model",
    [
        "vendor/model:free",
        "vendor/model:beta",
        "vendor/model:preview",
        "native:latest",
        "native:for",
        "native:against",
        "native:neutral",
        "native:unknown-suffix",
        "native-exact",
        "unknown-native-model",
    ],
)
def test_uncatalogued_identifiers_are_not_aliases_or_stance_options(selection_backend, model):
    target = selection_backend.resolve(model=model)
    assert target.requested_model == model
    assert target.effective_model == model


@pytest.mark.parametrize("model", [None, "auto"])
def test_native_default_request_uses_role_default(selection_backend, model):
    target = selection_backend.resolve(model=model)
    assert target.requested_model is None
    assert target.effective_model == "role-native"


def test_client_default_applies_without_role_model(selection_backend):
    selection_backend.registry.get_client("codex").get_role("default").model = None
    assert selection_backend.resolve().effective_model == "client-native"


def test_native_default_remains_unknown_without_configured_model(selection_backend):
    client = selection_backend.registry.get_client("codex")
    client.default_model = None
    client.get_role("default").model = None
    assert selection_backend.resolve().effective_model is None


def test_explicit_native_model_overrides_role_and_client_defaults(selection_backend):
    assert selection_backend.resolve(model="request-native").effective_model == "request-native"
