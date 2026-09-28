---
status: accepted
---

# Hosted agent reads skills from the local packaged `use-cases/`

The Foundry hosted-agent surface (`src/hosted-agent`) loads its system prompt
and skills from the `use-cases/` directory baked into its image, and treats
Blob Storage as an optional accelerator that is normally absent. Local-only is
an **accepted, first-class mode** for this surface — not a degraded state, and
not a bug to re-investigate each time it is observed.

## Why hosted-agent compute cannot reach the skills Blob account

Three existing infra facts combine into this:

- The skills storage account is reachable **only** through a private endpoint.
  `infra/modules/blob-storage.bicep` creates the blob private endpoint and the
  `privatelink.blob.*` DNS zone into the project VNet, and everything that
  needs the skills container is expected to resolve it from inside that VNet.
- A subscription policy re-applies `publicNetworkAccess: Disabled` on that
  account within seconds. `az storage account update --public-network-access
  Enabled` reports success and then silently reverts, so "just open it up" is
  not available, from a runner, a laptop, or Foundry compute.
- The Container App backend is VNet-injected — `infra/main.bicep` passes
  `network.outputs.containerAppsSubnetId` into the container apps environment —
  so it resolves the private endpoint and reaches the container. Foundry
  hosted-agent compute is **not** VNet-injected; it runs on platform-managed
  compute with no attachment to this VNet. Its requests to the private endpoint
  are dropped rather than refused, which is why the code bounds and
  short-circuits them instead of waiting out SDK retries.

The consequence is structural, not transient: from hosted-agent compute the
skills container is unreachable, and no RBAC change fixes it. An
`AuthorizationFailure` or "request may be blocked by network rules" seen there
is a *network* denial — check the topology before touching role assignments.

## What this means for skill authors

Hosted-agent runtime **does not pick up Blob-only skill edits.** A skill or
system-prompt change that is written straight to the skills container (for
example through the admin API or a blob upload) is visible to the Container App
backend and invisible to the hosted agent.

To change what the hosted agent runs, ship the change through the local
packaged artifact: edit `use-cases/` in the repo and rebuild/redeploy the
hosted-agent image, which copies `use-cases/` into `/app/use-cases`
(`src/hosted-agent/Dockerfile`). A Blob-only update is not a delivery path for
this surface.

## Out of scope: giving hosted-agent compute a private-network path

Attaching Foundry hosted-agent compute to the project VNet — the way the
Container App backend already is, via `containerAppsSubnetId` — or any other
supported private-network path to the skills account, is a **separate,
platform-capability-dependent follow-up**. It depends on what Foundry supports
for hosted-agent network injection, not on anything this repo can decide
unilaterally, and it is explicitly not implemented by the Blob-reachability
short-circuit that ships alongside this decision. Until such a path exists and
is adopted, local-only remains the intended behaviour of the hosted agent.
