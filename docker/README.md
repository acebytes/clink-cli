# Container support

The base image runs the provider-free MCP server on stdio as a non-root user. Startup, discovery and local tools need no API keys, CLI installation or login. No CLI agents or authentication files are installed or mounted. Delegated CLI execution inside this image has not been admitted; use a separately admitted host installation for CLI calls. The version 0.2.2 image is built locally; no registry publication is assumed.

Build and attach from the repository root:

```sh
docker compose build clink-mcp
docker compose run --rm --no-deps -T clink-mcp
```

A client must own the attached stdin/stdout session. Do not allocate a TTY or start this stdio server with `up -d`. `docker/scripts/build.sh` and `build.ps1` build the image; the retained `deploy.sh` and `deploy.ps1` names now attach the same stdio command. They do not source `.env`, require provider keys, stop existing services, retry deployment or configure a client.

Example client configuration after building:

```json
{
  "mcpServers": {
    "clink": {
      "command": "docker",
      "args": ["compose", "--project-directory", "/absolute/path/to/clink-mcp-server", "run", "--rm", "--no-deps", "-T", "clink-mcp"]
    }
  }
}
```

Compose disables networking, drops capabilities, prevents privilege escalation and uses a read-only filesystem plus temporary scratch space. Conversations are in memory and disappear when the session ends. Logs go to stderr when the installed package directory is read-only; no host logs or credentials are mounted automatically. The `.dockerignore` allowlist excludes `.env`, Git data, test evidence, provider catalogs and local state from the build context.

The health check verifies the PID 1 command, provider-free Python imports and CLI manifest validity. It never reads MCP stdin, calls a model, checks API keys or accesses the network. It does not prove MCP responsiveness or native CLI readiness. Malformed manifests fail; absent executables or unknown login status do not prevent local operation.

The fork removes the inherited release/publish automation. Manually dispatched CI performs checks and local builds with read-only repository permissions; it does not publish packages/images, deploy, write PR comments or change releases. Real CLI policy/smoke probes are separate opt-in host operations documented in [release evidence](../docs/release-evidence.md); they may consume subscription usage and are not an offline unit test.
