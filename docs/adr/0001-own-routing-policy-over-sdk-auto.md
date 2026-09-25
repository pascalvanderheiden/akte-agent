---
status: accepted
---

# Own routing policy over SDK Auto

The agent will own model selection through three stable Routing Roles
(`orchestrator`, `deep-reasoning`, and `fast`) rather than delegate routing to
an SDK or platform feature. In Azure mode, the Copilot SDK wiring is one named
provider per Foundry deployment. Models use provider-qualified IDs. This keeps
direct Foundry and APIM gateway traffic structurally identical and lets every
provider share the same bearer-token callback.

SDK `auto` and `auto_tier` are rejected because they are CAPI-only and cannot
express the Azure BYOK deployments used here. Foundry Model Router is rejected
because it moves the routing policy outside the application, cannot preserve
the three explicit role contracts, and does not provide the same direct/APIM
provider catalogue.

## Providers use the Responses API, not deployment-path completions

The wiring originally preferred here — a deployment-path base URL
(`{endpoint}/openai/deployments/{deployment}`) with `wire_api: "completions"`
and a pinned `azure.api_version` — **does not work on GPT-6** as soon as
function tools are in play, which is always true for this agent because
delegation itself is a tool. Live verification returned:

```
400 Function tools with reasoning_effort are not supported for gpt-6-luna in
/v1/chat/completions. To use function tools, use /v1/responses or set
reasoning_effort to 'none'.
```

Setting `reasoning_effort: "none"` on the session config and on every custom
agent does not clear the error; the SDK engine sends its own effort regardless,
so the application cannot opt out. The documented fallback is therefore adopted
as the decision: each provider uses the unversioned `{endpoint}/openai/v1` base
URL with `wire_api: "responses"` and no `azure` block. Per-provider model
catalogue entries keep `wire_model` set to the Foundry deployment name, so the
provider-qualified IDs and the direct/APIM symmetry are both preserved.

## Verification

Verified against a live Foundry account, with `GITHUB_TOKEN` and `GH_TOKEN`
removed from the environment and auth supplied only by an Azure credential:

- A session on `orchestrator/gpt-6-luna` delegated to a custom agent pinned to
  `deep-reasoning/gpt-6-sol`. `AzureOpenAIRequests` split by
  `ModelDeploymentName` recorded traffic on **both** deployments in the same
  window, proving the A→B hop crossed two distinct deployments.
- Provider-qualified model IDs and the plural `providers` surface both work.
- `gpt-6-luna`, `gpt-6-sol`, and `gpt-6-astra` are deployed `GlobalStandard`
  in the environment region with `Succeeded` provisioning state and non-zero
  capacity.

The same wiring is what APIM sees, so the gateway path needs no separate shape.
