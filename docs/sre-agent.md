# Azure SRE Agent (opt-in, read-only)

An azd environment can opt into an [Azure SRE Agent](https://learn.microsoft.com/azure/sre-agent/)
that observes *that* environment. It is **off by default**: environments that
have not opted in get no SRE resource, no SRE identity, and no SRE role
assignments, and the SRE hooks make no Azure calls at all.

What this release provisions is the **read-only core**: the agent resource, its
identities, least-privilege discovery permissions, and outputs. Workload
telemetry connectors are separate work. GitHub Code Access setup registers and
reads back the intended repository/branch, but reports `pending` until current
source access can be proven; registration alone is not a working connection.

## Enabling it

```bash
azd env set DEPLOY_SRE_AGENT true
# Core-only: no connector or GitHub calls are attempted at all.
azd env set SRE_CONNECT_TELEMETRY false
azd env set SRE_CONNECT_GITHUB false
azd provision
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `DEPLOY_SRE_AGENT` | `false` | Provision the SRE Agent for this environment |
| `SRE_CONNECT_TELEMETRY` | `true` | Attempt the workload telemetry connectors (not implemented yet → `pending`) |
| `SRE_CONNECT_GITHUB` | `true` | Attempt GitHub Code Access registration and readback |
| `SRE_GITHUB_REPOSITORY_URL` | `https://github.com/pascalvanderheiden/akte-agent` | HTTPS repository URL; `github.com` or an existing GitHub Enterprise Cloud host |
| `SRE_GITHUB_BRANCH` | discovered | Target repository's default branch; never the local checkout branch |
| `SRE_AGENT_NAME_OVERRIDE` | derived | Name override. The default is `<namePrefix>sre-<resourceToken>`, deterministic per environment (`namePrefix` is the optional `AZURE_RESOURCE_PREFIX` plus a hyphen, empty when unset). Preflight accepts 3-63 character lowercase DNS labels, starting with a letter and ending with a letter/digit; Azure still validates service naming/uniqueness. Named separately from the `SRE_AGENT_NAME` output so a provisioned name never becomes an implicit override |
| `SRE_LOCATION` | `AZURE_LOCATION` | Region for the agent, when the application's region does not offer SRE |
| `AZURE_PRINCIPAL_TYPE` | detected by preflight | `User`, `ServicePrincipal`, or explicit `Group`. Preflight persists a detected type with `azd env set`; inability to determine/persist it is a failure. Direct Bicep use defaults to `User`, so CI callers must supply `ServicePrincipal` |

Use lowercase `true` or `false`, matching the ARM parameter contract. Aliases
such as `yes`, `1`, and `TRUE` fail explicitly rather than passing a hook and
then failing ARM. Optional switches are validated when SRE is enabled.

The manual Deploy workflow exposes `deploy_sre_agent`, `sre_location`, and
`sre_agent_name`, `sre_connect_github`, `sre_github_repository_url`, and
`sre_github_branch`. Configuration inputs apply only when `provision` is selected;
telemetry remains off in this slice. Blank overrides clear earlier values.
Deploy-only runs refresh existing outputs and deploy application services;
they never create SRE. When `sre_connect_github` is selected, a dedicated setup
step reruns the existing environment's persisted SRE options after refresh.
Its optional environment secret `SRE_GITHUB_PAT` is scoped to that step alone.
To opt out completely during provisioning, set `sre_connect_github=false`;
on deploy-only runs that input skips the dedicated setup step.

## Core-only mode

`SRE_CONNECT_TELEMETRY=false` and `SRE_CONNECT_GITHUB=false` make the core-only
intent explicit. Hooks read azd/Azure CLI context and provider metadata;
postprovision reads the agent, with bounded readiness retries. No connector,
GitHub, data-plane token, or repository calls are made. Setup reports:

```text
SRE_TELEMETRY_RESULT app_insights=disabled log_analytics=disabled
SRE_RESULT core=ready telemetry=disabled github=disabled
```

Leaving either switch on is also supported; those components are then reported
as `pending` with the reason, and the provision still succeeds.

### The result contract

Every setup process, including configuration/CLI failures, ends with exactly
one `SRE_RESULT` line. Its original three fields and ordering are unchanged.
An additional `SRE_TELEMETRY_RESULT` line reports each telemetry source
independently. Both sources are `pending` or `disabled` in this core-only
release; **neither is verified**.

The shared `sre_result core telemetry github [app_insights] [log_analytics]`
helper in `hooks/sre-lib.sh` preserves the original three-argument interface.
Future telemetry setup supplies per-source states in arguments 4/5 and an
honest aggregate in `telemetry`. `core` uses `disabled`, `ready`, or `failed`;
optional integrations use:

| State | Meaning |
| --- | --- |
| `disabled` | Switched off for this environment; nothing was attempted |
| `ready` | Verified working — not merely created |
| `pending` | Not yet implemented, awaiting authorization, or awaiting propagation |
| `unavailable` | Confirmed unsupported by the service, policy, or tenant |
| `failed` | Attempted and errored |

## Prerequisites and permissions

- **Subscription access to the SRE service.** Registering `Microsoft.App` does
  **not** by itself prove access. Preflight requires advertised agents
  locations and the pinned API version, but provider metadata is not a
  guarantee of service eligibility. Confirm the subscription's available
  regions in the creation wizard at <https://sre.azure.com>.
- **Bash, azd, Azure CLI, and jq; Python 3.11+ and curl for GitHub setup.** The hooks check required tools only when
  enabled. Standalone `--environment` loading needs azd/jq even for a disabled
  environment, but makes no Azure calls when disabled.
- **Azure CLI signed in** to the same subscription as the azd environment. The
  hooks never sign in, install tools, register providers, change consent, or
  switch subscription or region for you; they report what to run.
- **Deployment permissions** for the existing subscription-scoped template
  and resources, plus permission to create role assignments in the
  environment's resource group (for example Contributor plus User Access
  Administrator, or Owner). User Access Administrator alone cannot provision
  infrastructure. These are deployer prerequisites, **not agent grants**.
  The deployment assigns:

  | Principal | Role | Scope |
  | --- | --- | --- |
  | SRE user-assigned identity | Reader | environment resource group |
  | SRE user-assigned identity | Monitoring Reader | environment resource group |
  | Deployer (`principalId`) | SRE Agent Administrator | the SRE Agent resource only |

  No Contributor, no Owner, no subscription-wide grants, and no Entra directory
  writes. The agent's system-assigned identity is provisioned but is granted
  nothing yet — the query roles belong with the telemetry connectors.

