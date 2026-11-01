# CLI configuration

`clink-mcp-server` routes delegated analysis through installed native CLIs. The default selection is client `codex`, role `default`; `CLINK_DEFAULT_CLI` may select another configured client. The server has no hosted-provider fallback. Local workflow steps and discovery do not need a logged-in CLI.

## Availability and admission

Call `listmodels` with `{}` to inspect configured clients, roles, executable presence, native model defaults, policy verification, and image/search capabilities. Discovery does not run a model or log in; authentication is reported as unknown. An executable being available does not mean its execution policy is verified.

[Codex 0.166.0 has accepted text/image/search admission](verification/cli-policy/codex-0.166.0-admission-v5-search.json) through the strict production parser. It binds the tested macOS arm64 executable, exact restrictive recipe, and frozen bundled catalog by SHA-256. Changing any bound component invalidates that admission; other platforms/builds are not admitted by the record. Earlier v1/v2 acceptances were invalidated for backend usability and remain preserved.

Native search is verified from an [actual backend lookup](verification/native-search-1/README.md) that returned `search_performed=true` and 20 structured source URLs. Completed recognized search results establish sources; assistant prose, requested URLs, and open-page intent do not. Recognized empty results return `no_results`; missing/unrecognized result shapes remain unsupported and genuine error items remain rejected. No automatic retry, extra lookup, or fallback is added.

The live lookup used its recorded parser revision. Later hostname/Unicode and literal `is_error` hardening passed offline tests using source-shaped synthetic fixtures and the captured source URLs. No live rerun under that final hardening or replay of unretained native raw output is claimed. The [17-tool matrix](verification/live-tools-2/README.md) plus separate lookup cover all 18 tool outcomes; see [implementation results](clink-implementation-results.md).

Claude 2.1.304 remains `policy_unverified` because managed authority and live boundaries are unresolved; Gemini, Antigravity (`agy`), and xAI Grok Build (`grok`) are also unavailable. Read the [Codex/Claude verification report](verification/cli-policy/README.md) and separate [Antigravity/Grok findings](clink-cli-admission.md). Installation/configuration does not grant admission, and no generic custom-command adapter exists.

Delegation supports POSIX process-group cleanup. On other platforms it returns `unsupported_platform`; local tools can still operate.

## Manifest locations and replacement

The registry reads JSON manifests in this order, from lowest to highest priority:

1. Bundled `conf/cli_clients/`.
2. Deprecated `~/.pal/cli_clients/`, with a migration warning.
3. `~/.clink/cli_clients/`.
4. `CLI_CLIENTS_CONFIG_PATH`, pointing to one JSON file or a directory.

A later source replaces the **whole client** with the same case-insensitive name. Roles, environment values, and arguments are not merged across sources. Duplicate client names within one source fail validation. Directory entries are read in sorted order, without recursion. A configured explicit source must exist.

**The explicit-path priority changed from PAL:** `CLI_CLIENTS_CONFIG_PATH` now has highest priority. Check overlapping client names before restarting.

Configuration is validated as a complete snapshot. Invalid JSON, duplicate JSON keys, unsupported fields, invalid paths, or unsafe arguments fail startup. An omitted `schema_version` is accepted with a migration warning; new manifests should declare `1`. Restart after configuration or prompt changes.

## Manifest example

Save an operator-owned manifest such as `~/.clink/cli_clients/codex.json`. This example assumes `/work/project` and the UTF-8 prompt files already exist; replace the paths with your real directories and files.

```json
{
  "schema_version": 1,
  "name": "codex",
  "adapter": "codex",
  "command": "codex",
  "working_dir": "/work/project",
  "allowed_roots": ["/work/project"],
  "default_model": null,
  "timeout_seconds": 1800,
  "additional_args": [],
  "env": {},
  "roles": {
    "default": {
      "prompt_path": "./prompts/advisory.txt",
      "role_args": []
    },
    "codereviewer": {
      "prompt_path": "./prompts/reviewer.txt",
      "description": "Review correctness and maintainability",
      "model": null,
      "role_args": []
    }
  }
}
```

The prompt paths here resolve inside `~/.clink/cli_clients/prompts/`. This replacement defines two roles; the bundled `planner` role is not retained.

| Field | Behavior |
| --- | --- |
| `name` | Required client name; starts with a letter, then letters, digits, `_` or `-`, up to 64 characters. |
| `adapter` | Registered adapter identity. It may be omitted only when `name` is `codex`, `claude`, or `gemini`. A custom client name must select a registered adapter. Registration alone does not grant admission. |
| `command` | Required executable, parsed as argv without a shell. Bare names use the server's `PATH`; executable paths resolve relative to this manifest. Embedded arguments face the same strict validation as other arguments. |
| `working_dir` | Existing directory. Defaults to the server's startup working directory. |
| `allowed_roots` | Nonempty list of existing directories. Defaults to the resolved `working_dir` alone; `working_dir` must be inside one of these roots. |
| `default_model` | Native CLI identifier or `null`. No provider aliases or model catalogs. |
| `timeout_seconds` | Positive integer, at most 1800; defaults to 1800 and can shorten the overall deadline. |
| `additional_args` | Validated extra argv tokens; use `[]` for ordinary configuration. |
| `env` | String values for allowlisted child environment keys only; use `{}` unless an override is needed. |
| `roles` | Named role objects with optional `prompt_path`, `role_args`, `description`, and native `model`. |

Relative `working_dir`, `allowed_roots`, executable paths, and `prompt_path` resolve against the declaring manifest's directory; `~` expands to the user's home. A relative `CLI_CLIENTS_CONFIG_PATH` resolves against server startup cwd. Role names follow the client-name rules.

