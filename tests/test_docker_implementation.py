"""Check the container's actual configuration; no Docker or CLI is launched."""

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def test_runtime_installs_distribution_instead_of_source_tree():
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split(" AS runtime", 1)[1]
    assert "--no-index --find-links /wheels clink-cli" in runtime
    assert "COPY ." not in dockerfile
    assert "COPY --chown=" not in runtime
    assert 'CMD ["clink-mcp-server"]' in runtime
    assert "COPY conf/cli_clients/ conf/cli_clients/" in dockerfile
    assert "providers" not in dockerfile


def test_container_runs_without_root_or_network_authentication():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "useradd --uid 10001" in dockerfile
    assert "USER clink" in dockerfile
    assert "HOME=/home/clink" in dockerfile
    assert "API_KEY" not in dockerfile
    assert "apt-get" not in dockerfile


def test_compose_preserves_stdio_and_restricts_container():
    compose = (ROOT / "docker-compose.yml").read_text()
    for setting in (
        "stdin_open: true",
        "tty: false",
        "network_mode: none",
        "read_only: true",
        "no-new-privileges:true",
        "cap_drop:",
        "- ALL",
        "/tmp:noexec,nosuid,size=100m,mode=1777",
    ):
        assert setting in compose
    for unsupported in ("API_KEY", "env_file:", "volumes:", "ports:", "restart:", "privileged:"):
        assert unsupported not in compose


def test_dockerignore_allows_only_packaging_inputs():
    patterns = (ROOT / ".dockerignore").read_text().splitlines()
    patterns = [line.strip() for line in patterns if line.strip() and not line.lstrip().startswith("#")]
    assert patterns[0] == "**"
    # Keep this test scoped to the repository's closed allowlist, rather than
    # approximating Docker's general pattern language with a partial matcher.
    assert patterns[-2:] == ["**/__pycache__/", "**/*.py[cod]"]
    assert all(pattern.startswith("!") for pattern in patterns[1:-2])
    for resource in ("!conf/cli_clients/*.json", "!systemprompts/clink/*.txt", "!LICENSE"):
        assert resource in patterns
    for pattern in patterns:
        if pattern.startswith("!"):
            assert not any(item in pattern for item in (".env", ".venv", "providers", "_models.json", "docs/", ".git"))

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for package, declarations in metadata["tool"]["setuptools"]["package-data"].items():
        for declaration in declarations:
            resource_pattern = f"{package.replace('.', '/')}/{declaration}"
            assert f"!{resource_pattern}" in patterns, f"Package data excluded from Docker context: {resource_pattern}"
            resources = list(ROOT.glob(resource_pattern))
            assert resources, f"Package data declaration matches no files: {resource_pattern}"
            for resource in resources:
                relative = resource.relative_to(ROOT)
                assert resource.is_file()
                assert "__pycache__" not in relative.parts
                assert not fnmatchcase(resource.name, "*.py[cod]")
                for parent in relative.parents:
                    if parent != Path("."):
                        assert f"!{parent.as_posix()}/" in patterns, f"Docker context excludes parent: {parent}"


@pytest.mark.parametrize("filename", ["test.yml", "docker-pr.yml"])
def test_offline_workflows_are_read_only_and_do_not_publish(filename):
    workflow = (ROOT / ".github" / "workflows" / filename).read_text()
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert not re.search(r"^  (release|schedule):", workflow, re.MULTILINE)
    if filename == "test.yml":
        assert "  pull_request:\n" in workflow
        assert "  push:\n    branches: [main]\n" in workflow
        assert not re.search(r"^\s+tags:", workflow, re.MULTILINE)
    else:
        assert not re.search(r"^  (push|pull_request):", workflow, re.MULTILINE)
    for mutation in (
        "packages: write",
        "contents: write",
        "pull-requests: write",
        "push: true",
        "docker/login",
        "gh release",
        "semantic-release",
        "--fix",
    ):
        assert mutation not in workflow


def test_workflows_have_no_release_publish_or_background_deployment():
    workflows = ROOT / ".github" / "workflows"
    assert {path.name for path in workflows.glob("*.yml")} == {"test.yml", "docker-pr.yml"}
    unit_workflow = (workflows / "test.yml").read_text()
    assert "--network none" in unit_workflow
    assert "not integration and not real_cli" in unit_workflow
    assert "black --check" in unit_workflow
    assert "isort --check-only" in unit_workflow


def test_host_client_example_is_clink_without_provider_keys():
    config = json.loads((ROOT / "claude_config_example.json").read_text())
    assert set(config["mcpServers"]) == {"clink"}
    server = config["mcpServers"]["clink"]
    assert server["command"].endswith("/clink-cli/.venv/bin/python")
    assert server["args"] == ["/path/to/clink-cli/server.py"]
    assert "env" not in server
