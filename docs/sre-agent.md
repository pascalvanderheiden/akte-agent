# Azure SRE Agent (opt-in, read-only)

An azd environment can opt into an [Azure SRE Agent](https://learn.microsoft.com/azure/sre-agent/)
that observes *that* environment. It is **off by default**: environments that
have not opted in get no SRE resource, no SRE identity, and no SRE role
assignments, and the SRE hooks make no Azure calls at all.

What this release provisions is the **read-only core**: the agent resource, its
identities, least-privilege discovery permissions, and outputs. Workload
telemetry connectors and GitHub repository attachment are separate pieces of
work and are reported as `pending` rather than pretended to be connected.

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
| `SRE_CONNECT_GITHUB` | `true` | Attempt repository attachment (not implemented yet → `pending`) |
| `SRE_AGENT_NAME_OVERRIDE` | derived | Name override. The default is `<AZURE_RESOURCE_PREFIX->sre-<resourceToken>`, deterministic per environment. Named separately from the `SRE_AGENT_NAME` output so a provisioned name never becomes an implicit override |
| `SRE_LOCATION` | `AZURE_LOCATION` | Region for the agent, when the application's region does not offer SRE |
| `AZURE_PRINCIPAL_TYPE` | `User` | Principal type of the deployer; preflight sets it from the current Azure CLI session |

A malformed boolean (`ture`, `on`, …) is an error, not a silent "off".

## Core-only mode

`SRE_CONNECT_TELEMETRY=false` and `SRE_CONNECT_GITHUB=false` make the core-only
intent explicit. In that mode the hooks issue exactly one Azure call — a
readiness check on the agent — and report:

```text
SRE_RESULT core=ready telemetry=disabled github=disabled
```

Leaving either switch on is also supported; those components are then reported
as `pending` with the reason, and the provision still succeeds.

### The result contract

Every setup run ends with one machine-readable line, with core and each
optional integration reported independently:

| State | Meaning |
| --- | --- |
| `disabled` | Switched off for this environment; nothing was attempted |
| `ready` | Verified working — not merely created |
| `pending` | Needs an authorization or a value a hook must not invent |
| `unavailable` | Confirmed unsupported by the service, policy, or tenant |
| `failed` | Attempted and errored |

## Prerequisites and permissions

- **Subscription access to the SRE service.** Availability is granted per
  subscription and per region. Registering the `Microsoft.App` provider does
  **not** by itself make the service available — the preflight hook checks
  whether the `Microsoft.App/agents` resource type is actually offered to your
  subscription and refuses to guess.
- **Azure CLI signed in** to the same subscription as the azd environment. The
  hooks never sign in, install tools, register providers, change consent, or
  switch subscription or region for you; they report what to run.
- **Permission to create role assignments** in the environment's resource
  group (`Owner` or `User Access Administrator`). The deployment assigns:

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

- the Azure CLI is missing or has no active session;
- the CLI's subscription differs from the azd environment's;
- `Microsoft.App` is not registered;
- `Microsoft.App/agents` is not offered to the subscription;
- the chosen region does not offer SRE — the message lists the regions that do,
  and the fix is `azd env set SRE_LOCATION <region>`. The application is never
  relocated.

If the availability query itself fails, the hook says so and continues. That is
an *unverified* state, not a confirmation of eligibility: the provision may
still fail against ARM.

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

## Re-running setup without redeploying

`hooks/sre-setup.sh` is rerunnable on its own against an already-provisioned
environment:

```bash
eval "$(azd env get-values | sed 's/^/export /')" && ./hooks/sre-setup.sh
```

It is noninteractive, converges on repeat runs, and its readiness retry is
bounded (`SRE_READY_ATTEMPTS`, default 10; `SRE_READY_DELAY_SECONDS`, default
15) — it reports exhaustion rather than hanging.

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

Cleanup is explicit:

```bash
azd env set DEPLOY_SRE_AGENT false
az resource delete --ids "$(azd env get-value SRE_AGENT_ID)" \
  --api-version 2025-05-01-preview
```

Delete the agent's user-assigned identity (`SRE_AGENT_IDENTITY_ID`) too if you
want it gone. Do **not** delete the Application Insights component or the Log
Analytics workspace: they are shared with the application and predate SRE.
`azd down` removes the whole environment, SRE included.

## Troubleshooting

| Symptom | What it means |
| --- | --- |
| `DEPLOY_SRE_AGENT must be true or false` | A malformed value; fix it with `azd env set DEPLOY_SRE_AGENT false` |
| `The Microsoft.App/agents resource type is not offered…` | The subscription has no SRE access — request it, or set `DEPLOY_SRE_AGENT=false` |
| `Region '<x>' does not offer the SRE Agent` | Set `SRE_LOCATION` to one of the listed regions |
| `The Azure CLI is pointed at a different subscription` | Run the printed `az account set` yourself |
| `this environment has no SRE_AGENT_ID` | Provisioning did not run with SRE enabled, or outputs are stale — `azd provision`, or `azd env refresh` |
| `did not become ready after N attempts` | Still provisioning; re-run `./hooks/sre-setup.sh` |
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
   `./hooks/sre-setup.sh`: no duplicate agent, identity, or role assignment, and
   the same result line.

Finish with `azd down` so the disposable environment stops costing money.

## References

- [Deploy SRE Agent with IaC](https://learn.microsoft.com/azure/sre-agent/deploy-iac)
- [Supported regions](https://learn.microsoft.com/azure/sre-agent/supported-regions)
- [microsoft/sre-agent templates](https://github.com/microsoft/sre-agent) — the
  resource contract pinned here (`Microsoft.App/agents@2025-05-01-preview`) and
  the identity model follow commit `53e7b66`.
