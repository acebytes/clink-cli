"""Synthetic process-boundary tests for one generation result and owned cleanup."""

import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from clink.generation_contracts import RAW_REQUEST_BYTES

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ID = "66f96fc9-969d-4c0e-ab92-e355fa0e7588"
ARGUMENTS = ["generate", "--request-json", "-"]
RESULT_FIELDS = {
    "schema_version",
    "request_id",
    "status",
    "output",
    "truncated",
    "error",
    "duration_ms",
    "request_sha256",
    "instructions_sha256",
    "input_sha256",
    "execution",
    "warnings",
}
EXECUTION_FIELDS = {
    "cli",
    "profile",
    "requested_model",
    "effective_model",
    "actual_model",
    "recipe_sha256",
    "executable_sha256",
    "catalog_sha256",
    "admission",
}


def request_bytes():
    return json.dumps(
        {
            "schema_version": 1,
            "request_id": REQUEST_ID,
            "cli": "codex",
            "model": "gpt-6.1-sol",
            "instructions": "PRIVATE_INSTRUCTIONS",
            "input": "PRIVATE_INPUT",
            "timeout_seconds": 30,
        }
    ).encode()


@pytest.fixture
def cli_environment(tmp_path):
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONNOUSERSITE": "1",
        "CLINK_DELEGATION_DEPTH": "0",
    }


def terminal(stdout, stderr, returncode, *, status, code=None, exit_code):
    assert returncode == exit_code, stderr.decode(errors="replace")
    assert stdout.endswith(b"\n")
    assert len(stdout.splitlines()) == 1
    result = json.loads(stdout)
    assert set(result) == RESULT_FIELDS
    assert set(result["execution"]) == EXECUTION_FIELDS
    assert result["schema_version"] == 1
    assert result["status"] == status
    assert result["truncated"] is False
    assert result["duration_ms"] >= 0
    assert result["warnings"] == []
    assert result["execution"]["profile"] == "generation_v1"
    assert result["execution"]["actual_model"] is None
    if code is None:
        assert result["error"] is None
    else:
        assert result["output"] is None
        assert result["error"]["code"] == code
        assert set(result["error"]) == {"code", "message", "retry_after_seconds"}
        assert result["error"]["retry_after_seconds"] is None
        assert len(result["error"]["message"]) <= 1024
    assert b"PRIVATE" not in stdout + stderr
    return result


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"", "invalid_request"),
        (b"{PRIVATE malformed}", "invalid_request"),
        (b'{"request_id":"' + REQUEST_ID.encode() + b'","request_id":"duplicate"}', "invalid_request"),
        (b'{"input":"PRIVATE\xff"}', "invalid_request"),
        (request_bytes() + b" {}", "invalid_request"),
        (b" " * (RAW_REQUEST_BYTES + 1), "input_limit"),
    ],
    ids=["empty", "malformed", "duplicate", "invalid-utf8", "multiple-values", "raw-limit"],
)
def test_module_rejects_bad_input_with_exactly_one_terminal_line(raw, code, cli_environment):
    completed = subprocess.run(
        [sys.executable, "-m", "clink.cli", *ARGUMENTS],
        input=raw,
        capture_output=True,
        cwd=ROOT,
        env=cli_environment,
        timeout=5,
    )
    result = terminal(completed.stdout, completed.stderr, completed.returncode, status="failed", code=code, exit_code=2)
    assert result["request_id"] is None
    assert result["request_sha256"] is None
    assert result["execution"]["admission"] == "not_checked"


@pytest.mark.parametrize(
    "arguments",
    [[], ["generate"], ["generate", "--request-json", "file.json"], [*ARGUMENTS, "--systemprompt", "PRIVATE"]],
    ids=["no-command", "missing-request-flag", "file-input", "unsupported-option"],
)
def test_module_rejects_invalid_invocation_with_terminal_json(arguments, cli_environment):
    completed = subprocess.run(
        [sys.executable, "-m", "clink.cli", *arguments],
        input=request_bytes(),
        capture_output=True,
        cwd=ROOT,
        env=cli_environment,
        timeout=5,
    )
    result = terminal(
        completed.stdout,
        completed.stderr,
        completed.returncode,
        status="failed",
        code="invalid_invocation",
        exit_code=2,
    )
    assert result["request_id"] is None