## Supported region and access checks

`hooks/sre-preflight.sh` runs at `preprovision`, only when `DEPLOY_SRE_AGENT`
is true, and fails before anything is created when:

- required tools or azd environment context are missing;
- the selected azd environment differs from injected context, or the CLI's
  subscription/tenant differs from that context;
- `Microsoft.App` is not registered;
- `Microsoft.App/agents` metadata/locations are missing or malformed, or the
  pinned API version is not advertised;
- the chosen region does not offer SRE — the message lists the regions that do,
  and the fix is `azd env set SRE_LOCATION <region>`. The application is never
  relocated.

Query failure/denial is a nonzero failure, never success-shaped fallback.
Diagnostics identify the failing operation without dumping CLI responses,
tokens, or invalid input values. Successful metadata checks still explicitly
report service eligibility as unproven: ARM provisioning may reject access.
No old SRE outputs are required for a fresh preflight.

An existing SRE agent cannot be moved between regions. Changing its name
creates a different resource under incremental deployment; the old agent
remains until explicitly cleaned up.

You can run the same checks by hand:

```bash
az provider show --namespace Microsoft.App --query registrationState -o tsv
az provider show --namespace Microsoft.App \
  --query "resourceTypes[?resourceType=='agents'].locations | [0]" -o tsv
```

## Read-only by design

