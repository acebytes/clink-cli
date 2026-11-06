"""Tool-to-runner integration using a synthetic executable, never a native login."""

import json
import sys
from types import SimpleNamespace

import pytest

import clink.backend as backend_module
from clink.backend import CLIBackend
from clink.contracts import AdapterPolicy
from clink.policy import executable_digest, recipe_digest
from tools.clink import CLinkTool


@pytest.fixture
def synthetic_cli(tmp_path, monkeypatch):
    capture = tmp_path / "invocations.jsonl"
    executable = tmp_path / "synthetic-cli"
    executable.write_text(
        "#!"
        + sys.executable
        + "\nimport json, sys\nfrom pathlib import Path\n"
        + "record = {'argv': sys.argv[1:], 'input': sys.stdin.read()}\n"
        + "with Path("
        + repr(str(capture))
        + ").open('a', encoding='utf-8') as stream:\n"
        + "    stream.write(json.dumps(record) + '\\n')\n"
        + "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'4'}}))\n"
        + "print(json.dumps({'type':'turn.completed','model':'synthetic-reported'}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    roles = {
        name: SimpleNamespace(
            name=name, prompt_text="Subordinate fixture expertise.", role_args=(), model="role-native", description=None
        )
        for name in ("default", "codereviewer")
    }
    client = SimpleNamespace(
        name="codex",
        adapter="codex",
        runner="codex",
        executable=(str(executable),),
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        config_args=(),
        env={},
        timeout_seconds=10,
        parser="codex_jsonl",
        default_model="client-native",
        roles=roles,
        get_role=lambda name: roles[name or "default"],
    )

    def get_client(name):
        if name != "codex":
            raise KeyError(name)
        return client

    monkeypatch.setattr(backend_module, "get_env", lambda key, default=None: default)
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    monkeypatch.setattr(
        backend_module,
        "_VERIFIED_POLICIES",
        {
            "codex": AdapterPolicy(
                "codex",
                version="synthetic-fixture",
                verified=True,
                verification_status="test_fixture",
                read_scope="inline-only",
                executable_sha256=executable_digest(str(executable)),
                recipe_sha256=recipe_digest("codex"),
            ),
        },
    )
    backend = CLIBackend(SimpleNamespace(get_client=get_client, list_clients=lambda: ["codex"]))
    monkeypatch.setattr(backend_module, "get_backend", lambda: backend)
    return backend, capture


@pytest.mark.asyncio
async def test_clink_shared_runner_parses_native_result_and_supplies_context_once(synthetic_cli, tmp_path):
    backend, capture = synthetic_cli
    source = tmp_path / "subject.py"
    source.write_text("SYNTHETIC_DECLARED_SOURCE", encoding="utf-8")
    try:
        payload = json.loads(
            (
                await CLinkTool().execute(
                    {
                        "prompt": "Return 2 + 2.",
                        "role": "codereviewer",
                        "model": "request-native",
                        "absolute_file_paths": [str(source)],
                    }
                )
            )[0].text
        )
        (invocation,) = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
        assert invocation["input"].count("SYNTHETIC_DECLARED_SOURCE") == 1
        assert invocation["argv"][invocation["argv"].index("--model") + 1] == "request-native"
        assert payload["content"] == "4"
        assert payload["metadata"]["backend"] == {"cli_name": "codex", "role": "codereviewer"}
        assert payload["metadata"]["requested_model"] == "request-native"
        assert payload["metadata"]["actual_model"] == "synthetic-reported"
        assert payload["metadata"]["read_scope"] == "inline-only"
        assert payload["metadata"]["warnings"][0]["code"] == "native_filesystem_access_disabled"
        assert backend.pool.running == 0
        assert not backend.pool._children
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_clink_shared_runner_prunes_old_history_after_required_input(synthetic_cli):
    backend, capture = synthetic_cli
    try:
        payload = json.loads(
            (
                await CLinkTool().execute(
                    {
                        "prompt": "Return 2 + 2.",
                        "_replay_history_turns": ("x" * (128 * 1024), "A recent retained observation."),
                        "_replay_omitted_turns": 3,
                    }
                )
            )[0].text
        )
        (invocation,) = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
        assert "Return 2 + 2." in invocation["input"]
        assert "A recent retained observation." in invocation["input"]
        assert "x" * 1024 not in invocation["input"]
        assert payload["metadata"]["history_omitted_turns"] == 4
        assert payload["metadata"]["history_retained_turns"] == 1
        assert payload["metadata"]["truncated"] is False
        assert backend.pool.running == 0
    finally:
        await backend.close()
