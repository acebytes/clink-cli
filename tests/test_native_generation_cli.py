"""Synthetic native-v2 CLI dispatch, authority, final-only output and lifecycle tests."""

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

ROOT = Path(__file__).resolve().parents[1]
ARGUMENTS = ["generate", "--request-json", "-"]
REQUEST_ID = "66f96fc9-969d-4c0e-ab92-e355fa0e7588"
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
V1_EXECUTION_FIELDS = {
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
NATIVE_EXECUTION_FIELDS = V1_EXECUTION_FIELDS | {
    "execution_mode",
    "project_dir",
    "side_effects_possible",
    "permission_observability",
    "fulfillment_verified",
}
FINAL_TEXT = "  transformed café 🦉 \\ {{source}}\r\n"


def request_data(project, **changes):
    return {
        "schema_version": 2,
        "request_id": REQUEST_ID,
        "cli": "codex",
        "model": "gpt-6.1-sol",
        "instructions": "PRIVATE_INSTRUCTIONS",
        "input": "PRIVATE_SOURCE",
        "timeout_seconds": 30,
        "execution_mode": "native",
        "project_dir": str(project),
        **changes,
    }


def encode(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


@pytest.fixture
def cli_environment(tmp_path):
    scratch = tmp_path / "native-tmp"
    scratch.mkdir()
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(scratch.resolve(strict=True)),
        "PYTHONNOUSERSITE": "1",
        "CLINK_DELEGATION_DEPTH": "0",
    }


def terminal(stdout, stderr, returncode, *, status, code=None, exit_code, schema=2, side_effects=False):
    assert returncode == exit_code, stderr.decode(errors="replace")
    assert stdout.endswith(b"\n") and len(stdout.splitlines()) == 1
    result = json.loads(stdout)
    assert set(result) == RESULT_FIELDS
    assert result["schema_version"] == schema
    assert result["status"] == status
    assert result["duration_ms"] >= 0
    assert len(result["warnings"]) <= 8
    assert result["execution"]["actual_model"] is None
    assert set(result["execution"]) == (NATIVE_EXECUTION_FIELDS if schema == 2 else V1_EXECUTION_FIELDS)
    if schema == 2:
        assert len(result["execution"]) == 14
        assert result["execution"]["profile"] == "generation_native_v1"
        assert result["execution"]["execution_mode"] == "native"
        assert result["execution"]["side_effects_possible"] is side_effects
        assert result["execution"]["catalog_sha256"] is None
        assert result["execution"]["permission_observability"] == "limited"
        assert result["execution"]["fulfillment_verified"] is False
    else:
        assert result["execution"]["profile"] == "generation_v1"
    if code is None:
        assert result["error"] is None
    else:
        assert result["output"] is None
        assert result["error"]["code"] == code
        assert set(result["error"]) == {"code", "message", "retry_after_seconds"}
        assert len(result["error"]["message"]) <= 1024
    assert b"PRIVATE" not in stdout + stderr
    return result


def run_module(data, environment):
    return subprocess.run(
        [sys.executable, "-m", "clink.cli", *ARGUMENTS],
        input=encode(data),
        capture_output=True,
        cwd=ROOT,
        env=environment,
        timeout=5,
    )


def test_admitted_native_default_refuses_without_independently_configured_roots(tmp_path, cli_environment):
    project = tmp_path.resolve(strict=True)
    launched = tmp_path / "unexpected-native-launch"
    executable = tmp_path / "codex"
    executable.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(launched)!r}).touch()\n")
    executable.chmod(0o700)
    cli_environment["PATH"] = str(tmp_path)
    completed = run_module(request_data(project), cli_environment)
    result = terminal(
        completed.stdout,
        completed.stderr,
        completed.returncode,
        status="failed",
        code="path_not_allowed",
        exit_code=1,
    )
    assert result["request_id"] == REQUEST_ID
    assert result["execution"]["project_dir"] == str(project)
    assert result["execution"]["admission"] == "not_checked"
    assert result["execution"]["executable_sha256"] is None
    assert all(len(result[key]) == 64 for key in ("request_sha256", "instructions_sha256", "input_sha256"))
    assert not launched.exists()
    assert list(Path(cli_environment["TMPDIR"]).iterdir()) == []


