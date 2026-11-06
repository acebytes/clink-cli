"""Verify the closed console-script declaration in synthetic distribution archives."""

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import verify_distribution as verifier

ROOT = Path(__file__).resolve().parent.parent


def archive(tmp_path, kind, scripts, *, resource_mutation=None):
    members = {name: (ROOT / name).read_bytes() for name in verifier.REQUIRED_RESOURCES}
    if resource_mutation is not None:
        resource_mutation(members)
    members["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    metadata = "[console_scripts]\n" + "".join(f"{name} = {target}\n" for name, target in scripts.items())
    members["clink_cli.dist-info/entry_points.txt"] = metadata.encode()
    if kind == "wheel":
        path = tmp_path / "synthetic.whl"
        with zipfile.ZipFile(path, "w") as output:
            for name, content in members.items():
                output.writestr(name, content)
    else:
        path = tmp_path / "synthetic.tar.gz"
        with tarfile.open(path, "w:gz") as output:
            for name, content in members.items():
                info = tarfile.TarInfo("synthetic/" + name)
                info.size = len(content)
                output.addfile(info, io.BytesIO(content))
    return path


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_distribution_accepts_both_declared_entrypoints_and_reports_them(tmp_path, kind):
    result = verifier.verify(archive(tmp_path, kind, {"clink-mcp-server": "server:run", "clink": "clink.cli:main"}))
    assert result["entrypoints"] == {"clink-mcp-server": "server:run", "clink": "clink.cli:main"}
    assert result["native_cli_calls"] == 0
    assert result["claude_native_generation_admission_sha256"] == verifier.CLAUDE_NATIVE_GENERATION_ADMISSION_SHA256
    assert result["grok_native_generation_admission_sha256"] == verifier.GROK_NATIVE_GENERATION_ADMISSION_SHA256
    assert "agy_native_generation_admission_sha256" not in result
    assert "clink/resources/native-generation-agy-1.4.12-admission.json" not in verifier.REQUIRED_RESOURCES


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "scripts",
    [
        {"clink-mcp-server": "server:run"},
        {"clink": "clink.cli:main"},
        {"clink-mcp-server": "server:run", "clink": "wrong:main"},
        {"clink-mcp-server": "server:run", "clink": "clink.cli:main", "unexpected": "other:main"},
    ],
    ids=["missing-generation", "missing-mcp", "wrong-target", "unexpected-entrypoint"],
)
def test_distribution_rejects_missing_changed_or_extra_entrypoints(tmp_path, kind, scripts):
    with pytest.raises(ValueError, match="Unexpected console entrypoint"):
        verifier.verify(archive(tmp_path, kind, scripts))


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("change", ["missing", "tampered"])
@pytest.mark.parametrize(
    "resource",
    [
        verifier.NATIVE_GENERATION_ADMISSION,
        verifier.CLAUDE_NATIVE_GENERATION_ADMISSION,
        verifier.GROK_NATIVE_GENERATION_ADMISSION,
    ],
)
def test_native_generation_admission_must_be_present_and_match_its_production_pin(tmp_path, kind, change, resource):
    def mutate(members):
        if change == "missing":
            members.pop(resource)
        else:
            members[resource] += b"\n"

    artifact = archive(tmp_path, kind, verifier.CONSOLE_SCRIPTS, resource_mutation=mutate)
    with pytest.raises(ValueError, match="Missing resource|[Nn]ative generation admission hash mismatch"):
        verifier.verify(artifact)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("change", ["missing", "tampered"])
