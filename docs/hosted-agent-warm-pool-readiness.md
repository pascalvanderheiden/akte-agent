# Hosted-agent warm-pool readiness

The backend maintains a pool of pre-warmed hosted-agent sandboxes so new
conversations can start quickly. During application startup, `/health/ready`
reports the state of that initial fill:

| State | `/health/ready` | Meaning |
|---|---|---|
| `warming` | 503 | The initial warm-pool fill is still in progress. Container Apps does not route ingress traffic to this replica yet. |
| `ready` | 200 | The configured warm-pool target was filled within the startup timeout. |
| `degraded` | 200 | The fill timed out, failed, or completed with fewer sandboxes than requested. The service remains able to start conversations using isolated sandboxes, although a conversation may take longer to start. |

Degraded is ready by design: losing some pre-warmed capacity affects startup
latency, not whether the service can handle requests. Treat `degraded` as a
reduced-capacity signal rather than an outage. Alerting on a sustained degraded
state remains an open question.

The `WARM_POOL_WARMUP_TIMEOUT_S` setting is an optional override from 1 to 90
seconds. Without an override, the timeout is approximately 16 seconds per
configured warm-pool sandbox plus 5 seconds, capped at 90 seconds; the default
pool size of two therefore gives a 37-second timeout. See
`Settings.effective_warm_pool_warmup_timeout_s` in
`src/backend/app/config.py`.

The `agent-service` Container App readiness probe checks `/health/ready` every
10 seconds and allows 10 failures: its default failure window is 100 seconds,
longer than the backend's 90-second maximum warm-up timeout. The probe timing
values are Bicep parameters. Liveness is independent: it checks `/health`,
which remains healthy while the warm pool is warming, so warm-up does not
restart a functioning container.

The bootstrap image does not implement these health endpoints, so the initial
bootstrap revision is created without HTTP probes. The predeploy hook applies
the configured probes before `azd deploy` replaces it with the application
image. The image update therefore creates the first application revision with
readiness already configured; in single-revision mode, ingress continues to
serve the previous revision until the new revision reports `ready` or
`degraded`. Probe settings are root Bicep parameters and can be overridden
through their `AGENT_SERVICE_*` azd environment variables. Later provisions
keep the probes on the deployed image.

Post-deploy smoke tests call the Container App through ingress. They do not
require readiness to be true immediately after deployment; ingress exposes a
replica after it becomes `ready` or `degraded`. The e2e smoke checks the
service's `/health` endpoint and application behavior rather than polling
`/health/ready`.
