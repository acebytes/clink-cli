"""Exercise explicit native transports through synthetic executables and the real pool."""

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import clink.native as native
import clink.native_adapters as adapters
from clink.backend import CLIBackend
from clink.contracts import AdapterPolicy, BackendError
from clink.models import ResolvedCLIClient, ResolvedCLIRole, ResolvedNativeTask
from clink.native import NativeRuntime
from clink.native_adapters import get_native_adapter, prepare_native_launch
from clink.native_policy import native_environment_names, native_recipe, native_recipe_digest
from clink.parsers.base import ParserError
from clink.policy import executable_digest, recipe_digest

SESSION = "a9dde8eb-e027-471d-b51c-d622ba5a1597"
OTHER_SESSION = "da856cb0-08f6-4a08-9127-3966ee873fbf"


@pytest.fixture
def adapter_runtime(tmp_path, monkeypatch):
    """Create only synthetic admission; actual installed CLI policies are untouched."""
    created = []
    original_mkdtemp = adapters.tempfile.mkdtemp
    inputs_root = tmp_path / "private-inputs"
    inputs_root.mkdir()

    def private_directory(suffix=None, prefix=None, dir=None):
        result = original_mkdtemp(suffix=suffix, prefix=prefix, dir=str(inputs_root))
        created.append(Path(result))
        return result

    monkeypatch.setattr(adapters.tempfile, "mkdtemp", private_directory)
    monkeypatch.setattr(native, "__file__", str(tmp_path / "clink" / "native.py"))
    monkeypatch.setattr(native, "_VERIFIED_NATIVE_PLATFORMS", {})
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    evidence = tmp_path / "docs" / "verification" / "native.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"scope":"synthetic_fixture"}')

    def factory(name):
        monkeypatch.setenv("CLINK_DEFAULT_CLI", name)
        project = tmp_path / name
        project.mkdir()
        executable = project / "fake-cli"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json,os,pathlib,stat,sys,time\n"
            f"adapter={name!r}\nsession={SESSION!r}\nother_session={OTHER_SESSION!r}\n"
            "args=sys.argv[1:]\nstdin=sys.stdin.read()\n"
            "capture={'args':args,'stdin':stdin}\n"
            "if '--prompt-file' in args:\n"
            " path=pathlib.Path(args[args.index('--prompt-file')+1])\n"
            " prompt=path.read_text()\n"
            " capture.update(prompt_path=str(path),file_mode=stat.S_IMODE(path.stat().st_mode),directory_mode=stat.S_IMODE(path.parent.stat().st_mode))\n"
            "elif adapter=='agy':\n"
            " envelope=json.loads(stdin)\n"
            " assert envelope['event']=='user'\n"
            " prompt=envelope['message']['content']\n"
            "else:\n"
            " prompt=stdin\n"
            "capture.update(prompt=prompt,grok_home=os.environ.get('GROK_HOME'),token=os.environ.get('SYNTHETIC_TOKEN'))\n"
            "pathlib.Path('capture.json').write_text(json.dumps(capture))\n"
            "mode=os.environ.get('SYNTHETIC_MODE','success')\n"
            "if mode=='sleep':time.sleep(60)\n"
            "if mode=='mismatch':session=other_session\n"
            "if mode=='malformed':\n"
            " print('private invalid output')\n sys.exit(0)\n"
            "if adapter=='grok':\n"
            " print(json.dumps({'sessionId':session,'stopReason':'end_turn','text':'done'}))\n"
            "elif adapter=='agy':\n"
            " print(json.dumps({'event':'init','conversation_id':session,'init':{'cwd':os.getcwd()}}))\n"
            " print(json.dumps({'event':'result','result':{'conversation_id':session,'status':'SUCCESS','response':'done'}}))\n"
            "elif adapter=='codex':\n"
            " print(json.dumps({'type':'thread.started','thread_id':session}))\n"
            " print(json.dumps({'type':'item.completed','item':{'id':'message-1','type':'agent_message','text':'done'}}))\n"
            " print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))\n"
            "else:\n"
            " print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':session,'result':'done'}))\n"
            "if mode=='nonzero':sys.exit(2)\n"
        )
        executable.chmod(0o755)
        role = ResolvedCLIRole(name="default", prompt_path=project / "role.txt", prompt_text="Native role")
        client = ResolvedCLIClient(
            name=name,
            adapter=name,
            executable=(str(executable),),
            working_dir=project,
            allowed_roots=(project,),
            timeout_seconds=5,
            parser=f"{name}_json",
            roles={"default": role},
            default_model="legacy-advisory-model",
            native_task=ResolvedNativeTask(
                enabled=True,
                inherit_env=("SYNTHETIC_MODE", "SYNTHETIC_TOKEN"),
                roles={"default": role},
            ),
        )
        registry = SimpleNamespace(get_client={name: client}.__getitem__, list_clients=lambda: [name])
        policy = AdapterPolicy(
            name,
            verified=True,
            version="synthetic-fixture",
            verification_status="synthetic_fixture",
            file_references=True,
            executable_sha256=executable_digest(str(executable)),
            recipe_sha256=native_recipe_digest(name),
            evidence="docs/verification/native.json",
            evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(native, "_VERIFIED_NATIVE_POLICIES", {name: policy})
        runtime = NativeRuntime(CLIBackend(registry))
        return runtime, runtime.prepare(name, None, None, str(project)), created

    return factory


def assert_private_inputs_removed(created):
    assert created
    assert all(not directory.exists() for directory in created)


async def wait_for_queue(runtime):
    for _ in range(500):
        if runtime.pool._waiters:
            return
        await asyncio.sleep(0.002)
    pytest.fail("Synthetic CLI did not reach the process queue")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["claude", "grok", "agy", "codex"])
