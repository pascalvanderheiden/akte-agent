#!/usr/bin/env bash
# Bootstrap kratos-agent in fully local mode (Azurite + SQLite).
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env.local ]; then
    (umask 077; printf '%s\n' 'COPILOT_GITHUB_TOKEN=' 'AZURE_TENANT_ID=' 'OBO_API_CLIENT_ID=' 'OBO_CLIENT_SECRET=' 'ALLOWED_CLIENT_APP_IDS=' > .env.local)
    echo "Created .env.local with empty configuration values."
    echo "Edit .env.local and set the Copilot token and Entra OBO configuration before continuing."
    exit 1
fi

docker compose --env-file .env.local up --build
