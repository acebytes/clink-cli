"""Execute the health probe's own functions, without network or installed CLIs."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("clink_healthcheck", ROOT / "docker/scripts/healthcheck.py")
healthcheck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(healthcheck)


@pytest.mark.parametrize(
    "argv", [b"/usr/local/bin/python\0/usr/local/bin/clink-mcp-server\0", b"python\0/app/server.py\0"]
)
def test_process_recognizes_actual_exec_form_server(tmp_path, argv):
    (tmp_path / "1").mkdir()
    (tmp_path / "1/cmdline").write_bytes(argv)
    assert healthcheck.check_process(tmp_path)


@pytest.mark.parametrize("argv", [b"python\0other-server.py\0", b"sh\0-c\0clink-mcp-server backup\0", b""])
def test_process_rejects_unrelated_or_missing_server(tmp_path, argv):
    (tmp_path / "1").mkdir()
    (tmp_path / "1/cmdline").write_bytes(argv)
    assert not healthcheck.check_process(tmp_path)


def test_process_missing_proc_fails_safely(tmp_path, capsys):
    assert not healthcheck.check_process(tmp_path)
    assert "Server process is unavailable" in capsys.readouterr().err


def test_imports_only_runtime_dependencies(monkeypatch):
    imported = []
    monkeypatch.setattr(healthcheck.importlib, "import_module", lambda name: imported.append(name))
    assert healthcheck.check_python_imports()
    assert imported == ["mcp", "pydantic", "dotenv"]


def test_import_error_fails_without_exposing_exception_content(monkeypatch, capsys):
    monkeypatch.setattr(healthcheck.importlib, "import_module", Mock(side_effect=RuntimeError("private-value")))
    assert not healthcheck.check_python_imports()
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert "private-value" not in output.err


def test_configuration_accepts_missing_cli_and_auth(monkeypatch, tmp_path):
    import clink.registry

    monkeypatch.setattr(clink.registry, "USER_CONFIG_DIR", tmp_path / "new-user-config")
    monkeypatch.setattr(clink.registry, "LEGACY_USER_CONFIG_DIR", tmp_path / "legacy-user-config")
    monkeypatch.delenv("CLI_CLIENTS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert healthcheck.check_configuration()


def test_configuration_failure_is_redacted(monkeypatch, capsys):
    import clink.registry

    monkeypatch.setattr(clink.registry, "ClinkRegistry", Mock(side_effect=ValueError("secret-manifest-value")))
    assert not healthcheck.check_configuration()
    assert "secret-manifest-value" not in capsys.readouterr().err


@pytest.mark.parametrize("passed,expected", [(True, 0), (False, 1)])
def test_main_reports_health_status_without_stdout(monkeypatch, capsys, passed, expected):
    monkeypatch.setattr(healthcheck, "check_process", lambda: passed)
    monkeypatch.setattr(healthcheck, "check_python_imports", lambda: True)
    monkeypatch.setattr(healthcheck, "check_configuration", lambda: True)
    assert healthcheck.main() == expected
    assert capsys.readouterr().out == ""