def test_published_duration_includes_service_shutdown(tmp_path, cli_environment):
    script = textwrap.dedent(f"""
        import asyncio
        from types import SimpleNamespace
        import clink.cli as cli
        import clink.generation_contracts as contracts
        from clink.native_generation_contracts import terminal_native_result

        clock = SimpleNamespace(now=100.0)
        monotonic = SimpleNamespace(monotonic=lambda: clock.now)
        cli.time = monotonic
        contracts.time = monotonic

        class DelayedShutdownService:
            def __init__(self):
                self.registry = None

            async def run(self, request):
                clock.now = 100.25
                return terminal_native_result(
                    started_at=request.started_at, request=request,
                    status='completed', output={FINAL_TEXT!r},
                )

            async def shutdown(self):
                await asyncio.sleep(0)
                clock.now = 102.0

        async def already_approved(request, registry):
            return registry

        cli.prepare_native_generation = already_approved
        cli.NativeGenerationService = DelayedShutdownService
        raise SystemExit(cli.main())
        """)
    completed = subprocess.run(
        [sys.executable, "-c", script, *ARGUMENTS],
        input=encode(request_data(tmp_path.resolve(strict=True))),
        capture_output=True,
        cwd=ROOT,
        env=cli_environment,
        timeout=5,
    )
    result = terminal(completed.stdout, completed.stderr, completed.returncode, status="completed", exit_code=0)
    assert result["output"] == FINAL_TEXT
    # Publishing must include the 1750 ms spent shutting down after terminal creation.
    assert result["duration_ms"] == 2000


@pytest.mark.parametrize("schema", [True, 2.0, "2"], ids=["boolean", "float", "string"])
def test_noninteger_schema_never_dispatches_native_authority(schema, tmp_path, cli_environment):
    completed = run_module(request_data(tmp_path.resolve(strict=True), schema_version=schema), cli_environment)
    terminal(
        completed.stdout,
        completed.stderr,
        completed.returncode,
        schema=1,
        status="failed",
        code="invalid_request",
        exit_code=2,
    )


def test_legacy_v1_stays_refused_and_cannot_add_native_selection(tmp_path, cli_environment):
    legacy = request_data(tmp_path.resolve(strict=True), schema_version=1)
    legacy.pop("execution_mode")
    legacy.pop("project_dir")
    completed = run_module(legacy, cli_environment)
    terminal(
        completed.stdout,
        completed.stderr,
        completed.returncode,
        schema=1,
        status="failed",
        code="policy_unverified",
        exit_code=1,
    )
    completed = run_module({**legacy, "execution_mode": "native", "project_dir": str(tmp_path)}, cli_environment)
    terminal(
        completed.stdout,
        completed.stderr,
        completed.returncode,
        schema=1,
        status="failed",
        code="invalid_request",
        exit_code=2,
    )


