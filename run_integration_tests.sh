#!/bin/sh
# Offline MCP transport/contracts by default; native inference requires --live.
set -eu
clink_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$clink_root"
if [ "${1:-}" = "--live" ]; then
    shift
    exec "$clink_root/.venv/bin/python" "$clink_root/scripts/smoke_cli.py" --live "$@"
fi
exec "$clink_root/.venv/bin/python" "$clink_root/communication_simulator_test.py" "$@"