def test_generation_catalog_must_match_its_production_pin(tmp_path, kind, change):
    def mutate(members):
        if change == "missing":
            members.pop(verifier.GENERATION_CATALOG)
        else:
            members[verifier.GENERATION_CATALOG] += b"\n"

    artifact = archive(tmp_path, kind, verifier.CONSOLE_SCRIPTS, resource_mutation=mutate)
    with pytest.raises(ValueError, match="Missing resource|generation catalog hash mismatch"):
        verifier.verify(artifact)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "name", ["../escaped.txt", "/absolute.txt", "folder/../../escaped.txt", "C:/absolute.txt", "folder\\file.txt"]
)
def test_distribution_rejects_unsafe_archive_paths(tmp_path, kind, name):
    artifact = archive(
        tmp_path, kind, verifier.CONSOLE_SCRIPTS, resource_mutation=lambda members: members.update({name: b"x"})
    )
    with pytest.raises(ValueError, match="Unsafe archive member path"):
        verifier.verify(artifact)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_distribution_rejects_duplicate_archive_members(tmp_path, kind):
    artifact = archive(tmp_path, kind, verifier.CONSOLE_SCRIPTS)
    if kind == "wheel":
        with zipfile.ZipFile(artifact, "a") as output, pytest.warns(UserWarning, match="Duplicate"):
            output.writestr(verifier.CATALOG, b"duplicate")
    else:
        with tarfile.open(artifact) as source:
            entries = [(entry, source.extractfile(entry).read()) for entry in source]
        with tarfile.open(artifact, "w:gz") as output:
            for entry, content in entries + [entries[0]]:
                output.addfile(entry, io.BytesIO(content))
    with pytest.raises(ValueError, match="Duplicate archive member"):
        verifier.verify(artifact)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_distribution_rejects_links(tmp_path, kind):
    artifact = archive(tmp_path, kind, verifier.CONSOLE_SCRIPTS)
    if kind == "wheel":
        with zipfile.ZipFile(artifact, "a") as output:
            info = zipfile.ZipInfo("linked.txt")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(info, "outside.txt")
    else:
        with tarfile.open(artifact) as source:
            entries = [(entry, source.extractfile(entry).read()) for entry in source]
        with tarfile.open(artifact, "w:gz") as output:
            for entry, content in entries:
                output.addfile(entry, io.BytesIO(content))
            info = tarfile.TarInfo("synthetic/linked.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside.txt"
            output.addfile(info)
    with pytest.raises(ValueError, match="links and special files"):
        verifier.verify(artifact)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "name", [".env", "clink/.env.local", "logs/runtime.log", ".flow/spec.md", "auth.json", "local.key"]
)
def test_distribution_rejects_private_files(tmp_path, kind, name):
    artifact = archive(
        tmp_path, kind, verifier.CONSOLE_SCRIPTS, resource_mutation=lambda members: members.update({name: b"private"})
    )
    with pytest.raises(ValueError, match="Forbidden private"):
        verifier.verify(artifact)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("content", [b"-----BEGIN PRIVATE KEY-----", b"ghp_" + b"a" * 36, b"sk-proj-" + b"x" * 64])
def test_distribution_detects_credential_shapes_without_reporting_values(tmp_path, kind, content):
    artifact = archive(
        tmp_path,
        kind,
        verifier.CONSOLE_SCRIPTS,
        resource_mutation=lambda members: members.update({"secret.txt": content}),
    )
    with pytest.raises(ValueError, match="Possible credential material") as error:
        verifier.verify(artifact)
    assert content.decode() not in str(error.value)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("location", ["name", "content"])
def test_distribution_rejects_configurable_private_literals_without_echoing_them(tmp_path, kind, location):
    literal = "private-fixture-identifier"
    name, content = (literal + ".txt", b"fixture") if location == "name" else ("fixture.txt", literal.upper().encode())
    artifact = archive(
        tmp_path, kind, verifier.CONSOLE_SCRIPTS, resource_mutation=lambda members: members.update({name: content})
    )
    with pytest.raises(ValueError, match="Forbidden release text") as error:
        verifier.verify(artifact, forbidden_text=[literal])
    assert literal not in str(error.value)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_distribution_bounds_member_reads_before_decompression(tmp_path, monkeypatch, kind):
    artifact = archive(tmp_path, kind, verifier.CONSOLE_SCRIPTS)
    monkeypatch.setattr(verifier, "MAX_MEMBER_BYTES", 8)
    with pytest.raises(ValueError, match="size exceeds"):
        verifier.verify(artifact)