The agent is created with `accessLevel: Low` and `actionMode: Review`: it can
investigate and recommend, and every action needs a human. These are literals
in `infra/modules/sre-agent.bicep`, not parameters, so the template cannot be
configured into an agent that changes your environment. There are no
remediation permissions, no autonomous incident response, no recurring tasks,
and no webhooks.

## Monitoring is reused, never duplicated

The agent logs to the environment's **existing** workspace-backed Application
Insights component, and the existing Log Analytics workspace is retained as-is.
No second component and no second workspace are created.

Note that four values are routinely confused, and are not interchangeable:

| Value | What it is |
| --- | --- |
| `AppId` | The Application Insights *application* ID used by the query API (`SRE_APP_INSIGHTS_APP_ID`) |
| ARM resource ID | The `/subscriptions/.../components/...` path (`SRE_APP_INSIGHTS_ID`) |
| Connection string | Ingestion endpoint + key, used for writing telemetry |
| Instrumentation key | Legacy ingestion key; not used here |

SRE writing its own logs to that component is **not** permission to query your
application's telemetry. That is a connector, and it comes with the telemetry
work.

## Operator outputs

After a successful provision, `azd env get-values` includes:

| Output | Use |
| --- | --- |
| `SRE_AGENT_ENABLED` | Whether this environment opted in |
| `SRE_AGENT_ID` | ARM resource ID — the handle for every later step |
| `SRE_AGENT_NAME` / `SRE_AGENT_LOCATION` | Where it lives |
| `SRE_AGENT_PORTAL_URL` | Portal link |
| `SRE_AGENT_PRINCIPAL_ID` | System-assigned identity (the future connector identity) |
| `SRE_AGENT_IDENTITY_ID` / `SRE_AGENT_IDENTITY_PRINCIPAL_ID` | Discovery identity |
| `SRE_APP_INSIGHTS_APP_ID` / `SRE_APP_INSIGHTS_ID` / `SRE_LOG_ANALYTICS_ID` | Monitoring references for later setup |

The agent's data-plane endpoint is deliberately **not** an output: later steps
resolve it from ARM rather than constructing a hostname or committing an
environment-specific default.

### Extension boundary for telemetry and GitHub

Telemetry (#36) must use `SRE_AGENT_PRINCIPAL_ID` (the system-assigned
connector identity), the distinct `SRE_APP_INSIGHTS_APP_ID`, and the two
monitoring ARM IDs. Discovery/actions use the separate user-assigned identity.
GitHub discovers `properties.agentEndpoint` using `SRE_AGENT_ID` after the
core checks, then requests the explicit `https://azuresre.dev` audience.
The helper never synthesizes a hostname or changes Azure context.

Extend the existing setup/result helpers rather than creating another agent
or duplicating core checks. Core readiness currently verifies ARM
`Succeeded`, environment ownership, location, `Low`/`Review`, discovery
scope, attached identities, and shared logging `AppId`. It does **not**
prove telemetry ingestion, connector query access, or repository access.

## Re-running setup without redeploying

`hooks/sre-setup.sh` is rerunnable on its own against an already-provisioned
environment:

```bash
./hooks/sre-setup.sh --environment <azd-environment>
```

This reads an allowlist from `azd env get-values --output json`, never
shell-evaluates it, and clears stale SRE values inherited from other
environments. Normal azd hooks use their injected environment. No generated
`.azure/` files are edited. Tokens are requested only by enabled integrations.

Readiness attempts are bounded (`SRE_READY_ATTEMPTS`, default 10, allowed
1-60; `SRE_READY_DELAY_SECONDS`, default 15, allowed 0-300). Supply these
optional tuning knobs as process environment variables. Known provisioning
states and allowlisted transient resource-read errors retry; malformed
responses, terminal failure, unknown errors, and permanent denial fail
immediately. Exhaustion is nonzero. Individual calls also depend on the
Azure CLI's own network timeout behavior.

Standalone preflight uses the same selection:
`./hooks/sre-preflight.sh --environment <azd-environment>`.