The registry always supplies a `default` role if omitted. Every role with omitted or `null` `prompt_path` uses the adapter's bundled `systemprompts/clink/default.txt`; it does not inherit a same-named bundled specialist prompt. Explicit prompt files must be existing regular UTF-8 files no larger than 1 MiB. Their contents are read at startup. Roles provide subordinate expertise; they cannot override tool instructions or execution policy.

## Native model selection

For each delegation, model precedence is:

1. Concrete request `model`.
2. Selected role's `model`.
3. Client `default_model`.
4. Admitted adapter native default; Codex uses the frozen captured default below.

Omitted, `null`, and `"auto"` values move to the next default. Identifiers are passed through without provider alias translation. For admitted Codex, identifiers outside the captured exact-ID set fail with `unsupported_capability` before launch; no native prefix/namespace alias lookup is permitted. Results distinguish requested, effective, and reported actual model; missing actual identity remains unknown. Legacy `temperature` and `thinking_mode` values receive `unsupported_parameter` warnings when supplied and remain unapplied.

### Frozen Codex model catalog

The bundled [catalog snapshot](../clink/resources/codex-0.166.0-models-direct.json) and [capture provenance](verification/cli-policy/codex-0.166.0-catalog-provenance.json) preserve the genuine installed catalog's native IDs, order, defaults, visibility, instructions, and other descriptor fields. The bounded policy projection changes only `tool_mode` to `direct`, while native code-mode host, shell, delegation, hook/plugin, and MCP controls remain disabled. The snapshot is frozen; no remote catalog refresh, automatic model substitution, or synthetic descriptor fallback occurs. A refresh requires explicit verification and new admission evidence.

Only the 12 exact IDs in the [admission record](verification/cli-policy/codex-0.166.0-admission-v5-search.json) are eligible. Unknown IDs, case/prefix variants, and namespace aliases fail before launch. Catalog membership does not prove live coverage for every model: only `gpt-6.1-sol` was exercised live.

When request, role, and client model defaults are absent, the genuine captured native default `gpt-6.1-sol` is passed explicitly. Metadata exposes `effective_model` and `default_origin: "pinned_native_catalog"`; explicit selections report their request/role/client origin. These fields describe selection, not provider-reported identity. `actual_model` remains unknown unless the CLI reports it.

For the `clink` tool:

```json
{
  "prompt": "Review this module and propose corrections as text.",
  "cli_name": "codex",
  "role": "codereviewer",
  "absolute_file_paths": ["/work/project/module.py"]
}
```

The restricted Codex recipe disables native file/command tools. The server supplies declared text files as bounded inline context and passes only validated explicit images through the admitted image interface; a path does not authorize native traversal or command execution. The native read-only profile permits broader OS reads, so this still does not establish OS read isolation.

`chat` retains its required `working_directory_absolute_path`. That existing absolute directory overrides the configured cwd for that chat call, but must resolve inside `allowed_roots`. Other tools use the configured directory unless their contract supplies one. Request paths cannot enlarge roots or change the configured executable. Declared context paths and symlink targets must pass root checks. These checks constrain server-supplied context; they do not promise that the native CLI can read only those roots.

## Execution policy and bounds

Adapter policy owns sandbox, approval, MCP, hook/plugin, output, session, and recursion controls. Manifests cannot weaken them. Currently only Claude's `--verbose` token is permitted as an extra argument; Codex and the other adapters accept no extra argument tokens. This validation applies to `command` arguments, `additional_args`, and `role_args`. Put model selection in structured `model`/`default_model` fields. Legacy bypass flags, shell wrappers with arguments, output-file settings, resume flags, and arbitrary native settings fail validation.

Child environments include only `PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `TMP`, `TEMP`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `CODEX_HOME`, and `CLAUDE_CONFIG_DIR`, plus the server-owned recursion marker. Manifest `env` accepts only that allowlist, excluding the marker. Provider keys and unrelated environment secrets are not forwarded. Native login stores retain ownership of authentication; the server does not copy credentials into prompts or generate native CLI configuration. Native auth/cache maintenance can still write operational files.

| Bound | Limit |
| --- | --- |
| Concurrent delegated processes | 2 by default; `CLINK_MAX_CONCURRENT` accepts a positive integer. |
| Waiting launches | 8; excess work returns `queue_full`. |
| Queue wait | At most 30 seconds, within the request deadline. |
| Overall deadline | At most 1800 seconds from admission, including lock and queue waits; client timeout can lower it. |
| Cancellation/timeout cleanup | Terminate the POSIX process group, then kill remaining processes after 5 seconds. |
| Captured stdout / stderr | 10 MiB each; overflow terminates execution with `output_limit`. |
| Returned delegated content | 20,000 characters with truncation metadata; MCP output budgeting also applies. |
| Assembled prompt / replay history | 1 MiB / 128 KiB UTF-8. |
| Explicit file/image context | At most 256 references and 64 MiB of files in total. |

The server never retries, switches clients/providers, resumes a native session, or repairs output through a second model call. Native CLIs may retry internally. Cancellation cannot undo remote work or usage already submitted. The recorded active native cancellation probe predates the catalog projection and was not repeated under it; production runner cleanup also has separate synthetic coverage. Search and images require separately verified adapter capabilities; they are not inferred from CLI installation.

Risk & Mitigation: subprocess authority and filesystem reads are the relevant boundaries. Keep the installed-version admission gate intact, configure narrow context roots, and treat `policy_unverified`, `path_not_allowed`, and capability errors as terminal results rather than reasons to add bypass flags.
