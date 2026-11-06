"""
Provider-free pytest configuration for clink-mcp-server tests
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

# On macOS, the default pytest temp dir is typically under /var (e.g. /private/var/folders/...).
# If /var is considered a dangerous system path, tests must use a safe temp root (like /tmp).
if sys.platform == "darwin":
    os.environ["TMPDIR"] = "/tmp"
    # tempfile caches the temp dir after first lookup; clear it so pytest fixtures pick up TMPDIR.
    tempfile.tempdir = None

# Ensure the parent directory is in the Python path for imports
parent_dir = Path(__file__).resolve().parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

import utils.env as env_config  # noqa: E402

# Ensure tests operate with runtime environment rather than .env overrides during imports
env_config.reload_env({"PAL_MCP_FORCE_ENV_OVERRIDE": "false"})

# Note: This creates a test sandbox environment
# Tests create their own temporary directories as needed

# Configure asyncio for Windows compatibility
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def project_path(tmp_path):
    """
    Provides a temporary directory for tests.
    This ensures all file operations during tests are isolated.
    """
    # Create a subdirectory for this specific test
    test_dir = tmp_path / "test_workspace"
    test_dir.mkdir(parents=True, exist_ok=True)

    return test_dir


def pytest_configure(config):
    """Register the shared async marker."""
    config.addinivalue_line("markers", "asyncio: mark test as async")


def pytest_addoption(parser):
    group = parser.getgroup("private evidence")
    group.addoption("--private-evidence-root", type=Path, help="External immutable provenance archive")
    group.addoption("--private-evidence-sha256", help="Independently recorded archive manifest SHA-256")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """Make historical provenance a separate, explicit gate with no missing-input skips."""
    root = config.getoption("--private-evidence-root")
    checksum = config.getoption("--private-evidence-sha256")
    requested = config.option.markexpr.strip() == "private_evidence"
    if bool(root) != bool(checksum) or (requested and not root):
        raise pytest.UsageError("Private evidence requires both --private-evidence-root and --private-evidence-sha256")
    if root:
        from scripts.private_evidence import verify_archive

        try:
            config._private_evidence_root = verify_archive(root, checksum)
        except (OSError, ValueError) as error:
            raise pytest.UsageError("Private evidence archive refused: " + str(error)) from error
        if not any(item.get_closest_marker("private_evidence") for item in items):
            raise pytest.UsageError("Private evidence gate selected zero historical checks")
        return
    historical = [item for item in items if item.get_closest_marker("private_evidence")]
    items[:] = [item for item in items if not item.get_closest_marker("private_evidence")]
    if historical:
        config.hook.pytest_deselected(items=historical)


@pytest.fixture(scope="session")
def private_evidence_root(pytestconfig):
    root = getattr(pytestconfig, "_private_evidence_root", None)
    if root is None:
        raise pytest.UsageError("Historical provenance needs an explicitly pinned private evidence archive")
    return root


@pytest.fixture(autouse=True)
def disable_force_env_override(monkeypatch):
    """Default tests to runtime environment visibility unless they explicitly opt in."""

    monkeypatch.setenv("PAL_MCP_FORCE_ENV_OVERRIDE", "false")
    env_config.reload_env({"PAL_MCP_FORCE_ENV_OVERRIDE": "false"})
    monkeypatch.setenv("MAX_CONVERSATION_TURNS", "50")

    import importlib
    import sys

    import config
    import utils.conversation_memory as conversation_memory

    importlib.reload(config)
    importlib.reload(conversation_memory)

    test_conversation_module = sys.modules.get("tests.test_conversation_memory")
    if test_conversation_module is not None:
        test_conversation_module.MAX_CONVERSATION_TURNS = conversation_memory.MAX_CONVERSATION_TURNS

    try:
        yield
    finally:
        env_config.reload_env()


@pytest.fixture
def cli_backend(monkeypatch, tmp_path):
    """Opt in to a recording backend; generic tests never install automatic mocks."""
    from tests.cli_helpers import RecordingBackend

    backend = RecordingBackend(tmp_path)
    monkeypatch.setattr("clink.backend.get_backend", lambda: backend)
    return backend