def test_production_generation_is_unverified_and_never_launches_a_model(tmp_path, cli_environment):
    launched = tmp_path / "unexpected-launch"
    executable = tmp_path / "codex"
    executable.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(launched)!r}).touch()\n")
    executable.chmod(0o700)
    cli_environment["PATH"] = str(tmp_path)
    # Invalid configuration would fail if production admission reached registry discovery.
    cli_environment["CLI_CLIENTS_CONFIG_PATH"] = str(tmp_path / "missing-manifest.json")
    completed = subprocess.run(
        [sys.executable, "-m", "clink.cli", *ARGUMENTS],
        input=request_bytes(),
        capture_output=True,
        cwd=ROOT,
        env=cli_environment,
        timeout=5,
    )
    result = terminal(
        completed.stdout,
        completed.stderr,
        completed.returncode,
        status="failed",
        code="policy_unverified",
        exit_code=1,
    )
    assert result["request_id"] == REQUEST_ID
    assert all(len(result[name]) == 64 for name in ("request_sha256", "instructions_sha256", "input_sha256"))
    assert result["execution"]["requested_model"] == "gpt-6.1-sol"
    assert result["execution"]["effective_model"] == "gpt-6.1-sol"
    assert result["execution"]["admission"] == "unverified"
    assert result["execution"]["executable_sha256"] is None
    assert not launched.exists()


def wait_for_file(path, process=None, timeout=5, *, allow_empty=False):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.exists():
            content = path.read_text()
            if content or allow_empty:
                return content
        if process is not None and process.poll() is not None:
            pytest.fail(f"Synthetic CLI exited before creating {path.name}: {process.stderr.read()!r}")
        time.sleep(0.01)
    pytest.fail(f"Synthetic CLI did not create {path.name} within {timeout} seconds.")


def close_input(process):
    process.stdin.write(request_bytes())
    process.stdin.close()
    process.stdin = None


@contextlib.contextmanager
def running_cli(command, environment):
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env=environment,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def intake_command(tmp_path, *, stdin_seconds=3):
    ready = tmp_path / "intake-ready"
    script = textwrap.dedent(f"""
        from pathlib import Path
        import clink.cli as cli
        original_read = cli.read_request_fd
        async def marked_read(fd):
            Path({str(ready)!r}).touch()
            return await original_read(fd)
        cli.read_request_fd = marked_read
        cli.STDIN_SECONDS = {stdin_seconds!r}
        raise SystemExit(cli.main())
        """)
    return [sys.executable, "-c", script, *ARGUMENTS], ready


@pytest.mark.skipif(os.name != "posix", reason="Generation intake uses POSIX file descriptors and signals.")
def test_stdin_requires_eof_before_its_finite_deadline(tmp_path, cli_environment):
    command, ready = intake_command(tmp_path, stdin_seconds=0.3)
    with running_cli(command, cli_environment) as process:
        process.stdin.write(request_bytes())
        process.stdin.flush()
        wait_for_file(ready, process, allow_empty=True)
        # Keeping the write end open must not leave a blocking reader behind at exit.
        process.wait(timeout=3)
        result = terminal(
            process.stdout.read(),
            process.stderr.read(),
            process.returncode,
            status="timed_out",
            code="timeout",
            exit_code=124,
        )
    assert result["request_id"] is None
    assert result["request_sha256"] is None
    assert result["duration_ms"] >= 250


