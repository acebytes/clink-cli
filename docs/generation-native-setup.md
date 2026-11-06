# Configure native generation for an application

Native generation requires an explicit v2 request and its own verified execution
profile. Configuration does not bypass admission. It uses Codex's configured
tools and native headless permissions; it may change files or call integrations.
Cancellation does not undo changes. Read the [v2 contract](generation-native-protocol.md).

The separately admitted build is Codex 0.166.0 on Darwin/arm64, bound to its
exact executable and recipe. The live checks used requested model
`gpt-6.1-sol`; native output did not report actual model identity. Other native
model identifiers are passed through explicitly but were not covered by these
checks. A CLI upgrade invalidates the binary pin until separately verified.

## Select a project independently of the request

The simplest enrollment command preserves your effective Codex settings:

```sh
clink allow /path/to/project --cli codex
# For a headless application or script, explicitly preapprove:
clink allow /path/to/project --cli codex --yes
```

The first command asks once, default No, showing the canonical directory.
Approval saves the root atomically. If no operator manifest exists, Clink writes
a complete user override while preserving working directory, roles and relative
path meanings. Existing operator manifests retain unrelated bytes and settings.
Only `y` or `yes` approves; declining changes nothing and launches no backend.

Native-v2 generation can ask on a separate foreground controlling terminal when
stderr is a terminal and CI is absent. It reads JSON stdin through EOF first,
reloads and validates the saved configuration, then continues the same parsed
request once within its original deadline. Prompts and enrollment diagnostics go
to stderr; stdout remains one bounded JSON result. Headless callers receive a
`clink allow ... --yes` remedy. Approval does not select configuration from the
request directory or change native permissions. If saving succeeded but reload or
generation fails, the explicit grant remains; Clink does not roll back or resend.

For manual configuration:

Put a complete Codex client manifest at `~/.clink/cli_clients/codex.json`, or set
`CLI_CLIENTS_CONFIG_PATH` in the application launch environment to an independently
chosen manifest file/directory. A desktop application may not inherit variables
set in a terminal. User-scope configuration is the simpler persistent option.

Set `working_dir` to an existing canonical absolute directory and include it in
`allowed_roots`. Include every project you intend to select, or a deliberately
chosen parent. Requests must name an existing canonical project within those
roots. The native generation request uses its explicit `project_dir`, not the
manifest's `working_dir` as an implicit target.

When editing an existing manifest, preserve its command, roles, native-task
settings, and any other intentional configuration. Manifests replace the whole
client; they are not partial patches. Relative prompt paths resolve against the
manifest's directory, so copying the bundled JSON into your home directory
without fixing those paths will fail.

For a **new** user-scope manifest, this complete example retains the bundled
Codex roles and disabled native-task setting. Replace both absolute path prefixes
with your real canonical paths; do not overwrite an existing custom manifest:

```json
{
  "schema_version": 1,
  "name": "codex",
  "adapter": "codex",
  "command": "codex",
  "working_dir": "/absolute/path/to/project",
  "allowed_roots": ["/absolute/path/to/project"],
  "default_model": null,
  "additional_args": [],
  "env": {},
  "roles": {
    "default": {
      "prompt_path": "/absolute/path/clink-mcp-server/systemprompts/clink/default.txt",
      "role_args": []
    },
    "planner": {
      "prompt_path": "/absolute/path/clink-mcp-server/systemprompts/clink/default_planner.txt",
      "role_args": []
    },
    "codereviewer": {
      "prompt_path": "/absolute/path/clink-mcp-server/systemprompts/clink/codex_codereviewer.txt",
      "role_args": []
    }
  },
  "native_task": {
    "enabled": false,
    "inherit_env": [],
    "required_env": [],
    "default_model": null,
    "roles": {
      "default": {
        "prompt_path": "/absolute/path/clink-mcp-server/systemprompts/clink/native_default.txt"
      },
      "planner": {
        "prompt_path": "/absolute/path/clink-mcp-server/systemprompts/clink/native_planner.txt"
      },
      "codereviewer": {
        "prompt_path": "/absolute/path/clink-mcp-server/systemprompts/clink/native_codereviewer.txt"
      }
    }
  },
  "generation_native": {
    "inherit_env": [],
    "required_env": []
  }
}
```

The optional `generation_native` section names additional environment variables
that configured integrations need. `required_env` must be a subset of
`inherit_env`. Values come from the launching process, never the JSON request;
do not put credential values into this manifest. Codex's native authentication
and configuration locations are retained by the baseline environment.

The CLI does not discover project `.clink` configuration from request paths.
`/clink:setup` configures a project-bound MCP server; that binding is not
automatically applied to `clink generate`. Native Codex's own project
configuration and trust checks still apply at the selected project.
