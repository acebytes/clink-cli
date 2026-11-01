# Getting started with clink-mcp-server

This fork preserves PAL's MCP workflows and obtains advisory analysis from installed CLI agents. The caller performs project edits. Copyright 2025 Beehive Innovations; distributed under the [Apache 2.0 license](../LICENSE).

## 1. Prepare the Python environment

Use macOS or Linux with a supported Python interpreter and its `venv`/`pip` modules. Python 3.10+ is required; Python 3.12 is the tested runtime. Delegated execution on native Windows remains unavailable.

From this checkout:

```sh
./run-server.sh --setup
```

The explicit setup step creates `.venv` if absent, installs [runtime requirements](../requirements.txt), and exits. It can access the package index to install dependencies. It does not create `.env`, read credentials into a template, register this server with clients, or alter native CLI settings. An installation error remains an error; the script does not continue with missing dependencies.

For an explicitly chosen interpreter, create the environment first:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Ordinary startup never installs or updates packages. Run `--setup` explicitly after requirements change.

## 2. Configure native CLI selection and workspace access

The default target is client `codex`, role `default`. Authenticate an admitted CLI using its native login mechanism. The server does not require provider API keys, log in for you, or copy credentials. Native authentication/cache maintenance can still write operational files.

Read [CLI configuration](cli-configuration.md) before supplying paths outside the server checkout. Bundled manifests default to the server's startup directory, and the launcher starts from the checkout. Configure `working_dir` and `allowed_roots` for the project you want to consult on. Chat's required `working_directory_absolute_path` may select a directory only within those roots; explicit context paths are checked after resolving symlinks.

Choose settings in your MCP client's environment or, optionally, copy [.env.example](../.env.example) to `.env` yourself. Native model selection belongs in a manifest's `default_model` or role `model`, or in an explicit tool request. A concrete model name is passed through to the native CLI; provider aliases are not translated.

CLI installation is separate from admission. `listmodels` reports configuration, executable presence, verification status, and capabilities without contacting a model; its authentication field remains unknown. [Codex 0.166.0 is admitted for text, explicit local images, and structured native search](verification/cli-policy/codex-0.166.0-admission-v5-search.json) when the exact tested macOS arm64 binary, restrictive recipe, and frozen catalog match their SHA-256 pins. Other builds/versions have no admission from that record. Native file/command tools are disabled; declared text context is supplied inline within the prompt limit.

The admitted snapshot accepts only its 12 exact native model IDs; unknown names and prefix/namespace aliases fail before launch. It freezes the genuine captured default as `gpt-6.1-sol`, passed explicitly when request/role/client defaults are absent. `effective_model` and `default_origin: "pinned_native_catalog"` describe that selection; they do not establish `actual_model`, which remains unknown unless the CLI reports it. Only `gpt-6.1-sol` has live model coverage. See [frozen model selection](cli-configuration.md#frozen-codex-model-catalog).

Native search is verified through an actual backend lookup that returned `search_performed=true` and 20 structured source URLs; `apilookup` uses recognized returned results rather than model recall or requested/open-page URLs. Claude 2.1.304, Gemini, Antigravity (`agy`), and xAI Grok Build (`grok`) remain blocked by unverified policy gates. See the [Codex/Claude verification report](verification/cli-policy/README.md), [Antigravity/Grok findings](clink-cli-admission.md), and [implementation results](clink-implementation-results.md). A failed gate returns an explicit error rather than trying another CLI. The [17-tool matrix](verification/live-tools-2/README.md) plus [separate lookup](verification/native-search-1/README.md) cover all 18 outcomes. Later parser hardening passed offline checks; the native call was not rerun under that final hardening and its raw output was not retained/replayed.

## 3. Connect your MCP client

Add a stdio server entry using the absolute checkout path:

```json
{
  "mcpServers": {
    "clink": {
      "command": "/absolute/path/clink-mcp-server/run-server.sh",
      "args": []
    }
  }
}
```

Use your client's own configuration format if it differs from this JSON example. Ensure its `PATH` includes the native CLI location, or use an absolute `command` in the CLI manifest. You do not need to activate `.venv`: the launcher selects its Python explicitly. Reload your client after changing its configuration.

For a direct stdio launch:

```sh
./run-server.sh
```

The launcher reserves stdout for MCP messages. Startup diagnostics go to stderr and rotated logs under `logs/`. An interactive terminal may appear idle because the server waits for MCP input.

## 4. Check discovery, then consult

Ask your client to call `version` and `listmodels`. These local calls must work without provider credentials or a usable native CLI. All 18 tools are enabled unless named in `DISABLED_TOOLS`; `version` and `listmodels` remain available.

Only after a target is admitted, make an explicit consultation. For `clink`:

```json
{"prompt":"Explain the tradeoffs of this proposed change.","cli_name":"codex","role":"default"}
```

A real consultation can consume native subscription/API quota. Proposed code is returned as text; the server does not create a generated project file. `planner`, `docgen`, `tracer`, and `challenge` guide local caller work without a delegated model call. Workflow tools preserve investigation steps and consult an expert at the final hook when requested.

## 5. Operate and update

```sh
# Show launcher usage.
./run-server.sh --help

# Follow existing runtime logs without running setup.
tail -f logs/mcp_server.log

# Explicitly refresh dependencies after reviewing requirements changes.
./run-server.sh --setup
```

Server conversations and per-tool workflow state are held in memory, with default retention of three hours and 50 individual messages. Restarting loses them. Native CLI session IDs are not continuation tokens. Failed or timed-out model calls are not automatically retried; starting a new call can incur additional usage.

For a missing environment/dependency error, run the explicit setup step. For `path_not_allowed`, correct operator-approved roots rather than weakening the request policy. For `policy_unverified` or `unsupported_capability`, use discovery and the recorded evidence; changing prompt wording cannot enable the capability. Existing PAL users should follow the [migration guide](migration.md).
