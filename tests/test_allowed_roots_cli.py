"""CLI-owned consent, unchanged generation requests and cancellation without live CLIs."""

import argparse
import asyncio
import io
import json
import os
import shlex
import signal
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import clink.cli as cli
import clink.registry as registry_module
import clink.setup as project_setup
from clink.allowed_roots import EnrollmentError, allow_command, prepare_enrollment
from clink.contracts import BackendError
from clink.native_generation_contracts import terminal_native_result
from clink.registry import ClinkRegistry
from clink.runner import ProcessOutput
from tests.test_allowed_roots import roots as roots
from tests.test_native_generation_service import events
from tests.test_native_generation_service import native_factory as native_factory


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def terminal_input(monkeypatch):
    opened = []
    stderr = Terminal()
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    monkeypatch.setattr(cli.sys, "stdin", Terminal())
    monkeypatch.delenv("CI", raising=False)
    real_open = os.open
    monkeypatch.setattr(cli.os, "tcgetpgrp", lambda descriptor: os.getpgrp())

    def provide(data, *, eof=True):
        monkeypatch.setattr(cli.sys, "stderr", stderr)
        monkeypatch.setattr(cli.sys, "stdin", Terminal())
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        if data:
            os.write(write_fd, data)
        if eof:
            os.close(write_fd)

        def open_terminal(path, *args, **kwargs):
            if path == "/dev/tty":
                descriptor = os.dup(read_fd)
                opened.append(descriptor)
                return descriptor
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(cli.os, "open", open_terminal)
        return read_fd, None if eof else write_fd

    yield provide, stderr, opened
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    "answer,approved",
    [
        (b"y\n", True),
        (b" YeS \n", True),
        (b"\n", False),
        (b"no\n", False),
        (b"sure\n", False),
        (b"", False),
        (b"yes", False),
    ],
)
async def test_single_default_no_terminal_question(roots, terminal_input, monkeypatch, answer, approved):
    _, target, _ = roots
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    provide, stderr, _ = terminal_input
    reader, _ = provide(answer)
    monkeypatch.setattr(
        asyncio.get_running_loop(), "add_reader", lambda *args: pytest.fail("TTY selector registration")
    )
    try:
        assert await cli.confirm_enrollment(plan) is approved
    finally:
        os.close(reader)
    output = stderr.getvalue()
    assert output.count("[y/N]") == 1
    assert "from now on" in output and plan.entry in output
    assert not registry_module.USER_CONFIG_DIR.exists()


@pytest.mark.parametrize("reason", ["stderr", "stdin", "ci", "background", "missing-tty"])
async def test_unavailable_terminal_fails_promptly_with_preapproval_command(roots, terminal_input, monkeypatch, reason):
    _, target, _ = roots
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    provide, stderr, _ = terminal_input
    reader, writer = provide(b"", eof=False)
    if reason == "stderr":
        monkeypatch.setattr(cli.sys, "stderr", io.StringIO())
    elif reason == "stdin":
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    elif reason == "ci":
        monkeypatch.setenv("CI", "true")
    elif reason == "background":
        monkeypatch.setattr(cli.os, "tcgetpgrp", lambda descriptor: -1)
    else:

        def unavailable(*args, **kwargs):
            raise OSError

        monkeypatch.setattr(cli.os, "open", unavailable)
    try:
        with pytest.raises(EnrollmentError, match=r"clink allow .* --cli codex --yes"):
            await asyncio.wait_for(cli.confirm_enrollment(plan, standalone=True), 0.5)
    finally:
        os.close(reader)
        os.close(writer)
    assert "[y/N]" not in stderr.getvalue()
    assert not registry_module.USER_CONFIG_DIR.exists()


async def test_breadth_warning_and_yes_bypass_question(roots, capsys):
    home, _, _ = roots
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(home))
    assert await cli.confirm_enrollment(plan, yes=True, standalone=True)
    assert not plan.already_allowed
    output = capsys.readouterr().err
    assert output.count("Warning:") == 1 and "[y/N]" not in output
    plan.persist()
    assert prepare_enrollment(ClinkRegistry(), "codex", str(home)).already_allowed


@pytest.mark.parametrize("response", [True, False])
async def test_allow_accept_or_decline(roots, monkeypatch, capsys, response):
    _, target, _ = roots
    calls = []

    async def confirm(plan, **kwargs):
        calls.append((plan.target, kwargs))
        return response

    monkeypatch.setattr(cli, "confirm_enrollment", confirm)
    arguments = argparse.Namespace(path=str(target), cli="codex", project=None, yes=False)
    assert await cli._allow(arguments) == 0
    assert len(calls) == 1
    assert registry_module.USER_CONFIG_DIR.exists() is response
    assert ("Added" if response else "Nothing was linked") in capsys.readouterr().err