## GitHub authorization and verification

```bash
azd env set SRE_CONNECT_GITHUB true
azd env set SRE_GITHUB_REPOSITORY_URL https://github.com/pascalvanderheiden/akte-agent
# Optional; otherwise discover this repository's default branch.
azd env set SRE_GITHUB_BRANCH main
./hooks/sre-setup.sh --environment <azd-environment>
```

The hook is noninteractive with closed stdin: it never runs `gh`, opens a
browser, waits for OAuth, or reuses a local GitHub credential. Authenticate
outside the hook in <https://sre.azure.com>, select the environment's agent,
then **Builder > Code Access** (or **Knowledge base > Add repository**).
Complete or renew authorization there and rerun setup. Public visibility does
not replace SRE authorization.

For headless setup, inject a dedicated `SRE_GITHUB_PAT` into the setup process
from your secret manager; never `azd env set` it or put it in a command argument.
Prefer a selected-repository fine-grained token with **Metadata: Read** and
**Contents: Read** only. Microsoft guidance inconsistently calls PATs
fine-grained while listing classic `repo`/`public_repo` scopes; compatibility
with a read-only token is not guaranteed. If the service requires broader
scopes, leave setup pending and complete a read-only BYO App authorization
manually. Do not grant write scopes to make this hook pass. The hook creates
no app, OAuth connector, MCP connector, webhook, or issue/PR capability.
Enterprise Cloud (`*.ghe.com`) requires an existing BYO App; a supplied PAT is
never sent to that host.

Existing matching domain records are candidates, **not proof of usable auth**.
They are preserved even when a PAT was supplied; an expired/ambiguous existing
authorization must be renewed in the portal, not overwritten automatically.
Other domains and registrations are never changed. A new PAT is installed only
when no target-domain record exists. The hook lists registrations, reuses an
exact URL/branch match regardless of its name, or PUTs a deterministic
URL-and-branch-specific name. It reads back the exact URL, branch, and type;
duplicates, name collisions, malformed data, and mismatches fail explicitly.
Changing the branch adds a distinct registration and preserves the old one.

Default-branch and current-commit lookup use GitHub's HTTPS REST API with only
the dedicated PAT, if supplied, otherwise anonymously. A private repo may be
usable by SRE while this independent lookup is unavailable: supply an explicit
branch. Never guess `main` or use the current checkout. A `Ready` clone at the
current GitHub commit is reported as verified **clone evidence**, still
`pending`: a cached clone cannot prove current auth or a fresh source read.
No hook path currently reports GitHub `ready`; even successful registration
requires the manual read-only acceptance below.

`SRE_GITHUB_ATTEMPTS` (default 3, range 1-10) and
`SRE_GITHUB_DELAY_SECONDS` (default 5, range 0-30) bound propagation/readiness
retries. Each CLI call is capped at 45 seconds and each HTTP request at 30
seconds. No OAuth wait loop exists. Missing auth, denied default-branch
discovery, missing ARM endpoint, or unverified clone access are `pending`.
Recognized SRE sign-in/tenant restrictions and HTTP 403/501 are `unavailable`.
Unexpected HTTP/CLI errors, invalid configuration/JSON, network timeout or
transient HTTP retry exhaustion are `failed` with nonzero exit. Core and
independent telemetry results survive optional failures.

PAT JSON uses a private temporary directory (0700) and file (0600); headers
travel to curl on stdin, never in argv. The PAT is removed from child
environments. Request/response files are removed on normal success/failure and
handled termination. Curl ignores user config and does not follow redirects.
Raw CLI/HTTP responses and exception details are suppressed, including auth
errors; tokens do not enter azd values, Bicep, ARM history, summaries or artifacts.
Do not run under an external environment-dumping/debug wrapper.

### API evidence and limits

Checked 2026-09-23 against Microsoft Learn and upstream commit
`53e7b66ef79bccf0b79cc331d5c64bb03b75a4b0`:

