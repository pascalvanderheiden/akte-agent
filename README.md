# Akte Agent

Bilingual (English/Dutch) AI assistant for notarial work, running on Azure with Microsoft Foundry and the GitHub Copilot SDK.

> Based on [aiappsgbb/kratos-agent](https://github.com/aiappsgbb/kratos-agent). For architecture, request flow, skills/MCP internals, observability and troubleshooting, see the Kratos repo.

## Features

- **Akte Agent persona** — notarial intake, draft follow-ups and exact time records ([intake](docs/akte-intake.md)); execution preparation and client-funds reconciliation ([execution](docs/akte-execution.md)); registration prep, itemized invoice drafts and dossier handoff ([handoff](docs/akte-handoff.md)). Drafts only — official actions stay human.
- **Generic persona** — general-purpose assistant with web search, code interpreter and file downloads.
- **English / Nederlands UI** — language switch keeps conversation, drafts and attachments; agent replies in the selected language.
- **Model picker** — choose the model per conversation; routed sub-agents handle delegated tasks.
- **File sharing** — agent-generated files (CSV, PDF, charts) appear as download links.
- **Signed-in user access (OBO)** — optional MCP server calls Microsoft Graph as the signed-in user.
- **Agent Manager** — edit system prompts, skills, MCP connections and APM packages per persona.
- **Evals & traces** — per-persona eval scenarios and an execution trace viewer, plus live tool/token/latency details per message.
- **Export** — download a persona as a standalone Foundry hosted agent (ZIP with its own `azd up`).

## Deploy to Azure

Prerequisites: [azd](https://learn.microsoft.com/azure/developer/azure-developer-cli/) ≥ 1.28, [Azure CLI](https://learn.microsoft.com/cli/azure/), Node.js 20.9+, Python 3.11+. No local Docker needed (images build in ACR).

1. Clone and sign in:
   ```bash
   git clone https://github.com/pascalvanderheiden/akte-agent && cd akte-agent
   az login && azd auth login
   ```
2. Create an environment:
   ```bash
   azd env new <env-name>
   azd env set DEPLOY_OBO false   # optional: skip the on-behalf-of MCP server
   ```
3. Deploy:
   ```bash
   azd up
   ```
   Pick subscription and region when prompted. At the start you're asked which use-cases to upload — press Enter to skip.
4. Open the app:
   ```bash
   azd env get-value AZURE_STATIC_WEB_APP_URL
   ```
5. Optional — verify the deployment:
   ```bash
   cd .copilot/skills/e2e-smoke && ./run.sh
   ```

Redeploy code only with `azd deploy`; tear down with `azd down`. Optional read-only Azure SRE Agent: see [docs/sre-agent.md](docs/sre-agent.md).

## Run locally

Runs the backend with SQLite and Azurite instead of Cosmos DB and Blob Storage, and a GitHub Copilot token instead of Foundry models. Requires Docker.

1. Create the config file:
   ```bash
   ./run-local.sh        # Windows: .\run-local.ps1 — first run creates .env.local and exits
   ```
2. Fill in `.env.local`: `COPILOT_GITHUB_TOKEN`, `AZURE_TENANT_ID`, `OBO_API_CLIENT_ID`, `OBO_CLIENT_SECRET`, `ALLOWED_CLIENT_APP_IDS`.
3. Start the backend (http://localhost:8000):
   ```bash
   ./run-local.sh
   ```
4. Start the frontend (http://localhost:3000):
   ```bash
   cd src/frontend && npm install && npm run dev
   ```

Local data lives in `.local/`; edits under `use-cases/` apply immediately.

## License

MIT — inherited from [kratos-agent](https://github.com/aiappsgbb/kratos-agent).
