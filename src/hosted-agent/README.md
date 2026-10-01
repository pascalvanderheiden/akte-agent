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

## Check the deployed source revision

Each deploy records the current Git commit in `KRATOS_SOURCE_REVISION`. The
hosted agent includes it as `source_revision` in its warmup response (the
Invocations request body is `{"warmup": true}`). Compare that value with the
latest `main` commit after fetching:

```bash
git fetch origin main
DEPLOYED_SOURCE_REVISION="<source_revision from the warmup response>"
test "$DEPLOYED_SOURCE_REVISION" = "$(git rev-parse origin/main)"
```

A mismatch means the deployed image was built from a different checkout (or
from a checkout without Git metadata, which reports `unknown`).

See [ADR 0002](../../docs/adr/0002-hosted-agent-local-only-skills.md) for the
topology behind this, and for the out-of-scope follow-up on giving hosted-agent
compute a private-network path to the skills account.
