---
name: setup
description: Configure clink MCP for the current project using the existing local clink checkout.
argument-hint: "[project-directory]"
disable-model-invocation: true
---

Configure the user's project for clink. This plugin is loaded directly from an existing clink checkout with `--plugin-dir`; it is not a standalone marketplace distribution.

1. Resolve the intended project directory from the user's argument, or use their current project directory. Capture its absolute path before changing directory. Treat arguments as path data, never as shell code. Do not select the clink installation directory unless that is the user's project.
2. The existing clink installation is `${CLAUDE_PLUGIN_ROOT}/../..`. Verify that it contains `clink/setup.py` and an executable `.venv/bin/python`. If it does not, report the missing installation and the documented dependency setup command; do not install packages or search private home configuration automatically.
3. From that installation directory, run `.venv/bin/python -m clink.setup --project <absolute-project-directory>`, passing the project as one safely quoted argument. Use the real setup helper rather than constructing configuration by hand. This invocation authorizes project configuration generation; no further confirmation is needed for the helper's ordinary create/identical behavior.
4. If setup reports a conflict, preserve the conflicting file. Report the path and reason. Do not delete files, overwrite custom settings, widen roots, or invoke an alternative writer to bypass the conflict.
5. Report the generated files and the project binding. Explain that the user should reconnect MCP and use `listmodels` to check the configured roots and separate consultation/native availability. Existing explicit `CLI_CLIENTS_CONFIG_PATH` overrides still take precedence. A previously registered Claude local-scope server named `clink` can also shadow the generated project registration; report that possibility without changing home configuration.

Setup writes complete project manifests and local role prompts under `.clink/`, plus the project `.mcp.json` registration. It uses bundled defaults for all clients, without copying user settings or credentials. It does not authenticate, install native CLIs, change native trust/permissions, run connectivity probes, or grant admission. Explicit native task execution still requires `action: "start"`; a no-op prompt is not a sandbox.

For a no-argument invocation, the shell operation has this shape. Capture the project first; substitute the plugin path through the host's supported variable expansion:

```sh
project_dir="$(pwd -P)"
cd "${CLAUDE_PLUGIN_ROOT}/../.."
.venv/bin/python -m clink.setup --project "$project_dir"
```

Do not run this default-directory example if the user's requested project differs from the shell's current directory. Never forward raw `$ARGUMENTS` into shell syntax.