def wait_for_json(path, process=None, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.exists():
            try:
                return json.loads(path.read_bytes())
            except ValueError:
                pass
        if process is not None and process.poll() is not None:
            pytest.fail(f"Synthetic CLI exited before {path.name}: {process.stderr.read()!r}")
        time.sleep(0.01)
    pytest.fail(f"Synthetic CLI did not create {path.name} within {timeout} seconds.")


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
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def submit(process, data):
    process.stdin.write(encode(data))
    process.stdin.close()
    process.stdin = None


@pytest.fixture
def synthetic_native(tmp_path):
    root = tmp_path.resolve(strict=True)
    approved = root / "approved"
    project = approved / "project"
    project.mkdir(parents=True)
    configured_cwd = root / "different-configured-working-directory"
    configured_cwd.mkdir()
    executable = root / "synthetic-codex"
    capture = root / "native-capture.json"
    stopped = root / "native-stop.json"
    release = root / "release-native"
    control = root / "synthetic-native-control.json"
    evidence = root / "synthetic-admission.json"
    evidence.write_text('{"synthetic":true}')
    control.write_text(json.dumps({"mode": "completed", "final_text": FINAL_TEXT}))
    executable.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f"""
            import json
            import os
            import signal
            import stat
            import subprocess
            import sys
            import time
            from pathlib import Path

            sys.stdin.buffer.read()
            destination = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
            child = subprocess.Popen(
                [sys.executable, '-c', 'import time; time.sleep(60)'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            def stop(_sig, _frame):
                Path({str(stopped)!r}).write_text(json.dumps({{'final_file_survived': destination.exists()}}))
                child.wait(timeout=2)
                raise SystemExit(0)
            signal.signal(signal.SIGTERM, stop)
            details = {{
                'pids': [os.getpid(), child.pid], 'final_path': str(destination),
                'file_mode': stat.S_IMODE(destination.stat().st_mode),
                'directory_mode': stat.S_IMODE(destination.parent.stat().st_mode),
                'cwd': os.getcwd(), 'argv': sys.argv[1:],
                'integration_present': os.environ.get('SYNTHETIC_INTEGRATION') == 'synthetic-value',
                'unrelated_secret_present': 'UNRELATED_SYNTHETIC_SECRET' in os.environ,
            }}
            Path({str(capture)!r}).write_text(json.dumps(details))
            while not Path({str(release)!r}).exists():
                time.sleep(0.01)
            child.terminate()
            child.wait(timeout=2)
            settings = json.loads(Path({str(control)!r}).read_text())
            mode = settings['mode']
            if mode == 'invalid-final':
                destination.write_bytes(b'\\xff')
            elif mode != 'missing-final':
                destination.write_text(settings['final_text'], encoding='utf-8', newline='')
            command = {{
                'id': 'tool', 'type': 'command_execution', 'command': 'PRIVATE_COMMAND',
                'aggregated_output': 'PRIVATE_TOOL_OUTPUT', 'exit_code': 1, 'status': 'failed',
            }}
            if mode == 'permission-needed':
                command.update(
                    aggregated_output='approval required by policy, but AskForApproval is set to Never',
                    exit_code=-1, status='declined',
                )
            events = [
                {{'type': 'thread.started', 'thread_id': {REQUEST_ID!r}}},
                {{'type': 'turn.started'}},
                {{'type': 'item.completed', 'item': {{'id': 'commentary', 'type': 'agent_message', 'text': 'PRIVATE_EARLY'}}}},
                {{'type': 'item.completed', 'item': command}},
                {{'type': 'item.completed', 'item': {{'id': 'last-streamed', 'type': 'agent_message', 'text': 'PRIVATE_LATE'}}}},
                {{'type': 'turn.completed', 'usage': {{
                    'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0,
                    'reasoning_output_tokens': 0, 'cache_write_input_tokens': 0,
                }}}},
            ]
            if mode == 'invalid-terminal':
                events[-1] = {{'type': 'turn.failed', 'error': {{'message': 'PRIVATE_FAILURE'}}}}
            for event in events:
                print(json.dumps(event), flush=True)
            print('PRIVATE_STDERR', file=sys.stderr)
            """))
    executable.chmod(0o700)
    exit_record = root / "native-cli-exit.json"

    def command(**changes):
        settings = {
            "allowed_roots_explicit": True,
            "configuration_scope": "explicit",
            "project_binding": None,
            "allowed_roots": (str(approved),),
            **changes,
        }
        script = textwrap.dedent(f"""
            import json
            from pathlib import Path
            from types import SimpleNamespace
            import clink.cli as cli
            from clink.native_generation import NativeGenerationService
            from clink.native_generation_policy import NativeGenerationAdmission, native_generation_recipe_digest
            from clink.generation_contracts import sha256
            from clink.models import ResolvedCLIClient, ResolvedGenerationNative
            from clink.parsers.codex_native_generation import parser_contract_digest
            from clink.policy import executable_digest
            from clink.runner import ProcessPool

            settings = {settings!r}
            client = ResolvedCLIClient(
                name='codex', adapter='codex', executable=({str(executable)!r},),
                working_dir=Path({str(configured_cwd)!r}),
                allowed_roots=tuple(Path(root) for root in settings['allowed_roots']),
                allowed_roots_explicit=settings['allowed_roots_explicit'],
                configuration_scope=settings['configuration_scope'], timeout_seconds=30, parser='codex', roles={{}},
                generation_native=ResolvedGenerationNative(
                    inherit_env=('SYNTHETIC_INTEGRATION',), required_env=('SYNTHETIC_INTEGRATION',),
                ),
            )
            registry = SimpleNamespace(project_dir=settings['project_binding'], get_client=lambda name: client)
            admission = NativeGenerationAdmission(
                version='synthetic-only', verified=True,
                executable_sha256=executable_digest({str(executable)!r}),
                recipe_sha256=native_generation_recipe_digest(), parser_sha256=parser_contract_digest(),
                evidence_path={str(evidence)!r}, evidence_sha256=sha256(Path({str(evidence)!r}).read_bytes()),
            )
            cli.NativeGenerationService = lambda: NativeGenerationService(registry, ProcessPool(), admission)
            async def service_only_preflight(request, configured_registry):
                return configured_registry
            cli.prepare_native_generation = service_only_preflight
            result = cli.main()
            Path({str(exit_record)!r}).write_text(json.dumps(result))
            raise SystemExit(result)
            """)
        return [sys.executable, "-c", script, *ARGUMENTS]

    yield {
        "command": command,
        "project": project,
        "approved": approved,
        "capture": capture,
        "stopped": stopped,
        "release": release,
        "control": control,
        "exit_record": exit_record,
    }
    if capture.exists():
        with contextlib.suppress(ProcessLookupError):
            os.killpg(json.loads(capture.read_bytes())["pids"][0], signal.SIGKILL)


