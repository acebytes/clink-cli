# CLI configuration

`clink-mcp-server` routes consultations through installed CLIs and exposes separate explicit [native task actions](native-cli-tasks.md). Consultation defaults to client `codex`, role `default`; `CLINK_DEFAULT_CLI` may select another configured consultation client. Native start requires explicit CLI/project selection. The server has no hosted-provider fallback. Local workflow steps and discovery do not need a logged-in CLI.

## Availability and admission

Installation, configuration, authentication and admission are separate. Consult `listmodels` for MCP profiles and `clink generation-capabilities --json` for programmatic generation. The [README](../README.md) lists the verified families for each operation. Changing a pinned binary or protected recipe requires new verification. An unavailable capability returns an error before delegation; there is no fallback.

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

## Remember an approved directory

```sh
clink allow /path/to/workspace --cli codex
clink allow /path/to/workspace --cli agy --project /bound/project
# Explicit consent for a script or desktop application's selected folder:
clink allow /path/to/workspace --cli codex --yes
```

The command accepts relative paths, `~`, symlinks and trailing slashes, resolves
them to an existing directory, and shows the canonical path in one default-No
question. Only `y` or `yes` approves. Empty input, other answers and EOF leave
configuration unchanged. A terminal is required unless `--yes` explicitly grants
consent; scripts cannot feed approval through stdin. Root and home directories
print one breadth warning, including with `--yes`.

Enrollment updates the effective whole-client winner, regardless of its filename.
Use `--project` to reproduce an MCP server's explicit binding. Explicit sources
remain highest priority and must be valid; a shadowed manifest cannot grant
authority. Bundled defaults become a complete user override with absolute paths
preserving executable, working directory and prompt meanings. Bundled files are
never edited. Explicitly pointing at a bundled file must be replaced with an
independent operator manifest before enrollment.

Already explicit approved roots and their descendants require no write or prompt.
Implicit/bundled coverage still requires durable operator consent. Existing
manifests retain all bytes outside the top-level `allowed_roots` value; strict
JSON is required. Publication preserves modes, creates private new files, and
uses a nonblocking destination-directory lock with source, target and content
checks. Observed races fail without retry. An error after publication says that
the grant was saved; inspect it before retrying. Other editors do not share this
cooperative lock.

Restart existing MCP servers after enrollment; their snapshots stay unchanged.
Approved roots constrain project selection and supplied context. Native CLI trust
and headless permission checks still apply independently.

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

The bundled [catalog snapshot](../clink/resources/codex-0.166.0-models-direct.json) and [capture provenance](../clink/resources/PROVENANCE.md) preserve the genuine installed catalog's native IDs, order, defaults, visibility, instructions, and other descriptor fields. The bounded policy projection changes only `tool_mode` to `direct`, while native code-mode host, shell, delegation, hook/plugin, and MCP controls remain disabled. The snapshot is frozen; no remote catalog refresh, automatic model substitution, or synthetic descriptor fallback occurs. A refresh requires explicit verification and new admission evidence.

Only the 12 exact IDs in the [admission record](release-evidence.md) are eligible. Unknown IDs, case/prefix variants, and namespace aliases fail before launch. Catalog membership does not prove live coverage for every model: only `gpt-6.1-sol` was exercised live.

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

## Application-owned generation configuration

Applications can bind one independent configuration directory before generation
reads stdin:

```sh
clink --consumer-config-dir /absolute/canonical/app-config generate --request-json -
clink --consumer-config-dir /absolute/canonical/app-config allow /absolute/project --cli codex --yes --json
```

The directory must already exist and be canonical and absolute. This binding is
mutually exclusive with `--project`; it loads only regular JSON files directly
inside that directory. Symlinked or nonregular JSON entries fail closed. It does
not load bundled, user, legacy, project, or `CLI_CLIENTS_CONFIG_PATH` manifests.
An empty directory is an error: the application must bootstrap its selected CLI
manifest before enrollment. An invalid consumer configuration fails before stdin
is read, without a fallback to another source.

Each manifest must be named exactly `claude.json` or `codex.json`, with exactly
these six required keys. `name` and `adapter` must both equal the filename's CLI.
For example, `codex.json` can start without folder authority:

```json
{
  "schema_version": 1,
  "name": "codex",
  "adapter": "codex",
  "command": "/absolute/path/to/codex",
  "allowed_roots": [],
  "generation_native": {"inherit_env": [], "required_env": []}
}
```

`schema_version` must be the integer `1`. `command` is parsed without a shell
and must contain one absolute executable path without arguments; use shell
quoting around paths containing spaces (for example, the JSON value
`"'/Applications/Native CLI/bin/codex'"`). Executable discovery and independent
binary/profile admission still apply at generation time. Every `allowed_roots`
entry must name an existing canonical absolute directory. An empty list grants
no project access; enrollment appends the first explicit grant atomically.
`generation_native` accepts only the environment-name fields described above.
All other manifest fields are rejected, including empty or null `roles`,
`native_task`, `working_dir`, `env`, argument fields, and model defaults.
Consumer clients have no working-directory default, consultation roles, or
native-task profile. Their grants authorize only separately admitted native
generation and remain bound to the same configuration directory during reload.

`allow --json` writes exactly one schema-one result to stdout, while readable
diagnostics remain on stderr. Human text remains the default without `--json`.
A successful first grant returns:

```json
{"schema_version":1,"cli":"codex","project_dir":"/absolute/project","status":"allowed","grant_saved":true,"error_code":null}
```

An existing grant uses `status: "already_allowed"` and does not rewrite the file.
A failed operation uses `status: "failed"`, a nonempty `error_code`, and boolean
`grant_saved`. The value is `true` once publication occurred, including a later
filesystem-sync or reload failure; it is `false` only when no publication is
known to have occurred. A missing or malformed result means the persistence
outcome is unknown: inspect the manifest before retrying. Cancellation or parent
loss cannot undo a published grant. Applications should obtain GUI consent before
using `--yes`; Clink does not treat the flag as native CLI permission approval.

Risk & Mitigation: folder enrollment changes persistent generation authority.
The consumer binding, confined file reads, parent/source identity checks,
cooperative locking, and compare-before-publication checks prevent observed
configuration races from transferring that authority. Native tools can still
have effects outside supplied context roots under their own permissions.

## Native generation configuration

Programmatic native generation requires separate `generation_native_v1`
admission, explicit v2 native opt-in, and a selected canonical project. It requires
independently configured Codex `allowed_roots`; request paths never select Clink
manifests or grant trust. Optional `generation_native.inherit_env` and
`generation_native.required_env` contain environment names only. See the
[complete application setup example](generation-native-setup.md) and
[authority contract](generation-native-protocol.md). Existing consultation and
native-task policies are unchanged.