def native_payload(target):
    return {
        "schema_version": 2,
        "request_id": "66f96fc9-969d-4c0e-ab92-e355fa0e7588",
        "cli": "codex",
        "model": "gpt-6.1-sol",
        "instructions": "Synthetic instructions",
        "input": "Synthetic input",
        "timeout_seconds": 30,
        "execution_mode": "native",
        "project_dir": str(target),
    }


@pytest.mark.parametrize("response", [True, False, "refusal"])
async def test_generate_preserves_parsed_request_and_launches_once_only_after_saved_reload(
    roots, monkeypatch, tmp_path, response
):
    _, target, _ = roots
    launched, parsed = [], []
    original_parser = cli.parse_native_request

    def parse(raw, **kwargs):
        request = original_parser(raw, **kwargs)
        parsed.append(request)
        return request

    class Service:
        registry = None

        async def run(self, request):
            assert registry_module.USER_CONFIG_DIR.exists()
            assert target in self.registry.get_client("codex").allowed_roots
            launched.append(request)
            if response == "refusal":
                return terminal_native_result(
                    started_at=request.started_at,
                    request=request,
                    code="path_not_allowed",
                    message="Synthetic refusal.",
                )
            return terminal_native_result(
                started_at=request.started_at, request=request, status="completed", output="result"
            )

        async def shutdown(self):
            pass

    async def confirm(plan, **kwargs):
        assert not registry_module.USER_CONFIG_DIR.exists()
        assert kwargs["deadline"] == parsed[0].deadline
        return bool(response)

    monkeypatch.setattr(cli, "parse_native_request", parse)
    monkeypatch.setattr(cli, "NativeGenerationService", Service)
    monkeypatch.setattr(cli, "confirm_enrollment", confirm)
    body = tmp_path / "request.json"
    body.write_text(json.dumps(native_payload(target)))
    with body.open("rb") as stream:
        result = await cli._generate(time.monotonic(), stream.fileno())
    expected = terminal_native_result(started_at=parsed[0].started_at, request=parsed[0])
    for key in ("request_sha256", "instructions_sha256", "input_sha256", "request_id"):
        assert result[key] == expected[key]
    assert len(parsed) == 1
    assert launched == ([parsed[0]] if response else [])
    if response:
        assert launched[0] is parsed[0]
        assert result["status"] == ("failed" if response == "refusal" else "completed")
        if response == "refusal":
            assert result["error"]["code"] == "path_not_allowed"
    else:
        assert result["status"] == "cancelled" and result["error"]["message"] == "Nothing was linked."
        assert not registry_module.USER_CONFIG_DIR.exists()


async def test_terminal_deadline_and_cancellation_remove_reader_and_close_descriptor(roots, terminal_input):
    _, target, _ = roots
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    provide, _, _ = terminal_input
    reader, writer = provide(b"", eof=False)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await cli.confirm_enrollment(plan, deadline=time.monotonic() + 0.02)
        task = asyncio.create_task(cli.confirm_enrollment(plan))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        os.close(reader)
        os.close(writer)
    assert not registry_module.USER_CONFIG_DIR.exists()


@pytest.mark.parametrize("trigger", ["parent", "sigint", "sigterm"])
async def test_supervision_cancels_approval_without_mutation(roots, terminal_input, monkeypatch, trigger):
    _, target, _ = roots
    plan = prepare_enrollment(ClinkRegistry(), "codex", str(target))
    provide, _, _ = terminal_input
    reader, writer = provide(b"", eof=False)
    task = asyncio.create_task(cli._supervise(cli.confirm_enrollment(plan), os.getppid()))
    await asyncio.sleep(0.02)
    assert not task.done(), task.exception() if task.done() else None
    if trigger == "parent":
        monkeypatch.setattr(cli.os, "getppid", lambda: -1)
    else:
        os.kill(os.getpid(), signal.SIGINT if trigger == "sigint" else signal.SIGTERM)
    try:
        assert await asyncio.wait_for(task, 1) == 1
    finally:
        os.close(reader)
        os.close(writer)
    assert not registry_module.USER_CONFIG_DIR.exists()