@pytest.fixture
def candidate_environment(cli_environment):
    return {
        **cli_environment,
        "SYNTHETIC_INTEGRATION": "synthetic-value",
        "UNRELATED_SYNTHETIC_SECRET": "synthetic-never-forwarded",
    }


def assert_cleaned(capture):
    for pid in capture["pids"]:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    final = Path(capture["final_path"])
    assert not final.exists()
    assert not final.parent.exists()


def collect_after_cleanup(process, capture):
    readable, _, _ = select.select([process.stdout], [], [], 8)
    assert readable, "Native CLI did not publish a terminal result."
    first = os.read(process.stdout.fileno(), 1024 * 1024 + 1)
    # Both process-tree and private-resource cleanup must precede any terminal bytes.
    assert_cleaned(capture)
    stdout, stderr = process.communicate(timeout=3)
    return first + stdout, stderr


@pytest.mark.parametrize(
    "change",
    [
        {"execution_mode": "consultation"},
        {"execution_mode": None},
        {"project_dir": "relative/project"},
        {"project_dir": None},
        {"role": "default"},
    ],
    ids=["wrong-mode", "null-mode", "relative-project", "null-project", "authority-extra"],
)
def test_invalid_native_selection_never_launches(change, synthetic_native, candidate_environment):
    fixture = synthetic_native
    with running_cli(fixture["command"](), candidate_environment) as process:
        stdout, stderr = process.communicate(encode(request_data(fixture["project"], **change)), timeout=5)
        result = terminal(stdout, stderr, process.returncode, status="failed", code="invalid_request", exit_code=2)
    assert result["execution"]["project_dir"] is None
    assert result["request_sha256"] is None
    assert not fixture["capture"].exists()


@pytest.mark.parametrize("kind", ["missing-mode", "missing-project", "symlink", "noncanonical", "missing"])
def test_native_project_and_mode_are_required_without_path_rewriting(kind, synthetic_native, candidate_environment):
    fixture = synthetic_native
    data = request_data(fixture["project"])
    if kind.startswith("missing-"):
        data.pop("execution_mode" if kind == "missing-mode" else "project_dir")
    elif kind == "symlink":
        link = fixture["approved"] / "project-link"
        link.symlink_to(fixture["project"], target_is_directory=True)
        data["project_dir"] = str(link)
    elif kind == "noncanonical":
        data["project_dir"] += "/."
    else:
        data["project_dir"] = str(fixture["project"] / "nonexistent")
    with running_cli(fixture["command"](), candidate_environment) as process:
        stdout, stderr = process.communicate(encode(data), timeout=5)
        terminal(stdout, stderr, process.returncode, status="failed", code="invalid_request", exit_code=2)
    assert not fixture["capture"].exists()


