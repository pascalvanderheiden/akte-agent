---
status: proposed
---

# Own routing policy over SDK Auto

The agent will own model selection through three stable Routing Roles
(`orchestrator`, `deep-reasoning`, and `fast`) rather than delegate routing to
an SDK or platform feature. In Azure mode, the preferred Copilot SDK wiring is
one named provider per Foundry deployment, with each provider using the
deployment-path base URL. Models use provider-qualified IDs. This keeps direct
Foundry and APIM gateway traffic structurally identical and lets every provider
share the same bearer-token callback.

The fallback, if live SDK verification shows provider-qualified models or the
plural `providers` surface do not work, is one Azure provider using the
`/openai/v1` base URL plus one model catalogue entry per deployment. Each model
entry sets `wire_model` to its Foundry deployment name.

SDK `auto` and `auto_tier` are rejected because they are CAPI-only and cannot
express the Azure BYOK deployments used here. Foundry Model Router is rejected
because it moves the routing policy outside the application, cannot preserve
the three explicit role contracts, and does not provide the same direct/APIM
provider catalogue.

## Pending verification

Live verification is still **PENDING**. Before this ADR can become accepted, a
cloud-like run without `GITHUB_TOKEN` must prove that a provider-qualified main
session can delegate to a custom agent on another named provider, through both
direct Foundry and APIM. The exact GPT-6 model names and versions,
`GlobalStandard` availability, and quota for `gpt-6-luna`, `gpt-6-sol`, and
`gpt-6-astra` also remain **PENDING** in every region used by the production and
experimentation environments. The Bicep defaults are provisional until that
verification is recorded; operators can override every name, model, version,
and capacity parameter.
