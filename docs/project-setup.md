# Project setup

Each repository can own its clink configuration. Project manifests override optional user manifests for the same client. A server launched with `--project` binds to that project for its lifetime, so two repositories can use different roots, roles, and model defaults without rewriting home configuration.

## Set up a project

From your existing clink checkout, after the ordinary `./run-server.sh --setup` dependency installation:

```sh
cd /Users/d/Tools/clink-mcp-server
.venv/bin/python -m clink.setup --project /absolute/path/to/your-project
```

The project must already exist. The helper creates complete manifests for all bundled clients by default. To configure only selected clients, repeat `--cli`:

```sh
.venv/bin/python -m clink.setup --project /absolute/path/to/your-project --cli agy --cli codex
```

For an installed Python package, use that installation's Python with `-m clink.setup`. No CLI login, native executable, model call, or network access is required by setup. The separate `run-server.sh --setup` command remains dependency installation only.

Generated layout:

```text
your-project/
  .clink/
    cli_clients/
      agy.json
      claude.json
      codex.json
      gemini.json
      grok.json
    prompts/
      ...copied role prompts...
  .mcp.json
```

Generated manifests use `working_dir: "../.."` and `allowed_roots: ["../.."]`, relative to `.clink/cli_clients/`. Both resolve to this repository. Prompt paths reference the copies in `.clink/prompts/`, so the clink manifests and prompts remain valid when the project moves. The helper preserves bundled native enablement and separate admission requirements; configuration never makes an unverified CLI available.

`.mcp.json` registers the `clink` stdio server with the current Python interpreter, the installed `server.py`, and an explicit absolute `--project` argument. These registration paths are machine-specific. Keep this generated registration local, or adapt it deliberately before sharing it with teammates; the portable `.clink/` files can be versioned. After moving a project or installation, update the registration paths. Setup reports a differing existing registration instead of replacing it.

Reconnect the MCP client after setup, then call `listmodels`. Check the selected client's `allowed_roots` and its separate native profile. Agy still requires explicit native `action: "start"`; omitted `action` selects its unverified consultation profile. Save useful task results before a restart, which discards server-owned task handles.

## Claude Code command

Load the small local plugin when starting Claude Code in your target project:

```sh
claude --plugin-dir /Users/d/Tools/clink-mcp-server/integrations/claude
```

Then run:

```text
/clink:setup
```

You can supply a different project directory as the command argument. The skill captures the target before switching to the installation directory and invokes the same Python helper. It does not start a model-backed connectivity check or modify user-level settings. This plugin is designed for direct loading from the checkout; it is not a standalone marketplace package and is not included as a plugin in the Python wheel.

Claude Code names plugin skills as `/plugin:skill` and supports `--plugin-dir` for session-local loading. Its project MCP configuration is `.mcp.json`. The host may request its normal project-server approval after reconnecting; setup does not change those approval settings. See the official [plugin documentation](https://code.claude.com/docs/en/plugins/create) and [MCP scope documentation](https://code.claude.com/docs/en/mcp#scope-hierarchy-and-precedence).

If an older registration named `clink` exists in Claude's local scope, it takes priority over the project entry. Inspect the active definition in `/mcp` and update that registration deliberately if needed. The clink helper does not read or rewrite `~/.claude.json`. Host-managed settings can also take precedence; the desktop Code tab has its own documented user/project precedence exception.

## Configuration precedence

Lowest to highest priority:

1. Bundled CLI manifests.
2. Legacy `~/.pal/cli_clients/`.
3. User `~/.clink/cli_clients/`.
4. `<bound-project>/.clink/cli_clients/`.
5. Explicit `CLI_CLIENTS_CONFIG_PATH`.

Each later manifest replaces the whole named client, including its roles and native profile. Generated manifests start from bundled defaults, so they replace user-level customizations for those names; they do not copy user secrets or settings. Edit the project manifest to select its roles/models or remove that project's client manifest to use the next lower source. Clients not configured in the project still use lower-priority sources. An explicit override remains highest priority and can intentionally shadow the project configuration.

The server reads project manifests only when explicitly launched with `--project`. It does not walk parent directories or load manifests from a request's `project_dir`. Existing launches without `--project` keep their previous behavior. You can bind a server manually:

```sh
/Users/d/Tools/clink-mcp-server/run-server.sh --project /absolute/path/to/your-project
```

The installed `clink-mcp-server --project /absolute/path/to/your-project` entrypoint also supports this binding. Project selection is independent of the server checkout's `.env`, logging directory, and process working directory. A different binding needs a separate process or restart, not an in-process registry switch.

## Existing files and recovery

Setup preflights its output. Identical files are left unchanged. Differing manifests, prompts, or an existing `clink` registration are conflicts; no force-overwrite option is provided. Other `.mcp.json` entries are retained when adding clink. Invalid JSON, duplicate keys, oversized configuration, and symlink destinations are rejected. Diagnostics identify conflicting paths without printing configuration values.

Each file is published atomically, with registration last. Cooperating setup processes are serialized, and detected concurrent file changes cause a conflict. Avoid editing `.mcp.json` while setup runs: an unrelated editor does not share its lock, and the final recheck and replacement are not a filesystem compare-and-swap. This is not an atomic multi-file transaction: interruption can leave some valid configuration files, and rerunning with the same inputs completes missing files. It does not roll back or delete user edits. After intentionally customizing generated files, edit them directly; rerunning setup is not a template-upgrade operation.

Risk & Mitigation: project manifests can select executables and native role guidance, so opening an arbitrary repository must not silently replace an already-running server's configuration. Explicit launch binding, existing host trust controls, and unchanged CLI admission checks preserve that boundary. Roots constrain project selection and supplied context; they are not a sandbox for all native tools. Each MCP process has its own task records and concurrency pool; multiple projects do not share a global concurrency limit.
