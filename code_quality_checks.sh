#!/bin/sh
# Check the working tree without autofix, installation, CLI calls or API access.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    python_cmd="$VIRTUAL_ENV/bin/python"
elif [ -x "$project_dir/.venv/bin/python" ]; then
    python_cmd="$project_dir/.venv/bin/python"
else
    printf '%s\n' 'No managed environment found. Run ./run-server.sh --setup and install requirements-dev.txt first.' >&2
    exit 1
fi
"$python_cmd" -m ruff check .
"$python_cmd" -m black --check .
"$python_cmd" -m isort --check-only .
"$python_cmd" -m pytest tests/ -m 'not integration and not real_cli'