@pytest.mark.skipif(os.name != "posix", reason="Generation cancellation requires POSIX signals.")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_signal_during_intake_returns_one_cancelled_result(tmp_path, cli_environment, sig):
    command, ready = intake_command(tmp_path)
    with running_cli(command, cli_environment) as process:
        process.stdin.write(request_bytes())
        process.stdin.flush()
        wait_for_file(ready, process, allow_empty=True)
        process.send_signal(sig)
        process.wait(timeout=3)
        result = terminal(
            process.stdout.read(),
            process.stderr.read(),
            process.returncode,
            status="cancelled",
            code="cancelled",
            exit_code=130,
        )
    assert result["request_id"] is None
    assert result["execution"]["admission"] == "not_checked"


@pytest.fixture
def supervised_generation(tmp_path):
    """Patch the implementation gate locally to test synthetic lifecycle, never production admission."""
    executable = tmp_path / "synthetic-codex"
    pids = tmp_path / "native-pids.json"
    release = tmp_path / "release-native"
    evidence = tmp_path / "synthetic-evidence.json"
    evidence.write_text('{"synthetic":true}')
    output = "  generated é 🦉 \\ source\n"
    executable.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f"""
            import json
            import os
            import signal
            import subprocess
            import sys
            import time
            from pathlib import Path

            sys.stdin.buffer.read()
            child = subprocess.Popen(
                [sys.executable, '-c', 'import time; time.sleep(60)'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            def stop(_sig, _frame):
                child.wait(timeout=2)
                raise SystemExit(0)
            signal.signal(signal.SIGTERM, stop)
            Path({str(pids)!r}).write_text(json.dumps([os.getpid(), child.pid]))
            while not Path({str(release)!r}).exists():
                time.sleep(0.01)
            child.terminate()
            child.wait(timeout=2)
            for event in (
                {{'type': 'thread.started', 'thread_id': 'synthetic-thread'}},
                {{'type': 'turn.started'}},
                {{'type': 'item.completed', 'item': {{'type': 'agent_message', 'text': {output!r}}}}},
                {{'type': 'turn.completed', 'usage': {{'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0}}}},
            ):
                print(json.dumps(event, ensure_ascii=False), flush=True)
            """))
    executable.chmod(0o700)
    exit_record = tmp_path / "generation-exit"
    script = textwrap.dedent(f"""
        from pathlib import Path
        from types import SimpleNamespace
        import clink.cli as cli
        import clink.runner as runner
        from clink.generation import GenerationService
        from clink.generation_contracts import sha256
        from clink.generation_policy import (
            CATALOG_SHA256, GenerationAdmission, generation_recipe_digest,
        )
        from clink.models import ResolvedCLIClient
        from clink.parsers.codex_generation import parser_contract_digest
        from clink.policy import executable_digest

        runner.CLEANUP_SECONDS = 0.5
        client = ResolvedCLIClient(
            name='codex', adapter='codex', executable=({str(executable)!r},),
            working_dir=Path({str(tmp_path)!r}), allowed_roots=(Path({str(tmp_path)!r}),),
            timeout_seconds=30, parser='codex', roles={{}},
        )
        admission = GenerationAdmission(
            version='synthetic-test', verified=True,
            executable_sha256=executable_digest({str(executable)!r}),
            recipe_sha256=generation_recipe_digest(), catalog_sha256=CATALOG_SHA256,
            parser_sha256=parser_contract_digest(), evidence_path={str(evidence)!r},
            evidence_sha256=sha256(Path({str(evidence)!r}).read_bytes()),
        )
        registry = SimpleNamespace(get_client=lambda name: client)
        # Synthetic executables establish lifecycle behavior only. No user-facing
        # setting or admission evidence can remove the real implementation gate.
        GenerationService._require_runtime_ceiling = staticmethod(lambda: None)
        cli.GenerationService = lambda: GenerationService(registry, runner.ProcessPool(), admission)
        result = cli.main()
        Path({str(exit_record)!r}).write_text(str(result))
        raise SystemExit(result)
        """)
    yield {
        "command": [sys.executable, "-c", script, *ARGUMENTS],
        "pids": pids,
        "release": release,
        "output": output,
        "exit_record": exit_record,
    }
    if pids.exists():
        # Also clean up failed-test paths, without relying on the CLI assertion passing.
        native_pid = json.loads(pids.read_text())[0]
        with contextlib.suppress(ProcessLookupError):
            os.killpg(native_pid, signal.SIGKILL)