async def test_nested_generation_does_not_prepare_or_ask(roots, monkeypatch, tmp_path):
    _, target, _ = roots
    monkeypatch.setenv("CLINK_DELEGATION_DEPTH", "1")
    monkeypatch.setattr(cli, "prepare_native_generation", lambda *args: pytest.fail("nested approval"))
    body = tmp_path / "nested.json"
    body.write_text(json.dumps(native_payload(target)))
    with body.open("rb") as stream:
        result = await cli._generate(time.monotonic(), stream.fileno())
    assert result["error"]["code"] == "recursion_blocked"
    assert not registry_module.USER_CONFIG_DIR.exists()


@pytest.mark.parametrize("trigger", ["deadline", "parent", "signal"])
async def test_cancellation_before_publication_prevents_grant(roots, monkeypatch, trigger):
    _, target, _ = roots
    request = SimpleNamespace(cli="codex", project_dir=str(target), deadline=time.monotonic() + 0.1)
    original_check = project_setup._check_unchanged

    def interrupt_after_temp_write(*args, **kwargs):
        original_check(*args, **kwargs)
        if trigger == "deadline":
            time.sleep(0.12)
        elif trigger == "parent":
            monkeypatch.setattr(cli.os, "getppid", lambda: -1)
        else:
            os.kill(os.getpid(), signal.SIGTERM)

    async def approve(*args, **kwargs):
        return True

    monkeypatch.setattr(project_setup, "_check_unchanged", interrupt_after_temp_write)
    monkeypatch.setattr(cli, "confirm_enrollment", approve)
    operation = cli.prepare_native_generation(request, ClinkRegistry())
    if trigger == "deadline":
        with pytest.raises(BackendError, match="deadline expired"):
            await cli._supervise(operation, os.getppid())
    else:
        assert await cli._supervise(operation, os.getppid()) == 1
    assert not (registry_module.USER_CONFIG_DIR / "codex.json").exists()


@pytest.mark.parametrize("invalid", ["malformed", "duplicate"])
@pytest.mark.parametrize("spelling", ["relative", "symlink"])
async def test_initial_configuration_error_quotes_canonical_root_and_command(
    roots, monkeypatch, capsys, invalid, spelling
):
    home, target, _ = roots
    canonical = target.resolve(strict=True)
    if spelling == "relative":
        selected = "../selected/"
    else:
        alias = home / "selected-alias"
        alias.symlink_to(target, target_is_directory=True)
        selected = "selected-alias/"
    path = registry_module.USER_CONFIG_DIR / "broken-operator-profile.json"
    path.parent.mkdir(parents=True)
    original = b'{"name":"codex"' if invalid == "malformed" else b'{"name":"codex","name":"codex"}'
    path.write_bytes(original)
    initial_info = path.stat()

    async def forbidden_prompt(*args, **kwargs):
        pytest.fail("Invalid initial configuration must fail before asking for consent.")

    monkeypatch.setattr(cli, "confirm_enrollment", forbidden_prompt)
    arguments = argparse.Namespace(path=selected, cli="codex", project=None, yes=False)
    assert await cli._allow(arguments) == 1
    stderr = capsys.readouterr().err
    assert str(path) in stderr
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == initial_info.st_mtime_ns
    assert sorted(entry.name for entry in path.parent.iterdir()) == [path.name]
    assert "[y/N]" not in stderr
    assert f"allowed_roots entry {json.dumps(str(canonical))}" in stderr
    assert f"Operator command: {allow_command(str(canonical), 'codex')}" in stderr
    assert f"allowed_roots entry {json.dumps(selected)}" not in stderr


@pytest.mark.parametrize("schema", [1, 2])
async def test_invalid_generation_request_preserves_validated_id_without_service(roots, monkeypatch, tmp_path, schema):
    _, target, _ = roots
    payload = native_payload(target)
    payload.update(schema_version=schema, model="auto")
    if schema == 1:
        payload.pop("execution_mode")
        payload.pop("project_dir")
    monkeypatch.setattr(cli, "GenerationService", lambda: pytest.fail("Invalid request constructed a service."))
    monkeypatch.setattr(cli, "NativeGenerationService", lambda: pytest.fail("Invalid request constructed a service."))
    body = tmp_path / "invalid-request.json"
    body.write_text(json.dumps(payload))
    with body.open("rb") as stream:
        result = await cli._generate(time.monotonic(), stream.fileno())
    assert result["schema_version"] == schema
    assert result["status"] == "failed"
    assert result["error"]["code"] == "invalid_request"
    assert result["request_id"] == payload["request_id"]
    assert not registry_module.USER_CONFIG_DIR.exists()


