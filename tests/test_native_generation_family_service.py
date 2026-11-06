"""Real shared-pool supervision of synthetic schema-three native defaults."""

import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import clink.native_generation_families as families
from clink.generation_contracts import canonical_json, sha256
from clink.native_generation import NativeGenerationService
from clink.native_generation_contracts import parse_native_request
from clink.native_generation_policy import NativeGenerationAdmission
from clink.policy import executable_digest
from clink.runner import QUEUE_LIMIT, ProcessPool
from tests.test_native_generation_v3 import wire


@pytest.mark.asyncio
@pytest.mark.parametrize("cli", ["grok", "agy"])
@pytest.mark.parametrize(
    "outcome", ["completed", "nonzero", "malformed", "overflow", "cancelled", "queue_full", "timeout"]
)
async def test_native_default_transport_and_terminal_ownership(tmp_path, monkeypatch, cli, outcome):
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    marker = tmp_path / "started.json"
    executable = tmp_path / "synthetic-native"
    final = "  final café 🦉\r\n" if outcome != "overflow" else "x" * 131073
    records = (
        [{"text": final, "sessionId": "66f96fc9-969d-4c0e-ab92-e355fa0e7588", "stopReason": "end_turn"}]
        if cli == "grok"
        else [
            {"event": "init", "conversation_id": "synthetic-1", "init": {"model": "unproven-echo"}},
            {"event": "result", "result": {"conversation_id": "synthetic-1", "status": "SUCCESS", "response": final}},
        ]
    )
    output = b"\xff" if outcome == "malformed" else ("\n".join(json.dumps(row) for row in records) + "\n").encode()
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,sys,time\nfrom pathlib import Path\n"
        "raw=sys.stdin.buffer.read()\n"
        "assert '--model' not in sys.argv\n"
        + (
            "assert raw == b''\n"
            "p=Path(sys.argv[sys.argv.index('--prompt-file')+1])\n"
            "assert p.stat().st_mode & 0o777 == 0o600\n"
            "assert p.parent.stat().st_mode & 0o777 == 0o700\n"
            "assert b'PRIVATE' in p.read_bytes()\n"
            if cli == "grok"
            else "assert len(raw.splitlines()) == 1\nassert json.loads(raw)['event'] == 'user'\n"
        )
        + f"Path({str(marker)!r}).write_text(json.dumps(sys.argv))\n"
        + ("time.sleep(30)\n" if outcome in {"cancelled", "timeout"} else "")
        + f"sys.stdout.buffer.write({output!r})\nsys.stdout.buffer.flush()\n"
        + ("sys.exit(9)\n" if outcome == "nonzero" else "")
    )
    executable.chmod(0o700)
    evidence = tmp_path / "candidate.json"
    evidence.write_text('{"synthetic":true}')
    candidate = NativeGenerationAdmission(
        version="synthetic",
        verified=True,
        executable_sha256=executable_digest(str(executable)),
        recipe_sha256=families.family_generation_recipe_digest(cli),
        parser_sha256=families.family_generation_parser_digest(cli),
        evidence_path=str(evidence),
        evidence_sha256=sha256(evidence.read_bytes()),
    )
    client = SimpleNamespace(
        adapter=cli,
        executable=(str(executable),),
        config_args=(),
        env={},
        allowed_roots=(tmp_path,),
        allowed_roots_explicit=True,
        configuration_scope="consumer",
        generation_native=None,
    )
    pool = ProcessPool(1)
    if outcome == "queue_full":
        pool.running = 1
        pool._waiters.extend(asyncio.get_running_loop().create_future() for _ in range(QUEUE_LIMIT))
    service = NativeGenerationService(
        SimpleNamespace(project_dir=None, get_client=lambda _name: client), pool, family_admissions={cli: candidate}
    )
    private = []
    prepare = families.prepare_family_generation_launch

    @contextmanager
    def track(*args):
        with prepare(*args) as launch:
            if cli == "grok":
                private.append(Path(launch.command[2]))
            yield launch

    monkeypatch.setattr(families, "prepare_family_generation_launch", track)
    request = parse_native_request(
        canonical_json(wire(tmp_path, cli, instructions="PRIVATE instruction", timeout_seconds=1))
    )
    try:
        attempt = asyncio.create_task(service.run(request))
        if outcome == "cancelled":
            for _ in range(300):
                if marker.exists():
                    break
                await asyncio.sleep(0.01)
            assert marker.exists()
            assert all(path.exists() for path in private)
            attempt.cancel()
        result = await attempt
        assert result["schema_version"] == 3
        assert result["execution"]["requested_model"] is None
        assert result["execution"]["effective_model"] is None
        assert result["execution"]["actual_model"] is None
        assert result["execution"]["model_selection"] == {"kind": "native_default"}
        if outcome == "completed":
            assert result["status"] == "completed" and result["output"] == final
        else:
            expected = {
                "nonzero": "nonzero_exit",
                "malformed": "protocol_error",
                "overflow": "output_limit",
                "cancelled": "cancelled",
                "queue_full": "queue_full",
                "timeout": "timeout",
            }[outcome]
            assert result["error"]["code"] == expected and result["output"] is None
        assert all(not path.parent.exists() for path in private)
        assert not pool._children
        assert marker.exists() is (outcome != "queue_full")
        if outcome == "queue_full":
            assert result["execution"]["side_effects_possible"] is False
        else:
            assert result["execution"]["side_effects_possible"] is True
            assert pool.running == 0
    finally:
        if outcome == "queue_full":
            pool.running = 0
            for future in pool._waiters:
                future.cancel()
            pool._waiters.clear()
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("cli", ["grok", "agy"])
async def test_explicitly_unverified_family_admission_refuses_before_launch(tmp_path, monkeypatch, cli):
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *_args, **_kwargs: pytest.fail("unadmitted process"))
    request = parse_native_request(canonical_json(wire(tmp_path, cli)))
    service = NativeGenerationService(family_admissions={cli: NativeGenerationAdmission()})
    try:
        result = await service.run(request)
        assert result["error"]["code"] == "policy_unverified"
        assert result["execution"]["side_effects_possible"] is False
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_default_grok_generation_admission_still_requires_the_exact_executable(tmp_path, monkeypatch):
    cli = "grok"
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *_args, **_kwargs: pytest.fail("unadmitted process"))
    executable = tmp_path / "unrelated-native"
    executable.write_bytes(b"synthetic unrelated executable; never run")
    executable.chmod(0o700)
    client = SimpleNamespace(
        adapter=cli,
        executable=(str(executable),),
        config_args=(),
        env={},
        allowed_roots=(tmp_path,),
        allowed_roots_explicit=True,
        configuration_scope="consumer",
        generation_native=None,
    )
    request = parse_native_request(canonical_json(wire(tmp_path, cli)))
    service = NativeGenerationService(SimpleNamespace(project_dir=None, get_client=lambda _name: client))
    try:
        expected = families.DEFAULT_GROK_NATIVE_ADMISSION
        assert service._admission_for(cli) is expected
        assert expected.verified is True
        result = await service.run(request)
        assert result["error"]["code"] == "policy_unverified"
        assert result["output"] is None
        assert result["execution"]["side_effects_possible"] is False
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_default_agy_generation_refuses_before_configuration_binary_identity_or_launch(tmp_path, monkeypatch):
    monkeypatch.delenv("CLINK_DELEGATION_DEPTH", raising=False)
    binary = Mock(side_effect=AssertionError("Unverified Agy must refuse before binary identity"))
    spawn = Mock(side_effect=AssertionError("Unverified Agy must refuse before process creation"))
    client = Mock(side_effect=AssertionError("Unverified Agy must refuse before configuration lookup"))
    monkeypatch.setattr("clink.native_generation.executable_digest", binary)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    service = NativeGenerationService(SimpleNamespace(project_dir=None, get_client=client))
    request = parse_native_request(canonical_json(wire(tmp_path, "agy")))
    try:
        assert service._admission_for("agy") == NativeGenerationAdmission()
        result = await service.run(request)
        assert result["error"]["code"] == "policy_unverified" and result["output"] is None
        assert result["execution"]["side_effects_possible"] is False
        client.assert_not_called()
        binary.assert_not_called()
        spawn.assert_not_called()
    finally:
        await service.shutdown()
