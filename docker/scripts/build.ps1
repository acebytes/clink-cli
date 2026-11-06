#!/usr/bin/env pwsh
#Requires -Version 5.1
# Build locally without reading .env or changing native CLI authentication.
$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
& docker compose --project-directory $ProjectDir -f (Join-Path $ProjectDir "docker-compose.yml") build clink-mcp
exit $LASTEXITCODE