async def test_parent_loss_during_saved_reload_preserves_grant_without_service_launch(
    roots, monkeypatch, tmp_path, capsys
):
    import clink.allowed_roots as allowed_module

    _, target, _ = roots
    destination = registry_module.USER_CONFIG_DIR / "codex.json"
    launched = []
    original_registry = ClinkRegistry

    def reload_loses_parent(*args, **kwargs):
        registry = original_registry(*args, **kwargs)
        if destination.exists():
            monkeypatch.setattr(cli.os, "getppid", lambda: -1)
        return registry

    class Service:
        registry = None

        async def run(self, request):
            launched.append(request)
            return terminal_native_result(
                started_at=request.started_at, request=request, status="completed", output="unexpected"
            )

        async def shutdown(self):
            pass

    async def approve(*args, **kwargs):
        return True

    monkeypatch.setattr(allowed_module, "ClinkRegistry", reload_loses_parent)
    monkeypatch.setattr(cli, "confirm_enrollment", approve)
    monkeypatch.setattr(cli, "NativeGenerationService", Service)
    body = tmp_path / "parent-loss-request.json"
    body.write_text(json.dumps(native_payload(target)))
    with body.open("rb") as stream:
        result = await cli._generate(time.monotonic(), stream.fileno())
    assert destination.exists()
    assert target in original_registry().get_client("codex").allowed_roots
    assert json.dumps(str(target)) in capsys.readouterr().err
    assert launched == []
    assert result["status"] == "cancelled"
    assert result["error"]["code"] == "cancelled"


async def test_preapproval_from_target_directory_admits_one_generation_without_question(
    roots, monkeypatch, tmp_path, native_factory
):
    _, target, data = roots
    monkeypatch.chdir(target)
    executions = []

    class Pool:
        async def execute(self, command, **kwargs):
            kwargs["prelaunch"]()
            executions.append(command)
            final = Path(command[command.index("--output-last-message") + 1])
            final.write_text("accepted")
            stdout = "\n".join(json.dumps(event) for event in events()) + "\n"
            return ProcessOutput(stdout, "", 0, len(stdout.encode()), 0, 0)

        async def shutdown(self):
            pass

    service, _, executable, _, _ = native_factory(pool=Pool())
    service.registry = None
    data["command"] = shlex.join([str(executable)])
    (registry_module.CONFIG_DIR / "arbitrary.json").write_text(json.dumps(data))
    bundled = ClinkRegistry().get_client("codex")
    assert bundled.allowed_roots == (target,)
    assert not bundled.allowed_roots_explicit
    arguments = argparse.Namespace(path=".", cli="codex", project=None, yes=True)
    assert await cli._allow(arguments) == 0
    destination = registry_module.USER_CONFIG_DIR / "codex.json"
    assert destination.exists()
    client = ClinkRegistry().get_client("codex")
    assert client.configuration_scope == "user" and client.allowed_roots_explicit
    assert client.allowed_roots == (target,)
    original_bytes = destination.read_bytes()
    original_mtime = destination.stat().st_mtime_ns
    launched = []
    original_run = service.run

    async def forbidden_question(*args, **kwargs):
        pytest.fail("Persisted independent roots must admit generation without another question.")

    async def run(request):
        launched.append(request)
        return await original_run(request)

    monkeypatch.setattr(service, "run", run)
    monkeypatch.setattr(cli, "confirm_enrollment", forbidden_question)
    monkeypatch.setattr(cli, "NativeGenerationService", lambda: service)
    body = tmp_path / "preapproved-request.json"
    body.write_text(json.dumps(native_payload(target)))
    with body.open("rb") as stream:
        result = await cli._generate(time.monotonic(), stream.fileno())
    assert result["status"] == "completed" and result["output"] == "accepted"
    assert result["execution"]["admission"] == "verified"
    assert len(launched) == len(executions) == 1
    assert executions[0][0] == str(executable)
    assert service.registry.get_client("codex").configuration_scope == "user"
    assert service.registry.get_client("codex").allowed_roots_explicit
    assert destination.read_bytes() == original_bytes
    assert destination.stat().st_mtime_ns == original_mtime


