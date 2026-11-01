#!/usr/bin/env pwsh
#Requires -Version 5.1
# Compatibility filename: attach stdio rather than deploy a background service.
$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
& docker compose --project-directory $ProjectDir -f (Join-Path $ProjectDir "docker-compose.yml") run --rm --no-deps -T clink-mcp
exit $LASTEXITCODE
