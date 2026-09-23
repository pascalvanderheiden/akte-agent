#!/usr/bin/env pwsh
# Bootstrap kratos-agent in fully local mode (Azurite + SQLite).
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".env.local")) {
    @(
        "COPILOT_GITHUB_TOKEN="
        "AZURE_TENANT_ID="
        "OBO_API_CLIENT_ID="
        "OBO_CLIENT_SECRET="
        "ALLOWED_CLIENT_APP_IDS="
    ) | Set-Content ".env.local"
    Write-Host "Created .env.local with empty configuration values." -ForegroundColor Yellow
    Write-Host "Edit .env.local and set the Copilot token and Entra OBO configuration before continuing." -ForegroundColor Yellow
    exit 1
}

docker compose --env-file .env.local up --build
