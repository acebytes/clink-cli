#!/bin/sh
# clink-mcp-server launcher, derived from PAL MCP Server.
# Copyright 2025 Beehive Innovations. Licensed under Apache-2.0; see LICENSE.
set -eu

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
venv_python="$script_dir/.venv/bin/python"

usage() {
    cat <<'EOF'
Usage: run-server.sh [--project EXISTING_DIR | --setup | --help]

  --setup    Create .venv if needed, install requirements.txt, and exit.
  --project  Bind project manifests and default workspace roots to EXISTING_DIR.
  --help     Show this help.
  no option  Start the MCP stdio server using .venv/bin/python.

Setup never creates .env or changes native CLI/client configuration.
See docs/getting-started.md and docs/migration.md for configuration.
EOF
}

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

check_python() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

project_dir=
case "${1-}" in
    -h|--help)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        usage
        exit 0
        ;;
    --setup)
        [ "$#" -eq 1 ] || { usage >&2; exit 2; }
        if [ ! -x "$venv_python" ]; then
            setup_python=
            for candidate in python3 python; do
                if command -v "$candidate" >/dev/null 2>&1 && check_python "$candidate"; then
                    setup_python=$candidate
                    break
                fi
            done
            [ -n "$setup_python" ] || fail 'A supported Python interpreter (3.10 or newer) is required. Python 3.12 is recommended.'
            "$setup_python" -m venv "$script_dir/.venv"
        fi
        check_python "$venv_python" || fail 'The project .venv needs Python 3.10 or newer. Recreate it with a supported interpreter.'
        "$venv_python" -m pip install -r "$script_dir/requirements.txt" >&2
        printf '%s\n' 'Setup complete. Start the MCP server with ./run-server.sh or configure your MCP client to launch it.' >&2
        exit 0
        ;;
    --project)
        if [ "$#" -ne 2 ]; then
            printf '%s\n' '--project requires exactly one existing directory.' >&2
            usage >&2
            exit 2
        fi
        [ -n "$2" ] || fail '--project must be a nonempty directory path.'
        project_dir=$(CDPATH= cd -P -- "$2" 2>/dev/null && pwd) || fail '--project must be an existing directory.'
        ;;
    '')
        [ "$#" -eq 0 ] || { usage >&2; exit 2; }
        ;;
    *)
        printf '%s\n' 'Unsupported launcher option. See docs/migration.md for the new setup and logging commands.' >&2
        usage >&2
        exit 2
        ;;
esac

[ -x "$venv_python" ] || fail 'Project .venv is missing. Run ./run-server.sh --setup first.'
check_python "$venv_python" || fail 'The project .venv needs Python 3.10 or newer. Recreate it with a supported interpreter.'
if ! "$venv_python" -c 'import importlib.util; raise SystemExit(0 if all(importlib.util.find_spec(name) is not None for name in ("mcp", "pydantic", "dotenv")) else 1)' >/dev/null 2>&1; then
    fail 'Project dependencies are missing. Run ./run-server.sh --setup to install requirements.txt.'
fi

cd "$script_dir"
if [ -n "$project_dir" ]; then
    exec "$venv_python" "$script_dir/server.py" --project "$project_dir"
fi
exec "$venv_python" "$script_dir/server.py"