@pytest.mark.parametrize(
    "duplicate",
    [b'"schema_version":2,', b'"execution_mode":"consultation",', b'"input":"PRIVATE_DUPLICATE",'],
    ids=["schema", "mode", "source"],
)
def test_native_dispatch_still_rejects_duplicate_keys(duplicate, synthetic_native, candidate_environment):
    fixture = synthetic_native
    raw = b"{" + duplicate + encode(request_data(fixture["project"]))[1:]
    with running_cli(fixture["command"](), candidate_environment) as process:
        stdout, stderr = process.communicate(raw, timeout=5)
        result = terminal(stdout, stderr, process.returncode, status="failed", code="invalid_request", exit_code=2)
    assert result["request_id"] is None and result["request_sha256"] is None
    assert result["execution"]["project_dir"] is None
    assert not fixture["capture"].exists()


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"allowed_roots_explicit": False}, "path_not_allowed"),
        ({"configuration_scope": "bundled"}, "path_not_allowed"),
        ({"configuration_scope": "project"}, "path_not_allowed"),
        ({"project_binding": "request-selected-project"}, "invalid_config"),
        ({"allowed_roots": ()}, "path_not_allowed"),
    ],
    ids=["implicit-roots", "bundled-roots", "project-roots", "bound-registry", "empty-roots"],
)
def test_candidate_requires_independently_configured_explicit_roots(
    changes, code, synthetic_native, candidate_environment
):
    fixture = synthetic_native
    with running_cli(fixture["command"](**changes), candidate_environment) as process:
        stdout, stderr = process.communicate(encode(request_data(fixture["project"])), timeout=5)
        result = terminal(stdout, stderr, process.returncode, status="failed", code=code, exit_code=1)
    assert result["execution"]["project_dir"] == str(fixture["project"])
    assert not fixture["capture"].exists()


@pytest.mark.skipif(os.name != "posix", reason="Native process ownership requires POSIX supervision.")
def test_native_final_file_is_independent_of_tools_and_multiple_messages(synthetic_native, candidate_environment):
    fixture = synthetic_native
    data = request_data(fixture["project"], model="native-only-model")
    with running_cli(fixture["command"](), candidate_environment) as process:
        submit(process, data)
        capture = wait_for_json(fixture["capture"], process)
        assert process.poll() is None  # EOF submits the body; it does not end the owned attempt.
        assert capture["cwd"] == str(fixture["project"])
        assert capture["file_mode"] == 0o600 and capture["directory_mode"] == 0o700
        assert capture["integration_present"] is True and capture["unrelated_secret_present"] is False
        assert capture["argv"] == [
            "exec",
            "--json",
            "--color",
            "never",
            "--output-last-message",
            capture["final_path"],
            "--cd",
            str(fixture["project"]),
            "--model",
            "native-only-model",
            "-",
        ]
        fixture["release"].touch()
        stdout, stderr = collect_after_cleanup(process, capture)
        result = terminal(stdout, stderr, process.returncode, status="completed", exit_code=0, side_effects=True)
    assert result["output"] == FINAL_TEXT
    assert result["execution"]["project_dir"] == data["project_dir"]
    assert result["execution"]["requested_model"] == result["execution"]["effective_model"] == "native-only-model"
    assert result["execution"]["admission"] == "verified"
    assert result["warnings"] == [
        {"code": "native_tool_errors", "message": "Native completion included 1 recoverable tool or item errors."}
    ]


@pytest.mark.skipif(os.name != "posix", reason="Native process ownership requires POSIX supervision.")
@pytest.mark.parametrize(
    "mode,code",
    [
        ("missing-final", "protocol_error"),
        ("invalid-final", "protocol_error"),
        ("invalid-terminal", "protocol_error"),
        ("permission-needed", "permission_needed"),
    ],
)
def test_native_failure_keeps_possible_effects_and_cleans_final_resources(
    mode, code, synthetic_native, candidate_environment
):
    fixture = synthetic_native
    fixture["control"].write_text(json.dumps({"mode": mode, "final_text": FINAL_TEXT}))
    with running_cli(fixture["command"](), candidate_environment) as process:
        submit(process, request_data(fixture["project"]))
        capture = wait_for_json(fixture["capture"], process)
        fixture["release"].touch()
        stdout, stderr = collect_after_cleanup(process, capture)
        result = terminal(
            stdout, stderr, process.returncode, status="failed", code=code, exit_code=1, side_effects=True
        )
    assert result["execution"]["admission"] == "verified"


