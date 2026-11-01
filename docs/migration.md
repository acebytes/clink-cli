# Migrating from PAL MCP Server

This fork retains PAL's 18 MCP tools and caller-driven workflows while routing delegated analysis through the shared native CLI backend. The default is `codex` with role `default`. Provider API keys, catalogs, alias routing, and hosted-provider fallback no longer select an execution backend.

## Install and launch

Run setup explicitly from the checkout:

```bash
./run-server.sh --setup
```

Setup prepares `.venv` and installs `requirements.txt`. Ordinary launch uses that environment:

```bash
./run-server.sh
```

Ordinary launch only starts the server; it does not install packages or modify `.env`, MCP client registration, or native CLI configuration. Update your MCP client's server command to the absolute path of this checkout's `run-server.sh`; remove legacy setup/log-management arguments. The launcher uses `.venv`, replacing `.pal_venv` in older PAL instructions.

Authenticate an admitted native CLI through its own login mechanism. The server does not transfer PAL credentials or copy native authentication data. Installation/login alone does not permit execution. Check `listmodels`; discovery does not make an authentication/model call.

[Codex 0.166.0 is admitted for text, explicit local images, and structured native search](verification/cli-policy/codex-0.166.0-admission-v5-search.json) through the strict production parser on the exact tested macOS arm64 binary, restrictive recipe, and frozen catalog, bound by SHA-256. Other versions/builds are not admitted by that record. Native file/command tools are disabled, and declared text context is supplied inline. Historical v1/v2 acceptances were invalidated for backend usability; their attempts remain preserved.

