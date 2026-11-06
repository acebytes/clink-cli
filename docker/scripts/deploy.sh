#!/bin/sh
# Compatibility filename: attach a stdio MCP session, not a background deployment.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec docker compose --project-directory "$project_dir" -f "$project_dir/docker-compose.yml" run --rm --no-deps -T clink-mcp
