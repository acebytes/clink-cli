"""Selected-Python isolation regressions without launching Seatbelt or model CLIs."""

import argparse
import json
import os
import subprocess
import sys
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import test_offline as offline


@pytest.fixture
def selected_python(tmp_path):
    home = tmp_path / "synthetic home"
    environment = home / "selected environment"
    base = home / "python runtime"
    (environment / "bin").mkdir(parents=True)
    (base / "bin").mkdir(parents=True)
    binary = base / "bin/python"
    binary.write_text("Synthetic interpreter; tests never execute this file.\n")
    binary.chmod(0o700)
    executable = environment / "bin/python"
    executable.symlink_to(binary)
    configuration = environment / "pyvenv.cfg"
    configuration.write_text(f"home = {base / 'bin'}\ninclude-system-site-packages = false\n")
    return SimpleNamespace(
        home=home,
        environment=environment,
        base=base,
        executable=executable,
        configuration=configuration,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )


def metadata_probe(monkeypatch, selected, base):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[:4] == [str(selected.executable.absolute()), "-I", "-S", "-c"]
        assert kwargs == {"env": selected.env, "capture_output": True, "text": True, "timeout": 5, "check": True}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"base_prefix": str(base)}), stderr="")

    monkeypatch.setattr(offline.subprocess, "run", run)
    return calls


def test_selected_venv_and_base_roots_preserve_executable_spelling_and_exclude_harness(selected_python, monkeypatch):
    selected = selected_python
    monkeypatch.setattr(
        offline,
        "sys",
        SimpleNamespace(executable=str(selected.home / "harness/bin/python"), prefix=str(selected.home)),
    )
    calls = metadata_probe(monkeypatch, selected, selected.base)

    roots = offline.interpreter_read_roots(selected.executable, selected.env)

    assert roots == (selected.environment.resolve(), selected.base.resolve())
    assert selected.home not in roots
    assert len(calls) == 1 and calls[0][0][0] != str(selected.executable.resolve())
    assert "sys.base_prefix" in calls[0][0][4]
    assert all(name not in calls[0][0][4] for name in ("sys.path", "sys.prefix", "site", "PYTHONPATH"))


@pytest.mark.skipif(os.name != "posix", reason="The macOS isolation helper uses POSIX Python environments")
def test_metadata_probe_suppresses_real_venv_startup_code(tmp_path):
    home = tmp_path / "synthetic home"
    environment = home / "selected environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    executable = environment / "bin/python"
    clean_env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    location = subprocess.run(
        [str(executable), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        env=clean_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    site_packages = Path(location.stdout.strip())
    assert site_packages.resolve().is_relative_to(environment.resolve())
    site_packages.mkdir(parents=True, exist_ok=True)
    canary = home / "startup-executed"
    (site_packages / "synthetic_startup.pth").write_text(
        f"import pathlib; pathlib.Path({str(canary)!r}).write_text('Synthetic startup executed')\n"
    )
    subprocess.run([str(executable), "-I", "-c", "pass"], env=clean_env, check=True, timeout=5)
    assert canary.exists(), "The control must prove this environment's startup code executes without -S"
    canary.unlink()

    roots = offline.interpreter_read_roots(executable, clean_env)

    assert not canary.exists()
    assert roots == (environment.resolve(), Path(sys.base_prefix).resolve())


@pytest.mark.parametrize("kind", ["home", "home_ancestor", "unrelated_home_runtime", "linked_configuration"])
def test_unsafe_home_grants_and_nonregular_venv_configuration_are_refused(selected_python, monkeypatch, kind):
    selected = selected_python
    base = selected.base
    if kind == "home":
        base = selected.home
    elif kind == "home_ancestor":
        base = selected.home.parent
    elif kind == "unrelated_home_runtime":
        base = selected.home / "unrelated runtime"
        base.mkdir()
    else:
        selected.configuration.unlink()
        unrelated = selected.home / "unrelated configuration"
        unrelated.write_text("Synthetic linked configuration.\n")
        selected.configuration.symlink_to(unrelated)
    metadata_probe(monkeypatch, selected, base)

    with pytest.raises((ValueError, OSError)):
        offline.interpreter_read_roots(selected.executable, selected.env)


def setup_main(monkeypatch, selected, tmp_path):
    args = argparse.Namespace(
        label="synthetic-offline",
        tests=["tests/synthetic.py"],
        python=selected.executable,
        output_dir=tmp_path / "evidence",
    )
    monkeypatch.setenv("HOME", str(selected.home))
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda *_args, **_kwargs: args)
    monkeypatch.setattr(
        offline,
        "sys",
        SimpleNamespace(platform="darwin", executable=str(selected.home / "harness/python"), prefix=str(selected.home)),
    )
    return args


def test_main_uses_selected_policy_and_interpreter_and_excludes_live_cli_tests(selected_python, tmp_path, monkeypatch):
    selected = selected_python
    args = setup_main(monkeypatch, selected, tmp_path)
    roots = (selected.environment.resolve(), selected.base.resolve())
    metadata_calls = []
    process_calls = []

    def read_roots(executable, environment):
        metadata_calls.append((executable, environment))
        return roots

    def run(command, **kwargs):
        process_calls.append((command, kwargs))
        assert command[0] == "/usr/bin/sandbox-exec"
        assert command[3] == str(selected.executable.absolute())
        if "stdout" in kwargs:
            kwargs["stdout"].write("Synthetic pytest success.\n")
        return subprocess.CompletedProcess(command, 0, stdout="Synthetic isolation success.\n", stderr="")

    monkeypatch.setenv("PYTHONPATH", str(selected.home / "unrelated search path"))
    monkeypatch.setattr(offline, "interpreter_read_roots", read_roots)
    monkeypatch.setattr(offline.subprocess, "run", run)
    with pytest.raises(SystemExit) as caught:
        offline.main()

    assert caught.value.code == 0
    assert len(metadata_calls) == 1 and metadata_calls[0][0] == selected.executable
    environment = metadata_calls[0][1]
    assert environment["HOME"] == str(selected.home) and "PYTHONPATH" not in environment
    assert len(process_calls) == 2
    assert all(kwargs["env"] == environment for _, kwargs in process_calls)
    pytest_command = process_calls[1][0]
    selectors = [pytest_command[index + 1] for index, value in enumerate(pytest_command) if value == "-m"]
    assert selectors == ["pytest", "not integration and not real_cli and not private_evidence"]
    policy = (args.output_dir / args.label / "offline.sb").read_text()
    home = json.dumps(str(selected.home))
    assert f"(deny file-read* (subpath {home}))" in policy and "(deny network*)" in policy
    read_grants = next(line for line in policy.splitlines() if line.startswith("(allow file-read* "))
    assert read_grants.count("(subpath ") == 3 and home not in read_grants
    assert all(json.dumps(str(root)) in read_grants for root in roots)


def test_main_refuses_metadata_errors_before_any_sandbox_command(selected_python, tmp_path, monkeypatch, capsys):
    args = setup_main(monkeypatch, selected_python, tmp_path)

    def refuse(*_args):
        raise ValueError("PRIVATE_SYNTHETIC_METADATA")

    monkeypatch.setattr(offline, "interpreter_read_roots", refuse)
    monkeypatch.setattr(
        offline.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Unvalidated metadata reached a sandbox command"),
    )
    with pytest.raises(SystemExit) as caught:
        offline.main()
    assert caught.value.code == 2
    assert "PRIVATE_SYNTHETIC_METADATA" not in capsys.readouterr().err
    assert not (args.output_dir / args.label / "offline.sb").exists()