@pytest.mark.parametrize("phase", ["before", "after", "unknown"])
async def test_allow_json_cancellation_never_reports_unknown_publication_as_unsaved(roots, monkeypatch, capsys, phase):
    from clink.allowed_roots import Enrollment

    _, target, _ = roots
    destination = registry_module.USER_CONFIG_DIR / "codex.json"
    persist = Enrollment.persist

    def cancelled(plan, **kwargs):
        if phase == "after":
            persist(plan, **kwargs)
        if phase == "unknown":
            # Simulate an interrupted publication transport with no reliable callback.
            raise asyncio.CancelledError
        failure = asyncio.CancelledError()
        failure.grant_saved = phase == "after"
        raise failure

    monkeypatch.setattr(Enrollment, "persist", cancelled)
    arguments = argparse.Namespace(path=str(target), cli="codex", project=None, yes=True, json=True)
    assert await cli._allow(arguments) == 1
    captured = capsys.readouterr()
    assert "cancelled" in captured.err
    if phase == "unknown":
        assert captured.out == ""
    else:
        result = json.loads(captured.out)
        assert result == {
            "schema_version": 1,
            "cli": "codex",
            "project_dir": str(target),
            "status": "failed",
            "grant_saved": phase == "after",
            "error_code": "cancelled",
        }
    assert destination.exists() is (phase == "after")


async def test_allow_json_parent_loss_after_reload_reports_saved_grant(roots, monkeypatch, capsys):
    import clink.allowed_roots as allowed_module

    _, target, _ = roots
    destination = registry_module.USER_CONFIG_DIR / "codex.json"

    def parent_loss_on_saved_reload(*args, **kwargs):
        registry = ClinkRegistry(*args, **kwargs)
        if destination.exists():
            monkeypatch.setattr(cli.os, "getppid", lambda: -1)
        return registry

    monkeypatch.setattr(allowed_module, "ClinkRegistry", parent_loss_on_saved_reload)
    arguments = argparse.Namespace(path=str(target), cli="codex", project=None, yes=True, json=True)
    assert await cli._allow(arguments) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["error_code"] == "cancelled"
    assert result["grant_saved"] is True
    assert target in ClinkRegistry().get_client("codex").allowed_roots


async def test_allow_json_decline_reports_no_publication_and_preserves_text_behavior(roots, monkeypatch, capsys):
    _, target, _ = roots

    async def decline(*_args, **_kwargs):
        return False

    monkeypatch.setattr(cli, "confirm_enrollment", decline)
    arguments = argparse.Namespace(path=str(target), cli="codex", project=None, yes=False, json=True)
    assert await cli._allow(arguments) == 0
    captured = capsys.readouterr()
    assert "Nothing was linked." in captured.err
    result = json.loads(captured.out)
    assert result["status"] == "failed" and result["error_code"] == "cancelled"
    assert result["grant_saved"] is False
    assert not registry_module.USER_CONFIG_DIR.exists()


def test_empty_consumer_flag_on_allow_never_falls_back_to_global_manifests(roots, monkeypatch, capsys):
    _, target, _ = roots
    monkeypatch.setattr(
        registry_module, "get_env", lambda *_args: pytest.fail("empty consumer flag used global source")
    )
    assert cli.main(["--consumer-config-dir", "", "allow", str(target), "--cli", "codex", "--yes", "--json"]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["error_code"] == "invalid_config" and result["grant_saved"] is False
    assert "canonical directory" in captured.err
    assert not registry_module.USER_CONFIG_DIR.exists()


@pytest.mark.parametrize(
    "options",
    [
        ["--yes", "--json", "--unknown"],
        ["--json", "--unknown", "--yes"],
        ["--yes", "--unknown", "--json"],
        ["--yes", "--json", "--project"],
        ["--yes", "--json", "--cli"],
    ],
    ids=["unknown-last", "json-before-unknown", "json-after-unknown", "missing-project", "duplicate-cli-missing"],
)
def test_allow_json_parse_failure_preserves_directory_grant_result_contract(roots, capsys, options):
    home, target, _ = roots
    (home / "selected-alias").symlink_to(target, target_is_directory=True)
    source = registry_module.CONFIG_DIR / "arbitrary.json"
    original = source.read_bytes(), source.stat().st_mtime_ns
    exit_code = cli.main(["allow", "selected-alias/", "--cli", "codex", *options])
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    result = json.loads(captured.out)
    assert result == {
        "schema_version": 1,
        "cli": "codex",
        "project_dir": str(target.resolve(strict=True)),
        "status": "failed",
        "grant_saved": False,
        "error_code": "invalid_invocation",
    }
    assert type(result["schema_version"]) is int
    assert exit_code == 1
    assert (source.read_bytes(), source.stat().st_mtime_ns) == original
    assert not registry_module.USER_CONFIG_DIR.exists()
