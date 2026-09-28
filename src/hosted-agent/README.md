# Hosted agent

Foundry hosted-agent variant of the Kratos agent. Same skills and system
prompts as the Container App backend, running on Foundry-managed compute.

## Skills come from the local packaged `use-cases/`

This surface reads its system prompt and skills from the `use-cases/` tree
baked into its image, and runs local-only whenever the skills Blob Storage
account is unreachable — which, from Foundry-managed compute, it always is.
That is an accepted, first-class mode, not a fault to re-investigate.

Practical consequence: **Blob-only skill edits never reach the hosted agent.**
Ship skill changes by editing `use-cases/` and redeploying this image.

See [ADR 0002](../../docs/adr/0002-hosted-agent-local-only-skills.md) for the
topology behind this, and for the out-of-scope follow-up on giving hosted-agent
compute a private-network path to the skills account.