@pytest.mark.skipif(os.name != "posix", reason="Native signal cleanup requires POSIX process groups.")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_native_signals_clean_processes_and_file_before_cancellation_result(
    sig, synthetic_native, candidate_environment
):
    fixture = synthetic_native
    with running_cli(fixture["command"](), candidate_environment) as process:
        submit(process, request_data(fixture["project"]))
        capture = wait_for_json(fixture["capture"], process)
        process.send_signal(sig)
        stdout, stderr = collect_after_cleanup(process, capture)
        result = terminal(
            stdout, stderr, process.returncode, status="cancelled", code="cancelled", exit_code=130, side_effects=True
        )
    assert result["request_id"] == REQUEST_ID
    assert wait_for_json(fixture["stopped"]) == {"final_file_survived": True}


@pytest.mark.skipif(os.name != "posix", reason="Native parent-loss cleanup requires POSIX process groups.")
def test_original_parent_loss_cleans_native_tree_and_final_file(synthetic_native, candidate_environment, tmp_path):
    fixture = synthetic_native
    output = tmp_path / "orphan-native-result.json"
    errors = tmp_path / "orphan-native-stderr"
    supervisor = textwrap.dedent(f"""
        import subprocess
        import time
        with open({str(output)!r}, 'wb') as output, open({str(errors)!r}, 'wb') as errors:
            child = subprocess.Popen({fixture['command']()!r}, stdin=subprocess.PIPE, stdout=output, stderr=errors)
            child.stdin.write({encode(request_data(fixture['project']))!r})
            child.stdin.close()
            time.sleep(60)
        """)
    with running_cli([sys.executable, "-c", supervisor], candidate_environment) as parent:
        capture = wait_for_json(fixture["capture"], parent)
        parent.terminate()
        parent.wait(timeout=3)
        assert wait_for_json(fixture["exit_record"]) == 130
        terminal(
            output.read_bytes(),
            errors.read_bytes(),
            130,
            status="cancelled",
            code="cancelled",
            exit_code=130,
            side_effects=True,
        )
        assert_cleaned(capture)
    assert wait_for_json(fixture["stopped"]) == {"final_file_survived": True}


@pytest.mark.skipif(os.name != "posix", reason="Native result pipes require POSIX process groups.")
def test_broken_result_pipe_still_cleans_owned_native_resources(synthetic_native, candidate_environment):
    fixture = synthetic_native
    with running_cli(fixture["command"](), candidate_environment) as process:
        submit(process, request_data(fixture["project"]))
        capture = wait_for_json(fixture["capture"], process)
        process.stdout.close()
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=8)
        stderr = process.stderr.read()
        assert process.returncode == 1, stderr.decode(errors="replace")
        assert b"PRIVATE" not in stderr
        assert_cleaned(capture)
    assert wait_for_json(fixture["stopped"]) == {"final_file_survived": True}


@pytest.mark.skipif(os.name != "posix", reason="Native intake uses POSIX file descriptors.")
def test_open_native_request_without_eof_has_finite_intake_and_no_authority(synthetic_native, candidate_environment):
    fixture = synthetic_native
    with running_cli(fixture["command"](), candidate_environment) as process:
        process.stdin.write(encode(request_data(fixture["project"])))
        process.stdin.flush()
        process.wait(timeout=12)
        result = terminal(
            process.stdout.read(),
            process.stderr.read(),
            process.returncode,
            schema=1,
            status="timed_out",
            code="timeout",
            exit_code=124,
        )
    assert result["request_id"] is None and result["request_sha256"] is None
    assert not fixture["capture"].exists()
    assert list(Path(candidate_environment["TMPDIR"]).iterdir()) == []
