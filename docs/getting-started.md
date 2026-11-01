# Getting started with clink-mcp-server

This fork preserves PAL's MCP workflows and obtains advisory analysis from installed CLI agents. Explicit `clink` native task actions can delegate edits and tests with configured native capabilities; Claude Code 2.1.304, Grok 1.0.70, and Antigravity 1.4.12 have separate native admission for their exact recorded binaries/recipes. Copyright 2025 Beehive Innovations; distributed under the [Apache 2.0 license](../LICENSE).

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

The consultation default is client `codex`, role `default`; omitted `clink.action` and `action: "consult"` retain it. Native `start` requires explicit CLI selection and an approved project directory. Authenticate an admitted CLI using its native login mechanism. The server does not require provider API keys, log in for you, or copy credentials. Native authentication/cache maintenance can still write operational files.

For project-local configuration, run this from the clink checkout:

```sh
.venv/bin/python -m clink.setup --project /absolute/path/to/your-project
```

This creates `.clink/` manifests/prompts and a project `.mcp.json` registration with an explicit `--project` binding. It sets the project roots, preserves existing custom files, and makes no model calls. The [project setup guide](project-setup.md) also covers Claude Code's `/clink:setup` command and configuration precedence. Use a separate binding for each repository.

Manual [CLI configuration](cli-configuration.md) remains supported. Without `--project`, bundled manifests default to the server's startup directory, and the launcher starts from the checkout. Configure `working_dir` and `allowed_roots` for the intended project. Roots constrain project selection and server-supplied context; they do not sandbox all native tool authority. Chat's required `working_directory_absolute_path` may select a directory only within those roots; explicit context paths are checked after resolving symlinks.

Choose settings in your MCP client's environment or, optionally, copy [.env.example](../.env.example) to `.env` yourself. Consultation model selection belongs in a manifest's top-level `default_model` or role `model`, or in an explicit request; native tasks use the separate `native_task` model defaults. A concrete model name is passed through to the native CLI; provider aliases are not translated.

CLI installation is separate from admission. `listmodels` reports configuration, executable presence, verification status, and capabilities without contacting a model; its authentication field remains unknown. [Codex 0.166.0 is admitted for text, explicit local images, and structured native search](verification/cli-policy/codex-0.166.0-admission-v5-search.json) when the exact tested macOS arm64 binary, restrictive recipe, and frozen catalog match their SHA-256 pins. Other builds/versions have no admission from that record. Native file/command tools are disabled; declared text context is supplied inline within the prompt limit.

The admitted snapshot accepts only its 12 exact native model IDs; unknown names and prefix/namespace aliases fail before launch. It freezes the genuine captured default as `gpt-6.1-sol`, passed explicitly when request/role/client defaults are absent. `effective_model` and `default_origin: "pinned_native_catalog"` describe that selection; they do not establish `actual_model`, which remains unknown unless the CLI reports it. Only `gpt-6.1-sol` has live model coverage. See [frozen model selection](cli-configuration.md#frozen-codex-model-catalog).

Native search is verified through an actual backend lookup that returned `search_performed=true` and 20 structured source URLs; `apilookup` uses recognized returned results rather than model recall or requested/open-page URLs. Claude 2.1.304, Gemini, Antigravity (`agy`), and xAI Grok Build (`grok`) consultations remain blocked by unverified policy gates.

[Claude Code 2.1.304 native tasks](verification/native-claude-2.1.304-admission.json) and [Grok 1.0.70 native tasks](verification/native-grok-1.0.70-admission.json) are separately admitted for their exact recorded binaries/recipes; Grok admission is scoped to macOS arm64. The Codex consultation receipt does not admit native execution. [Antigravity 1.4.12 native tasks](verification/native-agy-1.4.12-admission.json) are also admitted for their exact macOS arm64 binary/recipe. Native Codex remains disabled for a [supported-output refusal gap](native-codex-attempt5-diagnosis.md). Other binaries/recipes require reverification and fail closed. See [native lifecycle results](native-lifecycle-results.md) and [adapter results](native-adapters-results.md) for passing gates and preserved failures/limits.

The [Codex/Claude verification report](verification/cli-policy/README.md), [Antigravity/Grok findings](clink-cli-admission.md), and [implementation results](clink-implementation-results.md) retain their separate consultation evidence. A failed gate returns an explicit error rather than trying another CLI. The [17-tool matrix](verification/live-tools-2/README.md) plus [separate lookup](verification/native-search-1/README.md) cover all 18 outcomes. Later parser hardening passed offline checks; the native call was not rerun under that final hardening and its raw output was not retained/replayed.

## 3. Connect your MCP client

If you ran project setup, use its generated `.mcp.json` and reconnect your client. Otherwise, add a stdio server entry using the absolute checkout and project paths:

```json
{
  "mcpServers": {
    "clink": {
      "command": "/absolute/path/clink-mcp-server/run-server.sh",
      "args": ["--project", "/absolute/path/to/your-project"]
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

For delegated implementation, see [native CLI tasks](native-cli-tasks.md). Explicit `start`/`status`/`followup`/`cancel` actions use opaque task handles and private native sessions rather than PAL continuations. Configured tools, hooks, skills, plugins, MCP integrations, and subagents are intentionally retained under native permissions. Enabling a profile does not admit it; launch requires the selected binary/recipe to match its separate native admission or returns `policy_unverified` without launching. Live synthetic-project evidence covers edits/tests, hooks/subagent/MCP environment inheritance, same-session follow-up, permission handling, and cancellation after mutation; it does not establish every native configuration or complete detached/remote cleanup.

## 5. Operate and update

```sh
# Show launcher usage.
./run-server.sh --help

# Follow existing runtime logs without running setup.
tail -f logs/mcp_server.log

# Explicitly refresh dependencies after reviewing requirements changes.
./run-server.sh --setup
```

Server conversations and per-tool workflow state are held in memory, with default retention of three hours and 50 individual messages. Restarting loses them. Native CLI session IDs are not continuation tokens. Native task records/session mappings are also volatile: at most 64 tasks, 16 attempts each, and 8 MiB retained data, with only the latest bounded result. Inactive tasks expire three hours after their last terminal attempt; polling does not extend retention. A restart loses handles and deduplication state, and shutdown cancels active native work while awaiting cleanup.

Risk & Mitigation: failed, cancelled, or uncertain native work can leave file or external effects; cancellation does not roll them back. Save needed results durably, inspect effects before resubmitting ambiguous work, and independently check agent-reported changes/tests. `completed` is an execution outcome, not verified fulfillment. No automatic retry, resume, provider fallback, PTY, or approval relay is supplied; a new explicit attempt can incur usage.

For a missing environment/dependency error, run the explicit setup step. For `path_not_allowed`, correct operator-approved roots rather than weakening the request policy. For `policy_unverified` or `unsupported_capability`, use discovery and the recorded evidence; changing prompt wording cannot enable the capability. Existing PAL users should follow the [migration guide](migration.md).
