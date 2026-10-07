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

## Shared-core startup and warmup readiness

The Python process entry point initializes shared services before serving with
the SDK's `run_async()`, on the same event loop. Initialization failure is
logged and does not prevent the host from starting; the next invocation retries
through the same single-flight initializer. Failed attempts cancel and drain
initializers and close partial services before a retry is allowed; services are
published only after successful initialization. Telemetry remains process-owned
and is not recreated on service retries. Per-use-case registries remain lazy.

The inspected SDK (`azure-ai-agentserver-invocations` 1.2.0,
`azure-ai-agentserver-core` 2.2.0) exposes an async server runner, but no
startup/warmup callback. Its `GET /readiness` handler returns static health,
not application-core readiness. This implementation therefore uses process
startup, not an assumed Foundry lifecycle or warmup signal. See the SDK's
[host implementation](https://github.com/Azure/azure-sdk-for-python/blob/azure-ai-agentserver-core_2.2.0/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/_base.py)
(`run_async` and `_readiness_endpoint`).

The `{"warmup": true}` response retains its existing fields and adds
`core_ready_before_ping`: whether shared-core initialization had completed
before the handler began. A ping that initializes or waits for the core reports
`false`, even though it returns ready afterward. User-invocation telemetry
records `kratos.readiness_source` as `early_init` or
`first_invocation_fallback`, and an invocation after completed early startup is
classified `warm`. Expected Blob local-only and Cosmos denial warnings keep
their existing severity and fallback behavior.

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