def assert_owned_processes_gone(pids):
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def collect_result_after_cleanup(process, pids):
    readable, _, _ = select.select([process.stdout], [], [], 5)
    assert readable, "The CLI did not publish its terminal result."
    first_output = os.read(process.stdout.fileno(), RAW_REQUEST_BYTES + 1)
    # Check as soon as any result bytes are observable, before waiting for CLI exit.
    assert_owned_processes_gone(pids)
    stdout, stderr = process.communicate(timeout=5)
    return first_output + stdout, stderr


@pytest.mark.skipif(os.name != "posix", reason="Shared supervision requires POSIX process groups.")
def test_eof_submits_once_and_completed_cli_preserves_exact_final_text(supervised_generation, cli_environment):
    fixture = supervised_generation
    with running_cli(fixture["command"], cli_environment) as process:
        close_input(process)
        pids = json.loads(wait_for_file(fixture["pids"], process))
        assert process.poll() is None  # EOF ends the body, while the generation attempt remains active.
        fixture["release"].touch()
        stdout, stderr = collect_result_after_cleanup(process, pids)
        result = terminal(stdout, stderr, process.returncode, status="completed", exit_code=0)
    assert result["output"] == fixture["output"]
    assert result["request_id"] == REQUEST_ID
    assert result["execution"]["admission"] == "verified"


@pytest.mark.skipif(os.name != "posix", reason="Shared supervision requires POSIX process groups.")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_signal_midrun_cleans_child_and_descendant_before_one_result(supervised_generation, cli_environment, sig):
    fixture = supervised_generation
    with running_cli(fixture["command"], cli_environment) as process:
        close_input(process)
        pids = json.loads(wait_for_file(fixture["pids"], process))
        process.send_signal(sig)
        stdout, stderr = collect_result_after_cleanup(process, pids)
        result = terminal(stdout, stderr, process.returncode, status="cancelled", code="cancelled", exit_code=130)
    assert result["request_id"] == REQUEST_ID
    assert result["execution"]["admission"] == "verified"


@pytest.mark.skipif(os.name != "posix", reason="Parent-loss ownership requires POSIX process groups.")
def test_original_parent_loss_cancels_and_cleans_owned_tree(supervised_generation, cli_environment, tmp_path):
    fixture = supervised_generation
    output = tmp_path / "orphan-result.json"
    errors = tmp_path / "orphan-stderr"
    supervisor = textwrap.dedent(f"""
        import subprocess
        import time
        with open({str(output)!r}, 'wb') as output, open({str(errors)!r}, 'wb') as errors:
            child = subprocess.Popen({fixture['command']!r}, stdin=subprocess.PIPE, stdout=output, stderr=errors)
            child.stdin.write({request_bytes()!r})
            child.stdin.close()
            time.sleep(60)
        """)
    with running_cli([sys.executable, "-c", supervisor], cli_environment) as parent:
        pids = json.loads(wait_for_file(fixture["pids"], parent))
        parent.terminate()
        parent.wait(timeout=3)
        assert wait_for_file(fixture["exit_record"]) == "130"
        result = terminal(
            output.read_bytes(), errors.read_bytes(), 130, status="cancelled", code="cancelled", exit_code=130
        )
        assert_owned_processes_gone(pids)
    assert result["request_id"] == REQUEST_ID


@pytest.mark.skipif(os.name != "posix", reason="Broken-pipe cleanup requires POSIX process groups.")
def test_broken_result_pipe_still_allows_controlled_cleanup(supervised_generation, cli_environment):
    fixture = supervised_generation
    with running_cli(fixture["command"], cli_environment) as process:
        close_input(process)
        pids = json.loads(wait_for_file(fixture["pids"], process))
        process.stdout.close()
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        stderr = process.stderr.read()
        assert process.returncode == 1, stderr.decode(errors="replace")
        assert b"PRIVATE" not in stderr
        assert_owned_processes_gone(pids)
