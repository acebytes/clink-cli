"""CLI context is explicit and bounded; directories cannot trigger implicit discovery."""

import pytest

from clink.backend import CLIBackend
from clink.contracts import BackendError, CLIRequest
from tests.cli_helpers import context_target


def test_directory_requires_verified_native_read_capability(tmp_path):
    with pytest.raises(BackendError) as error:
        CLIBackend._context(CLIRequest("Analyze files", context_target(tmp_path), files=(str(tmp_path),)))
    assert error.value.code == "unsupported_capability"


def test_consensus_rejects_directory_even_before_manifest_admission(tmp_path):
    with pytest.raises(BackendError) as error:
        CLIBackend._context(
            CLIRequest("Assess proposal", context_target(tmp_path), files=(str(tmp_path),), tool_name="consensus")
        )
    assert error.value.code == "path_not_allowed"


def test_explicit_files_do_not_discover_neighboring_files(tmp_path):
    declared = tmp_path / "declared.py"
    declared.write_text("value = 1\n")
    (tmp_path / "undeclared.py").write_text("PRIVATE_NEIGHBOR = 2\n")
    request = CLIRequest("Analyze file", context_target(tmp_path), files=(str(declared),))
    files, images = CLIBackend._context(request)
    assert files == (str(declared),)
    assert images == ()
    composed = CLIBackend._compose(request, files, images).decode()
    assert "value = 1" in composed
    assert "PRIVATE_NEIGHBOR" not in composed


def test_symlink_target_cannot_escape_context_roots(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_text("outside context\n")
    link = tmp_path / "link.py"
    link.symlink_to(outside)
    with pytest.raises(BackendError) as error:
        CLIBackend._context(CLIRequest("Analyze file", context_target(tmp_path), files=(str(link),)))
    assert error.value.code == "path_not_allowed"


def test_declared_reference_count_is_bounded(tmp_path):
    with pytest.raises(BackendError) as error:
        CLIBackend._context(CLIRequest("Analyze files", context_target(tmp_path), files=(str(tmp_path),) * 257))
    assert error.value.code == "input_limit"