async def test_native_transports_and_exact_resume_keep_task_text_out_of_argv(
    adapter_runtime, monkeypatch, caplog, name
):
    runtime, context, created = adapter_runtime(name)
    monkeypatch.setenv("SYNTHETIC_TOKEN", "secret-value")
    monkeypatch.setenv("GROK_HOME", "/synthetic/grok-config")
    assert context.target.effective_model is None
    result = await runtime.run(context, "private assigned task", attempt_id=f"first-{name}")
    assert result.state == "completed"
    assert result.session_id == SESSION
    capture = json.loads((context.target.working_directory / "capture.json").read_text())
    assert "private assigned task" in capture["prompt"]
    assert "private assigned task" not in json.dumps(capture["args"])
    assert "--model" not in capture["args"]
    assert capture["token"] == "secret-value"
    if name == "grok":
        assert capture["stdin"] == ""
        assert capture["file_mode"] == 0o600 and capture["directory_mode"] == 0o700
        assert capture["grok_home"] == "/synthetic/grok-config"
        assert_private_inputs_removed(created)
    else:
        assert capture["grok_home"] is None
        assert not created
        if name == "agy":
            assert len(capture["stdin"].splitlines()) == 1
            assert json.loads(capture["stdin"])["message"]["content"] == capture["prompt"]
        elif name == "codex":
            assert capture["args"] == [
                "exec",
                "--json",
                "--color",
                "never",
                "--cd",
                str(context.target.working_directory),
                "-",
            ]
        else:
            assert capture["stdin"] == capture["prompt"]
    result = await runtime.run(context, "new followup only", SESSION, attempt_id=f"followup-{name}")
    assert result.state == "completed" and result.session_id == SESSION
    capture = json.loads((context.target.working_directory / "capture.json").read_text())
    assert "private assigned task" not in capture["prompt"]
    assert "new followup only" in capture["prompt"]
    if name == "agy":
        assert capture["args"][-2:] == ["--conversation", SESSION]
    elif name == "codex":
        assert capture["args"] == [
            "exec",
            "--cd",
            str(context.target.working_directory),
            "resume",
            "--json",
            SESSION,
            "-",
        ]
    else:
        assert capture["args"][-2:] == ["--resume", SESSION]
    for private in ("private assigned task", "new followup only", "secret-value", SESSION):
        assert private not in caplog.text
        assert private not in json.dumps(result.metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["grok", "agy", "codex"])
@pytest.mark.parametrize(
    "mode,state,code", [("nonzero", "failed", "nonzero_exit"), ("mismatch", "uncertain", "native_session_mismatch")]
)
async def test_adapter_outcomes_cannot_override_exit_or_private_session(
    adapter_runtime, monkeypatch, name, mode, state, code
):
    runtime, context, created = adapter_runtime(name)
    monkeypatch.setenv("SYNTHETIC_MODE", mode)
    result = await runtime.run(context, "task", SESSION, attempt_id="attempt")
    assert result.state == state
    assert result.metadata["error_code"] == code
    if mode == "mismatch":
        assert result.session_id is None
    if name == "grok":
        assert_private_inputs_removed(created)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["queued_cancel", "queue_timeout", "changed_admission", "changed_prompt"])
async def test_grok_private_file_is_removed_for_queued_failures(adapter_runtime, monkeypatch, failure):
    runtime, context, created = adapter_runtime("grok")
    if failure == "queue_timeout":
        monkeypatch.setattr("clink.runner.QUEUE_SECONDS", 0.02)
    await runtime.pool._acquire(time.monotonic() + 5)
    await runtime.pool._acquire(time.monotonic() + 5)
    task = asyncio.create_task(runtime.run(context, "private task", attempt_id=failure))
    try:
        await wait_for_queue(runtime)
        assert created and (created[0] / "prompt.txt").is_file()
        if failure == "queued_cancel":
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if failure == "changed_admission":
                monkeypatch.setattr(native, "_VERIFIED_NATIVE_POLICIES", {})
                runtime.pool._release()
            elif failure == "changed_prompt":
                (created[0] / "prompt.txt").write_text("different input")
                runtime.pool._release()
            with pytest.raises(BackendError) as caught:
                await task
            expected = {
                "queue_timeout": "queue_timeout",
                "changed_admission": "policy_unverified",
                "changed_prompt": "invalid_config",
            }
            assert caught.value.code == expected[failure]
        assert_private_inputs_removed(created)
        assert not (context.target.working_directory / "capture.json").exists()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        while runtime.pool.running:
            runtime.pool._release()