The catalog/default snapshot no longer follows future remote catalog changes automatically. Only the captured 12 exact native IDs are eligible; unknown names and prefix/namespace aliases return `unsupported_capability` before launch. The genuine captured native default `gpt-6.1-sol` is passed explicitly when request/role/client defaults are absent, with `effective_model` and `default_origin: "pinned_native_catalog"`. This is not provider alias translation or proof of the actual provider model. Only `gpt-6.1-sol` has live model coverage; refresh requires explicit verification. See [frozen model selection](cli-configuration.md#frozen-codex-model-catalog).

Native search is verified by an actual backend lookup returning `search_performed=true` and 20 structured source URLs. Lookup requires recognized returned results; model prose/requested URLs are insufficient. Empty recognized results return `no_results`; unsupported evidence fails explicitly without fallback. Claude 2.1.304, Gemini, Antigravity (`agy`), and xAI Grok Build (`grok`) remain unavailable with unresolved policy gates. See the [Codex/Claude verification report](verification/cli-policy/README.md), separate [Antigravity/Grok findings](clink-cli-admission.md), and [implementation results](clink-implementation-results.md). The [17-tool matrix](verification/live-tools-2/README.md) plus [separate lookup](verification/native-search-1/README.md) cover all 18 tool outcomes. The lookup predates later offline parser hardening; no live final-parser rerun or raw-output replay is claimed.

## Migrate client manifests

Move operator manifests from `~/.pal/cli_clients/` to `~/.clink/cli_clients/`; the old directory remains a deprecated source. Review duplicates rather than relying on partial overrides.

Source precedence is now:

```text
bundled conf/cli_clients
  < ~/.pal/cli_clients
  < ~/.clink/cli_clients
  < CLI_CLIENTS_CONFIG_PATH (JSON file or directory)
```

**`CLI_CLIENTS_CONFIG_PATH` now wins.** Each later manifest replaces the entire client, including roles; fields are not deep-merged. Duplicate names in one source fail. A partial replacement can therefore remove a role you used before. Configuration and prompt changes require a server restart.

For each manifest:

1. Add `"schema_version": 1` and an explicit `adapter`. Adapter inference remains only for the built-in client names `codex`, `claude`, and `gemini`.
2. Keep `command` as the executable. Remove shell setup, bypass/permission flags, output-file options, resume options, and arbitrary config/MCP arguments. They fail validation. Put native model selection in request `model`, role `model`, or client `default_model`.
3. Set an existing `working_dir` and explicit existing `allowed_roots` when callers need more than that single directory. Relative manifest paths resolve against the file that declares them.
4. Retain each needed role and its `prompt_path`. Omitted prompts fall back to the bundled generic prompt, including for newly declared specialist roles. Use only validated `additional_args`, `role_args`, and child `env` values.

See [CLI configuration](cli-configuration.md) for the exact schema, argument allowlist, role behavior, and a complete example. A custom client name selects a registered adapter; it cannot introduce an arbitrary adapter or relax admission.

## Review environment settings

The repository `.env` is still loaded at runtime. `PAL_MCP_FORCE_ENV_OVERRIDE` keeps its legacy name and defaults to `false`: existing process environment variables take precedence over `.env`. When set to `true` **in `.env`**, the server's configuration lookup uses the `.env` mapping and returns defaults for keys absent from it, even if those keys are present in the process environment. The launcher does not rewrite the file.

| Setting | Migration |
| --- | --- |
| `CLINK_DEFAULT_CLI` | New default client selector; defaults to `codex` and must name a configured client. |
| `CLINK_MAX_CONCURRENT` | Positive integer; defaults to 2. Queue and deadline bounds still apply. |
| `CLI_CLIENTS_CONFIG_PATH` | Retained, now the highest-priority JSON file/directory source. |
| `DISABLED_TOOLS` | Retained comma-separated names, case-insensitive; defaults empty, enabling all 18 tools. `version` and `listmodels` cannot be disabled. |
| `DEFAULT_MODEL` | No longer selects a provider/model. Migrate to client `default_model` or role/request `model`. |
| Provider keys, provider URLs, model allowlists/catalogs | No longer configure server inference. Remove obsolete entries from server configuration after preserving any separately needed native CLI setup. |
| `DEFAULT_THINKING_MODE_THINKDEEP` | Deprecated compatibility setting; no verified native reasoning-budget mapping. Explicit legacy tuning is unapplied with warnings. |
| `LOCALE`, `LOG_LEVEL` | Retained; log level defaults to `INFO`. |
| `CONVERSATION_TIMEOUT_HOURS`, `MAX_CONVERSATION_TURNS` | Retained, with defaults of 3 hours and 50 turns. |
| `MAX_MCP_OUTPUT_TOKENS` | Retained as MCP output budgeting; it does not configure a native model's context window. |
| `CLINK_DELEGATION_DEPTH` | Internal recursion guard; do not set it to configure normal routing or policy. |

Do not move provider keys into manifest `env`: the child environment is allowlisted and excludes provider keys. Keep native authentication in the CLI's own stores.

## Update callers and operational expectations

- Delegated tools accept optional `cli_name` and `role`. An unavailable selection fails without fallback. Model values are native identifiers, resolved request → role → client → admitted native default (frozen for Codex); omitted, `null`, or `"auto"` uses the next default.
- `chat` still requires `working_directory_absolute_path`. It must name an existing absolute directory inside the selected client's approved roots. Configure roots explicitly when switching projects.
- Tools return proposed code/content for the caller to apply. The server does not create project outputs such as `pal_generated.code`; delegated execution remains advisory.
- Server `continuation_id` values refer to bounded in-memory history, not native CLI session IDs. Restart loses all continuations. Unknown, expired, full, or conflicting continuations fail explicitly; preserve any needed work outside that memory before restarting.
- Sequential consensus remains the default; `execution_mode: "parallel"` opts in to parallel consultation. Participants resolve independently, results retain input order, and the caller synthesizes. Declared-file changes invalidate the consensus; detected consistency is reported as `unchanged_at_checks`, not a filesystem snapshot.
- Explicit local images are admitted only through the pinned Codex binary/recipe/catalog. Base64 image input is rejected. Native search requires recognized structured returned sources, with no automatic follow-up or fallback. Declared text is embedded within the bounded prompt because native file/command tools are disabled. Context roots and native read-only enforcement do not establish complete OS read isolation.
- Delegation requires POSIX process supervision. Timeouts, queue/output bounds, and cancellation are enforced centrally. No automatic retries, backend substitution, native resume, or hidden synthesis calls occur. See [the limits](cli-configuration.md#execution-policy-and-bounds).

Risk & Mitigation: a restart discards conversation state, a native CLI update can invalidate policy admission, and remote work may already incur usage when cancellation arrives. Preserve caller-owned findings before restart, recheck installed-version admission after updates, and make any new usage attempt explicit.
