# CLI configuration

`clink-mcp-server` routes consultations through installed CLIs and exposes separate explicit [native task actions](native-cli-tasks.md). Consultation defaults to client `codex`, role `default`; `CLINK_DEFAULT_CLI` may select another configured consultation client. Native start requires explicit CLI/project selection. The server has no hosted-provider fallback. Local workflow steps and discovery do not need a logged-in CLI.

## Availability and admission

Call `listmodels` with `{}` to inspect configured clients, roles, executable presence, native model defaults, policy verification, and image/search capabilities. In consultation `clients`, `installed` reports executable presence; `available` also requires verified consultation policy and POSIX support. Native availability is reported separately in `native_task_clients`. Discovery does not run a model or log in; authentication is reported as unknown. Availability is provisional: exact executable, recipe, and bound resource checks still run before launch. Installation alone does not establish admission for either route.

[Codex 0.166.0 has accepted text/image/search admission](verification/cli-policy/codex-0.166.0-admission-v5-search.json) through the strict production parser. It binds the tested macOS arm64 executable, exact restrictive recipe, and frozen bundled catalog by SHA-256. Changing any bound component invalidates that admission; other platforms/builds are not admitted by the record. Earlier v1/v2 acceptances were invalidated for backend usability and remain preserved.

Native search is verified from an [actual backend lookup](verification/native-search-1/README.md) that returned `search_performed=true` and 20 structured source URLs. Completed recognized search results establish sources; assistant prose, requested URLs, and open-page intent do not. Recognized empty results return `no_results`; missing/unrecognized result shapes remain unsupported and genuine error items remain rejected. No automatic retry, extra lookup, or fallback is added.

The live lookup used its recorded parser revision. Later hostname/Unicode and literal `is_error` hardening passed offline tests using source-shaped synthetic fixtures and the captured source URLs. No live rerun under that final hardening or replay of unretained native raw output is claimed. The [17-tool matrix](verification/live-tools-2/README.md) plus separate lookup cover all 18 tool outcomes; see [implementation results](clink-implementation-results.md).

Claude 2.1.304 consultation remains `policy_unverified` because its advisory managed-authority/live boundaries are unresolved; Gemini, Antigravity (`agy`), and xAI Grok Build (`grok`) consultations are also unavailable.

[Claude Code 2.1.304 native tasks have separate admission](verification/native-claude-2.1.304-admission.json) for the exact recorded executable and launch recipe. Changed binaries/recipes fail closed and require reverification; the live synthetic-project evidence does not prove all user configurations, models, or detached/remote cleanup. See [native lifecycle results](native-lifecycle-results.md) for the passing gates and preserved failed/partial attempts.