@pytest.mark.asyncio
async def test_grok_running_cancellation_keeps_file_until_child_cleanup(adapter_runtime, monkeypatch):
    runtime, context, created = adapter_runtime("grok")
    monkeypatch.setenv("SYNTHETIC_MODE", "sleep")
    task = asyncio.create_task(runtime.run(context, "private task", attempt_id="running-cancel"))
    capture_path = context.target.working_directory / "capture.json"
    for _ in range(500):
        if capture_path.exists():
            break
        await asyncio.sleep(0.002)
    assert capture_path.exists()
    assert created and (created[0] / "prompt.txt").is_file()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.pool.running == 0 and not runtime.pool._children
    assert_private_inputs_removed(created)


@pytest.mark.asyncio
async def test_grok_private_file_cleanup_after_parse_failure_and_binary_rejection(adapter_runtime, monkeypatch):
    runtime, context, created = adapter_runtime("grok")
    monkeypatch.setenv("SYNTHETIC_MODE", "malformed")
    result = await runtime.run(context, "task", attempt_id="invalid-output")
    assert result.state == "uncertain"
    assert_private_inputs_removed(created)
    executable = Path(context.target.client.executable[0])
    executable.write_text(executable.read_text() + "\n")
    with pytest.raises(BackendError) as caught:
        await runtime.run(context, "task", attempt_id="changed-binary")
    assert caught.value.code == "policy_unverified"
    assert len(created) == 2
    assert_private_inputs_removed(created)


@pytest.mark.asyncio
async def test_grok_spawn_failure_cleans_private_file_without_exposing_os_diagnostics(adapter_runtime, monkeypatch):
    runtime, context, created = adapter_runtime("grok")

    async def failed_spawn(*args, **kwargs):
        raise PermissionError("private native launch diagnostics")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failed_spawn)
    with pytest.raises(BackendError) as caught:
        await runtime.run(context, "private task", attempt_id="spawn-failure")
    assert caught.value.code == "invalid_config"
    assert "private native launch diagnostics" not in str(caught.value)
    assert runtime.pool.running == 0 and not runtime.pool._children
    assert_private_inputs_removed(created)


@pytest.mark.parametrize("name", ["grok", "codex"])
def test_native_session_titles_and_options_cannot_be_used_as_resume_ids(adapter_runtime, name):
    runtime, context, created = adapter_runtime(name)
    for invalid in ("friendly-title", "--latest", "../session", "invalid\nidentity"):
        with pytest.raises(ParserError):
            with prepare_native_launch(get_native_adapter(name), context, b"task", invalid):
                pytest.fail("Invalid session was admitted")
    assert not created


def test_private_prompt_file_limit_is_checked_before_materialization(adapter_runtime):
    runtime, context, created = adapter_runtime("grok")
    with pytest.raises(BackendError) as caught:
        with prepare_native_launch(get_native_adapter("grok"), context, b"x" * (1024 * 1024 + 1), None):
            pytest.fail("Oversized private input was admitted")
    assert caught.value.code == "input_limit"
    assert not created


def test_new_native_profiles_do_not_inherit_claude_admission(adapter_runtime, monkeypatch):
    runtime, context, created = adapter_runtime("grok")
    monkeypatch.setattr(native, "_VERIFIED_NATIVE_POLICIES", {})
    assert runtime.describe()[0]["available"] is False
    with pytest.raises(BackendError) as caught:
        runtime.prepare("grok", None, None, str(context.target.working_directory))
    assert caught.value.code == "policy_unverified"
    assert not created


def test_recipe_and_environment_boundaries_are_separate():
    assert native_recipe_digest("claude") == "de885baaadfdf5810321e32992c7e961f3a360dda90b903ab98ffc243564885e"
    assert recipe_digest("codex") == "4bf9a7fe0491e12965d5c36dd7ccc9d197dd271cdf57df378c68fc1b9e0dae16"
    assert "GROK_HOME" in native_environment_names("grok")
    assert "GROK_HOME" not in native_environment_names("claude")
    for name in ("grok", "agy", "codex"):
        assert native_recipe_digest(name) != native_recipe_digest("claude")
        recipe = native_recipe(name)
        assert "--dangerously-skip-permissions" not in recipe["argv"]
        assert "--ignore-user-config" not in recipe["argv"]
        assert "--ignore-rules" not in recipe["argv"]
