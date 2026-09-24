# Azure SRE Agent (opt-in, read-only)

An azd environment can opt into an [Azure SRE Agent](https://learn.microsoft.com/azure/sre-agent/)
that observes *that* environment. It is **off by default**: environments that
have not opted in get no SRE resource, no SRE identity, and no SRE role
assignments, and the SRE hooks make no Azure calls at all.

The core provisions the agent, its identities, discovery permissions, and
outputs. An isolated postprovision phase configures **read-only workload
telemetry connectors**, enabled by default. Configuration is reported separately
from proven query access. GitHub repository attachment remains a separate phase.

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
| `SRE_CONNECT_TELEMETRY` | `true` | Configure and read back both workload connectors and narrow query RBAC; `pending` until connector-identity queries are verified |
| `SRE_CONNECT_GITHUB` | `true` | Attempt repository attachment (not implemented yet → `pending`) |
| `SRE_AGENT_NAME_OVERRIDE` | derived | Name override. The default is `<namePrefix>sre-<resourceToken>`, deterministic per environment (`namePrefix` is the optional `AZURE_RESOURCE_PREFIX` plus a hyphen, empty when unset). Preflight accepts 3-63 character lowercase DNS labels, starting with a letter and ending with a letter/digit; Azure still validates service naming/uniqueness. Named separately from the `SRE_AGENT_NAME` output so a provisioned name never becomes an implicit override |
| `SRE_LOCATION` | `AZURE_LOCATION` | Region for the agent, when the application's region does not offer SRE |
| `AZURE_PRINCIPAL_TYPE` | detected by preflight | `User`, `ServicePrincipal`, or explicit `Group`. Preflight persists a detected type with `azd env set`; inability to determine/persist it is a failure. Direct Bicep use defaults to `User`, so CI callers must supply `ServicePrincipal` |

Use lowercase `true` or `false`, matching the ARM parameter contract. Aliases
such as `yes`, `1`, and `TRUE` fail explicitly rather than passing a hook and
then failing ARM. Optional switches are validated when SRE is enabled.

The manual Deploy workflow exposes `deploy_sre_agent`, `sre_location`,
`sre_agent_name`, and `sre_connect_telemetry` (default `true`). They apply only
when `provision` is selected; GitHub remains disabled in this workflow slice.
Blank overrides clear earlier values.
Deploy-only runs refresh existing outputs and deploy application services;
they neither configure nor create SRE.

## Core-only mode

`SRE_CONNECT_TELEMETRY=false` and `SRE_CONNECT_GITHUB=false` make the core-only
intent explicit. Hooks read azd/Azure CLI context and provider metadata;
postprovision reads the agent, with bounded readiness retries. No connector,
GitHub, data-plane token, or repository calls are made. Setup reports:

```text
SRE_TELEMETRY_RESULT app_insights=disabled log_analytics=disabled
SRE_RESULT core=ready telemetry=disabled github=disabled
```

Leaving telemetry on invokes the optional declaration after core verification.
Expected authorization/policy restrictions retain the core and produce a degraded
result. Unexpected failures and exhausted retries return nonzero.

### The result contract

Every setup process, including configuration/CLI failures, ends with exactly
one `SRE_RESULT` line. Its original three fields and ordering are unchanged.
An additional `SRE_TELEMETRY_RESULT` line reports each telemetry source
independently. The setup hook reports a successfully configured/read-back source
as `pending`, **not `ready`**: ARM success cannot prove query access. The pinned
public connector contract does not document a headless identity-bound query
verification API. No endpoint or success evidence is invented.

The shared `sre_result core telemetry github [app_insights] [log_analytics]`
helper in `hooks/sre-lib.sh` preserves the original three-argument interface.
Telemetry supplies per-source states in arguments 4/5 and an aggregate in
`telemetry` (precedence: `failed`, `unavailable`, `pending`). `core` uses `disabled`, `ready`, or `failed`;
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
- **Bash, azd, Azure CLI, and jq.** The hooks check required tools only when
  enabled. Standalone `--environment` loading needs azd/jq even for a disabled
  environment, but makes no Azure calls when disabled.
- **Installed Bicep CLI managed by Azure CLI**, for enabled telemetry. Install
  it yourself with `az bicep install` before setup. Hooks check `az bicep version`,
  compile the local template, and submit private temporary ARM JSON; they never
  install/upgrade a compiler or download a remote template.
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

  Optional telemetry additionally grants the **system-assigned** agent identity:

  | Principal | Role | Scope |
  | --- | --- | --- |
  | SRE system-assigned identity | Monitoring Reader | existing Application Insights component |
  | SRE system-assigned identity | Log Analytics Reader | existing Log Analytics workspace |

  No Contributor, no Owner, no subscription-wide grants, and no Entra directory
  writes. The setup caller needs deployment/connector write permission in the
  environment resource group and role-assignment permission at these monitoring
  resources. These caller privileges are not granted to the agent.

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
application's telemetry. The optional workload connectors and query RBAC are
declared separately in `infra/sre-telemetry.bicep`.

## Workload telemetry setup

```bash
azd env set DEPLOY_SRE_AGENT true
azd env set SRE_CONNECT_TELEMETRY true
azd env set SRE_CONNECT_GITHUB false
azd provision
```

Postprovision verifies the core, then configures `app-insights` and
`log-analytics` independently through `az deployment group create`, in
**Incremental** mode. Each call selects one source from the same local Bicep
template. Connector names and scoped role-assignment GUIDs are deterministic.
Deployment names prefix the complete agent name with `a` or `l`, retaining
uniqueness even for a 63-character agent name.

The hook checks selected azd outputs, monitoring resource ownership, the actual
Application Insights `AppId` and backing workspace, then reads back each
connector's type, target, extended properties, and `identity: system`.
The system principal comes from the verified core, not its discovery identity.
The other source is still attempted after a restriction or unexpected failure;
no rollback deletes a working connector, core, or shared monitoring resource.

Example after both configurations pass:

```text
Telemetry app-insights configured/read back; pending connector-identity query verification (not ready).
Telemetry log-analytics configured/read back; pending connector-identity query verification (not ready).
SRE_TELEMETRY_RESULT app_insights=pending log_analytics=pending
SRE_RESULT core=ready telemetry=pending github=disabled
```

`AuthorizationFailed`, `LinkedAuthorizationFailed`, or `Forbidden` produce
`pending` without repeatedly trying a permanent denial. Confirmed
`RequestDisallowedByPolicy` or `SubscriptionNotRegistered` produce `unavailable`.
Unknown errors, invalid API versions, `NoRegisteredProviderFound` (which can
mean a template/API defect), malformed responses, drift, and compilation errors
are `failed` and nonzero. Nested ARM errors are classified by their leaf codes;
a defect mixed with a restriction still fails.

Transient reads, `PrincipalNotFound` propagation, throttling, and known
in-progress connector states have bounded retries using the same
`SRE_READY_ATTEMPTS` / `SRE_READY_DELAY_SECONDS` settings as core readiness.
Exhaustion remains `pending` but **returns nonzero**, never `ready`. Azure CLI's
own network timeout applies to each individual request. Authorization denials
are not treated as propagation.

After an administrator resolves access/policy, or resources finish propagating:

```bash
azd env refresh --environment <azd-environment> --no-prompt
./hooks/sre-setup.sh --environment <azd-environment>
```

This does not deploy application services or create another core agent. A
fresh manual workflow provision uses postprovision's newly generated outputs;
deploy-only refresh/deploy never runs connector setup. Disable telemetry with
`azd env set SRE_CONNECT_TELEMETRY false` for core-only reruns. Disabling skips all
workload connector/query-RBAC changes; it does not remove previously created
connectors or role assignments.

### Optional authorized query verification

In a disposable, explicitly authorized environment, open the existing SRE
agent's Chat after setup. For each source, ask SRE to use the **named connector**,
not the general Azure CLI tool or the operator's credentials:

1. `app-insights`: run `requests | where timestamp > ago(1h) | take 1`.
2. `log-analytics`: inspect available tables, select one existing table, and run
   `<table> | where TimeGenerated > ago(1h) | take 1`.

Inspect the tool card/trace: require actual query execution, the selected
environment's exact target, and the connector's system-assigned identity
matching `SRE_AGENT_PRINCIPAL_ID`. Confirm principal attribution using available
service diagnostics if the tool card does not expose it. A narrative answer, a
generic tool, a saved connector, or an operator-run query is insufficient.
Successful execution with zero rows proves access but not recent ingestion;
403/unauthorized, missing tables, and transport failures are different outcomes.
Allow only a bounded retry window for RBAC propagation; keep access unverified
if identity or execution cannot be established. Never widen permissions or
disable network controls just to make a query pass.

Record per-source query evidence privately, not in this public repository.
The automated setup remains `pending` on subsequent runs; it does not accept
operator-supplied claims as machine-verifiable query proof. Repeat setup to
confirm the same connector names/roles, then clean up the disposable environment.

## Operator outputs

After a successful provision, `azd env get-values` includes:

| Output | Use |
| --- | --- |
| `SRE_AGENT_ENABLED` | Whether this environment opted in |
| `SRE_AGENT_ID` | ARM resource ID — the handle for every later step |
| `SRE_AGENT_NAME` / `SRE_AGENT_LOCATION` | Where it lives |
| `SRE_AGENT_PORTAL_URL` | Portal link |
| `SRE_AGENT_PRINCIPAL_ID` | System-assigned workload connector identity |
| `SRE_AGENT_IDENTITY_ID` / `SRE_AGENT_IDENTITY_PRINCIPAL_ID` | Discovery identity |
| `SRE_APP_INSIGHTS_APP_ID` / `SRE_APP_INSIGHTS_ID` / `SRE_LOG_ANALYTICS_ID` | Distinct monitoring references for connector setup |

The agent's data-plane endpoint is deliberately **not** an output: later steps
resolve it from ARM rather than constructing a hostname or committing an
environment-specific default.

### Extension boundary for telemetry and GitHub

Telemetry uses `SRE_AGENT_PRINCIPAL_ID` (the system-assigned
connector identity), the distinct `SRE_APP_INSIGHTS_APP_ID`, and the two
monitoring ARM IDs. Discovery/actions use the separate user-assigned identity.
GitHub (#37) must discover `properties.agentEndpoint` using `SRE_AGENT_ID`
before authenticated data-plane calls. Telemetry does not require that
data-plane endpoint, GitHub credentials, or a token acquired for the operator.

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
`.azure/` files are edited, and no authentication tokens are requested.

Readiness attempts are bounded (`SRE_READY_ATTEMPTS`, default 10, allowed
1-60; `SRE_READY_DELAY_SECONDS`, default 15, allowed 0-300). Supply these
optional tuning knobs as process environment variables. Known provisioning
states and allowlisted transient resource-read errors retry; malformed
responses, terminal failure, unknown errors, and permanent denial fail
immediately. Exhaustion is nonzero. Individual calls also depend on the
Azure CLI's own network timeout behavior.

Standalone preflight uses the same selection:
`./hooks/sre-preflight.sh --environment <azd-environment>`.

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
used. If telemetry was enabled, also review/remove the system-assigned
principal's Monitoring Reader assignment on the component and Log Analytics
Reader assignment on the workspace. Capture that principal before deleting
the agent. These are explicit operator actions; hooks never delete or roll back.
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
- [Connect Log Analytics and Application Insights](https://learn.microsoft.com/azure/sre-agent/setup-log-analytics-connector)
- [Telemetry query tool verification](https://learn.microsoft.com/azure/sre-agent/connect-telemetry-source)
- [Log Analytics resource-scoped permissions](https://learn.microsoft.com/azure/azure-monitor/logs/manage-access)
- [Pinned ARM connector contract](https://github.com/microsoft/sre-agent/blob/53e7b66ef79bccf0b79cc331d5c64bb03b75a4b0/sreagent-templates/bicep/agent-extensions.bicep)
- [microsoft/sre-agent templates](https://github.com/microsoft/sre-agent) — the
  resource contract pinned here (`Microsoft.App/agents@2025-05-01-preview`) and
  the identity model follow commit `53e7b66`.

Microsoft Learn guidance and the pinned core template were rechecked on
2026-09-23. Local compilation and fake-CLI tests verify the submitted contract,
not live subscription eligibility, resource readiness, or paid service access.
