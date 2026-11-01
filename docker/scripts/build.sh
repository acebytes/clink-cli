#!/bin/sh
# Build locally. Never source .env or change native CLI authentication.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec docker compose --project-directory "$project_dir" -f "$project_dir/docker-compose.yml" build clink-mcp