[Grok 1.0.70 native tasks are admitted](verification/native-grok-1.0.70-admission.json) for the exact recorded macOS arm64 executable and recipe, with observed `grok-4.7-build` execution. Earlier failed checks and ordinal8’s unknown `uncertain/native_failed` cause remain preserved; a later cancellation passed. See the [compatibility review](native-grok-admission-review.md). Antigravity 1.4.12 is also [separately admitted](verification/native-agy-1.4.12-admission.json) for its exact macOS arm64 binary/recipe, with actual model unknown. Native Codex remains disabled because its supported execution output omits a genuine prelaunch refusal; see the [diagnosis](native-codex-attempt5-diagnosis.md). Existing Codex consultation admission does not authorize native Codex execution; enabling a profile cannot supply missing admission. See [native transport and permission contracts](native-cli-tasks.md#adapter-transports-and-permissions), the [Codex/Claude verification report](verification/cli-policy/README.md), and earlier [adapter findings](clink-cli-admission.md). Installation/configuration does not grant admission, and no generic custom-command adapter exists.

Delegation supports POSIX process-group cleanup. On other platforms it returns `unsupported_platform`; local tools can still operate.

## Manifest locations and replacement

The registry reads JSON manifests in this order, from lowest to highest priority:

1. Bundled `conf/cli_clients/`.
2. Deprecated `~/.pal/cli_clients/`, with a migration warning.
3. `~/.clink/cli_clients/`.
4. `<bound-project>/.clink/cli_clients/`, only when the server starts with `--project`.
5. `CLI_CLIENTS_CONFIG_PATH`, pointing to one JSON file or a directory.

A later source replaces the **whole client** with the same case-insensitive name. Roles, environment values, and arguments are not merged across sources. Duplicate client names within one source fail validation. Directory entries are read in sorted order, without recursion. A configured explicit source must exist.

**The explicit-path priority changed from PAL:** `CLI_CLIENTS_CONFIG_PATH` now has highest priority. Check overlapping client names before restarting.

Configuration is validated as a complete snapshot. Invalid JSON, duplicate JSON keys, unsupported fields, invalid paths, or unsafe arguments fail startup. An omitted `schema_version` is accepted with a migration warning; new manifests should declare `1`. Restart after configuration or prompt changes.

Use [project setup](project-setup.md) to generate project-local manifests, portable prompt files, and an MCP registration bound to that directory. Project configuration replaces user configuration for the same client. The binding is captured once per server; the registry does not search parent directories or select configuration from request paths. Existing launches without `--project` do not load project manifests. The explicit configuration path remains the highest-priority override.

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
| `working_dir` | Existing directory. Defaults to the bound project when launched with `--project`, otherwise the server's startup working directory. |
| `allowed_roots` | Nonempty list of existing directories. Defaults to the resolved `working_dir` alone; `working_dir` must be inside one of these roots. |
| `default_model` | Consultation native CLI identifier or `null`; admitted Codex IDs must match its frozen catalog. No provider alias translation. |
| `timeout_seconds` | Positive integer, at most 1800; defaults to 1800 and can shorten the overall deadline. |
| `additional_args` | Validated extra argv tokens; use `[]` for ordinary configuration. |
| `env` | String values for allowlisted child environment keys only; use `{}` unless an override is needed. |
| `roles` | Consultation role objects with optional `prompt_path`, `role_args`, `description`, and native `model`. |
| `native_task` | Optional separate native task profile for `claude`, `grok`, `agy`, or `codex`; see below. Configuration alone does not grant admission. |

Relative `working_dir`, `allowed_roots`, executable paths, and `prompt_path` resolve against the declaring manifest's directory; `~` expands to the user's home. A relative `CLI_CLIENTS_CONFIG_PATH` resolves against server startup cwd. Role names follow the client-name rules.

For consultation roles, the registry supplies a `default` role if omitted. Every consultation role with omitted or `null` `prompt_path` uses the adapter's bundled `systemprompts/clink/default.txt`; it does not inherit a same-named bundled specialist prompt. Explicit prompt files must be existing regular UTF-8 files no larger than 1 MiB. Their contents are read at startup. Consultation roles provide subordinate expertise; they cannot override tool instructions or execution policy. Native roles require their own explicit prompts below.

## Antigravity projects outside the checkout

For new multi-repository setups, [project setup](project-setup.md) generates these fields inside the target project automatically. The manual user-scope option below remains supported.

The bundled Agy manifest omits `working_dir` and `allowed_roots`. Without `--project`, the registry therefore uses the server's startup working directory for both. `run-server.sh` starts in the checkout, so seeing only the checkout in `listmodels` is expected even when no operator manifest exists. Restarting without a configuration change preserves that default. An explicit `--project` binding supplies project defaults and loads its local manifests.

For a project elsewhere, save this complete replacement as `~/.clink/cli_clients/agy.json`. Replace every `/absolute/path/to/clink-mcp-server` with your checkout path and both `/absolute/path/to/project` occurrences with the existing project directory. This example preserves all three consultation and native roles. If `CLI_CLIENTS_CONFIG_PATH` also defines `agy`, update that higher-priority manifest instead.

```json
{
  "schema_version": 1,
  "name": "agy",
  "adapter": "agy",
  "command": "agy",
  "working_dir": "/absolute/path/to/project",
  "allowed_roots": ["/absolute/path/to/project"],
  "roles": {
    "default": {
      "prompt_path": "/absolute/path/to/clink-mcp-server/systemprompts/clink/default.txt"
    },
    "planner": {
      "prompt_path": "/absolute/path/to/clink-mcp-server/systemprompts/clink/default_planner.txt"
    },
    "codereviewer": {
      "prompt_path": "/absolute/path/to/clink-mcp-server/systemprompts/clink/default_codereviewer.txt"
    }
  },
  "native_task": {
    "enabled": true,
    "roles": {
      "default": {
        "prompt_path": "/absolute/path/to/clink-mcp-server/systemprompts/clink/native_default.txt"
      },
      "planner": {
        "prompt_path": "/absolute/path/to/clink-mcp-server/systemprompts/clink/native_planner.txt"
      },
      "codereviewer": {
        "prompt_path": "/absolute/path/to/clink-mcp-server/systemprompts/clink/native_codereviewer.txt"
      }
    }
  }
}
```

Set both `working_dir` and `allowed_roots`: the configured working directory must be inside an allowed root. Choose the narrow project directory, rather than a shared temporary directory or your entire home. Prompt files may remain in the checkout outside that project. Absolute prompt paths here make relocation explicit; existing paths relative to the new manifest also work. Copying the bundled manifest unchanged into your home configuration breaks its six relative prompt paths, which were anchored to `conf/cli_clients/`.

Restart the MCP server after saving the manifest, then inspect `listmodels`: Agy's `clients` entry reports its configured roots, and `native_task_clients` reports its enabled native profile. Save any useful task results first: restart discards server-owned task handles. For an installed admitted Agy build, explicitly submit:

```json
{
  "action": "start",
  "cli_name": "agy",
  "project_dir": "/absolute/path/to/project",
  "prompt": "Report connectivity without editing files or running commands.",
  "request_key": "agy-connectivity-1",
  "timeout_seconds": 180
}
```

Poll the returned handle with `action: "status"`; a successful start only schedules work. This is native delegation under the CLI's configured permissions and can use native tools or edit files. The prompt requests a no-op but does not impose an advisory sandbox. Omitting `action` selects consultation, which remains `policy_unverified` for Agy; configuration does not change that admission. A `path_not_allowed` refusal means the project or declared context did not pass path checks. Inspect the selected client's roots, path existence/access, and resolved symlink targets before submitting again. The error intentionally omits filesystem paths and raw exception details.

## Native task profile

The `claude`, `grok`, `agy`, and `codex` adapters implement this profile; Gemini does not. A client's `native_task.enabled` field defaults to `false`. The bundled Claude, Grok, and Antigravity profiles are enabled and separately admitted for their exact recorded binaries/recipes; Grok and Antigravity additionally require Darwin arm64. The bundled native Codex profile remains disabled for its known output limitation. Enablement and executable presence do not authorize launch: a disabled profile is unavailable, and an enabled profile without native evidence fails with `policy_unverified`.

Add the following object to a complete operator-owned Claude manifest for an admitted installation; replacement still replaces the whole client. The example assumes the prompt file exists relative to that manifest:

```json
{
  "native_task": {
    "enabled": true,
    "inherit_env": [],
    "required_env": [],
    "default_model": null,
    "roles": {
      "default": {"prompt_path": "./prompts/native-task.txt"}
    }
  }
}
```

An enabled profile requires a native `default` role. Each native role requires an explicit existing UTF-8 `prompt_path`; it may also specify `description` and `model`, but `role_args` must remain empty. Write task expertise in these prompts without the consultation ban on edits or native capabilities. Native roles and model defaults do not inherit the top-level consultation roles or `default_model`. Native model precedence is request → native role → `native_task.default_model` → native CLI configuration. A resumed follow-up must preserve the original CLI, project, role, instruction profile, and model selection.

`inherit_env` lists environment **names**, never values, needed by configured hooks/tools/MCP integrations. `required_env` must be a subset; names must be unique valid environment identifiers outside the reserved `CLINK_` namespace. At most 128 names are accepted in each list. Values are read from the server process only when launching a child. Missing required values stop execution with `missing_environment`, exposing names only. Keep credential values out of manifests, prompts, logs, and receipts; native login/configuration stores remain CLI-owned. Native profiles preserve the baseline auth/config location variables listed below; Grok additionally inherits `GROK_HOME`. Other integration names require explicit `inherit_env` configuration. This addition is separate from the unchanged consultation environment allowlist.

Native profiles retain native project/user configuration under the CLI's permissions. They use separate adapter transports: Claude text stdin, Grok a private prompt file with empty stdin, Agy one user NDJSON input event, and Codex explicit stdin `-` through `exec`/`exec resume`. Private session mapping selects exact follow-ups. Grok's bounded prompt file is `0600` inside a `0700` directory, checked before launch and removed after shared process cleanup on controlled exits. These adapters reuse one task manager and process pool. See [transport details and verification](native-cli-tasks.md#adapter-transports-and-permissions).

The server protects launch flags; arbitrary native settings, resume IDs, bypass flags, and extra command arguments remain invalid. No PTY, interactive approval relay, or silent permission approval is supplied. Native permission behavior differs by adapter; recognized denials return `permission_needed`, and other terminal failures/incomplete signals cannot establish completion. Codex native `exec` uses its own approval semantics without wrapper approval/sandbox overrides. Explicit image input is currently unsupported for native tasks. Native actions reject legacy `temperature`/`thinking_mode` controls; use native configuration and model fields. Per-attempt `timeout_seconds` accepts a finite number from 1 to 1800, includes launch preparation/queue wait, and starts afresh for follow-up; a shorter client timeout wins. See [native task usage and limits](native-cli-tasks.md).

## Consultation model selection

For each consultation, model precedence is:

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

The restricted Codex consultation recipe disables native file/command tools. The server supplies declared text files as bounded inline context and passes only validated explicit images through the admitted image interface; a path does not authorize native traversal or command execution. Codex's restrictive consultation profile can still perform broader OS reads, so it does not establish OS read isolation.

`chat` retains its required `working_directory_absolute_path`. That existing absolute directory overrides the configured cwd for that chat call, but must resolve inside `allowed_roots`. Other tools use the configured directory unless their contract supplies one. Request paths cannot enlarge roots or change the configured executable. Declared context paths and symlink targets must pass root checks. These checks constrain server-supplied context; they do not promise that the native CLI can read only those roots.

## Execution policy and bounds

Each profile owns its launch policy. Consultation policy enforces sandbox, approval, MCP, hook/plugin, output, session, and recursion controls; manifests cannot weaken them. Native tasks use the separate profile above to preserve configured capabilities under native permissions, with independent admission and no permission bypass. Currently only Claude's `--verbose` token is permitted as an extra argument; Codex and the other adapters accept no extra argument tokens. This validation applies to `command` arguments, `additional_args`, and `role_args`. Put model selection in structured `model`/`default_model` fields. Legacy bypass flags, shell wrappers with arguments, output-file settings, resume flags, and arbitrary native settings fail validation.

Consultation child environments include only `PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `TMP`, `TEMP`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `CODEX_HOME`, and `CLAUDE_CONFIG_DIR`, plus the server-owned recursion marker. Manifest `env` accepts only that allowlist, excluding the marker. Provider keys and unrelated environment secrets are not forwarded. Native login stores retain ownership of authentication; the server does not copy credentials into prompts or generate native CLI configuration. Native auth/cache maintenance can still write operational files.

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

The server never automatically retries, switches clients/providers, or repairs output through a second model call. Consultations never resume native sessions. Explicit native `followup` resumes only the recorded private native session; it does not replay PAL history or restart work automatically. Native CLIs may retry internally. Cancellation cannot undo remote work or usage already submitted. The older Codex consultation cancellation probe predates its catalog projection and was not repeated under it; production runner cleanup also has separate synthetic coverage. Search and images require separately verified adapter capabilities; they are not inferred from CLI installation.

Native tasks share the process, queue, prompt, and output bounds above, and additionally retain at most 64 tasks, 16 attempts per task, and 8 MiB of task metadata/results. Only the latest result is retained, capped at 20,000 characters. Inactive records expire three hours after their last terminal attempt; polling does not extend retention. Active records are never evicted. Capacity overflow is rejected before submission.

Risk & Mitigation: native tasks can mutate files and external systems. Approved roots constrain project selection/context, not all native authority. Project/session ownership prevents conflicting server-owned native submissions, while external writers, detached descendants, and remote MCP operations remain outside that coordination. Cancellation/shutdown cleanup cannot roll back partial effects. Keep the independent installed-version admission gate intact and treat policy/capability errors as reasons to inspect evidence, never to add bypass flags.