- [`apply-extras.sh`](https://github.com/microsoft/sre-agent/blob/53e7b66ef79bccf0b79cc331d5c64bb03b75a4b0/sreagent-templates/bicep/apply-extras.sh)
  and [`Apply-Extras.ps1`](https://github.com/microsoft/sre-agent/blob/53e7b66ef79bccf0b79cc331d5c64bb03b75a4b0/sreagent-templates/bicep/Apply-Extras.ps1)
  agree on domain normalization `github.com` -> `github_com`,
  `PUT /api/v2/github/domains/{normalized-domain}` with `{authType:"Pat",pat:...}`,
  and `PUT /api/v2/repos/{name}` with
  `{name,type:"CodeRepo",properties:{url,type:"GitHub",branch}}`.
  One older bash section spells the route `github.com`; this implementation
  uses the normalized route corroborated by both scripts, without fallback.
  The deprecated `GitHubOAuth` connector is not used.
- [`verify-agent.sh`](https://github.com/microsoft/sre-agent/blob/53e7b66ef79bccf0b79cc331d5c64bb03b75a4b0/sreagent-templates/bin/verify-agent.sh)
  establishes domain `.values` and repository `.value`/array list envelopes.
  Domain names accept dotted or normalized spelling; unknown shapes fail rather
  than treating any nonempty collection as authorized.
- [`bootstrap-agent.ps1`, `Wait-ForRepositoryCommit`](https://github.com/microsoft/sre-agent/blob/53e7b66ef79bccf0b79cc331d5c64bb03b75a4b0/labs/onboardinglab/scripts/bootstrap-agent.ps1)
  corroborates exact URL, `properties.cloneStatus == "Ready"` and
  `properties.latestCommit`; this hook also checks the branch. These fields
  lack a pinned freshness/auth-liveness guarantee.
- [API reference](https://learn.microsoft.com/azure/sre-agent/api-reference)
  documents repository GET/list and `POST /api/v2/repos/{name}/test`, but no
  test response schema proving a fresh branch read. The hook does not invent
  that schema, call chat, or upgrade cached evidence to `ready`.
- [GitHub authentication/permissions](https://learn.microsoft.com/azure/sre-agent/github-connector)
  and [BYO App](https://learn.microsoft.com/azure/sre-agent/connect-github-enterprise-cloud)
  document host restrictions and read-only app permissions.

### Optional live GitHub acceptance

Only in an explicitly authorized environment: run setup, inspect Code Access
for the exact repository/branch, test the connection there, then ask SRE to
read a known file from that branch and compare its returned contents/commit
with GitHub. Do not ask it to create a PR, issue, or deployment. Record the
actual access outcome separately from the hook's conservative `pending`.
Rerun setup and confirm the same repository name, no duplicate registration,
no domain replacement, and unchanged independent integration results. Revoke
or expire auth and confirm a cached clone is never presented as fresh access.
None of these paid/live checks run automatically in CI.

## Cost and usage

The SRE Agent is a **billable** Azure resource, charged on agent usage while it
exists. That is the reason for default-off: an environment that never opts in
is unchanged and costs nothing extra. Review current pricing before enabling it
on a long-lived environment.

## Disabling and cleanup

> [!IMPORTANT]
> Setting `DEPLOY_SRE_AGENT` back to `false` stops *future* provisioning from
> creating an agent. It does **not** delete an agent that already exists: azd
> deploys the template incrementally, and ARM does not remove resources that
> simply stopped being declared. A billable agent left behind keeps billing.

Cleanup is explicit. Capture references **before** reprovisioning with the flag
off: a disabled provision empties SRE outputs even though resources remain.

```bash
AGENT_ID="$(azd env get-value SRE_AGENT_ID)"
IDENTITY_ID="$(azd env get-value SRE_AGENT_IDENTITY_ID)"
IDENTITY_PRINCIPAL_ID="$(azd env get-value SRE_AGENT_IDENTITY_PRINCIPAL_ID)"
# Review these identifiers and associated role assignments before deleting.
azd env set DEPLOY_SRE_AGENT false
az resource delete --ids "$AGENT_ID" --api-version 2025-05-01-preview
```

If already disabled/reprovisioned, recover the exact SRE resource/identity
references from Azure before cleanup; never guess a target. Remove the
Reader/Monitoring Reader assignments belonging to `IDENTITY_PRINCIPAL_ID`
at this environment's resource group, then delete `IDENTITY_ID` if no longer
used. These are explicit operator actions; hooks never delete or roll back.
Do **not** delete the Application Insights component or the Log
Analytics workspace: they are shared with the application and predate SRE.
`azd down` removes the whole environment, SRE included.

## Troubleshooting

| Symptom | What it means |
| --- | --- |
| `DEPLOY_SRE_AGENT must be true or false` | A malformed value; fix it with `azd env set DEPLOY_SRE_AGENT false` |
| Missing/malformed agents metadata or failed provider query | Availability unverified: check provider-read permissions, network, and the service creation wizard; do not assume registration proves eligibility |
| Region not offered | Set `SRE_LOCATION` to one of the listed regions |
| Context differs | Clear stale shell exports; select the intended azd environment and Azure CLI subscription/tenant yourself |
| Missing SRE outputs | Provisioning did not run with SRE enabled, or outputs are stale — `azd provision`, or `azd env refresh` |
| `did not become ready after N attempts` | Still provisioning/transient read failures; inspect Azure and re-run with `--environment` |
| Core verification failed | Read-only settings, identity, scope, region, or logging target drifted; inspect before correcting, never auto-widen permissions |
| Unknown/permanent resource read failure | Check permissions/network and inspect Azure privately; raw diagnostics are intentionally suppressed |
| `RoleAssignmentUpdateNotPermitted` on redeploy | An assignment exists with a different principal type — check `AZURE_PRINCIPAL_TYPE` matches how you signed in |

## Manual acceptance (optional, explicitly authorized environments only)

Automated tests for this feature are cloud-free. Deploying to Azure is never an
automatic part of implementation or CI. If you want to verify a real
environment, use a **disposable** one you are authorized to create resources in:

```bash
azd env new sre-accept --location eastus2
azd env set DEPLOY_SRE_AGENT true
azd env set SRE_CONNECT_TELEMETRY false
azd env set SRE_CONNECT_GITHUB false
azd provision
```

Then check:

1. **Readiness** — the run ends with `SRE_RESULT core=ready telemetry=disabled github=disabled`.
2. **Read-only configuration** —
   `az resource show --ids "$(azd env get-value SRE_AGENT_ID)" --api-version 2025-05-01-preview --query "properties.actionConfiguration"`
   reports `accessLevel: Low` and `mode: Review`.
3. **Reused monitoring** — the same query's `properties.logConfiguration` points
   at the environment's existing component, and the resource group contains
   exactly one Application Insights component and one Log Analytics workspace.
4. **Scope** — `properties.knowledgeGraphConfiguration.managedResources`
   contains only this environment's resource group.
5. **Convergence** — run `azd provision` again and re-run
   `./hooks/sre-setup.sh --environment sre-accept`: no duplicate agent, identity, or role assignment, and
   the same result line.

Finish with `azd down` so the disposable environment stops costing money.

## References

- [Deploy SRE Agent with IaC](https://learn.microsoft.com/azure/sre-agent/deploy-iac)
- [Supported regions](https://learn.microsoft.com/azure/sre-agent/supported-regions)
- [Create and set up SRE Agent](https://learn.microsoft.com/azure/sre-agent/usage)
- [microsoft/sre-agent templates](https://github.com/microsoft/sre-agent) — the
  resource contract pinned here (`Microsoft.App/agents@2025-05-01-preview`) and
  the identity model follow commit `53e7b66`.

Microsoft Learn guidance and the pinned core template were rechecked on
2026-09-23. Local compilation and fake-CLI tests verify the submitted contract,
not live subscription eligibility, resource readiness, or paid service access.
