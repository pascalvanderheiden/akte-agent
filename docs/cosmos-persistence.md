# Cosmos persistence: denial signatures and latency bound

Conversation persistence runs on the interactive response path, so a Cosmos
fault has to be *visible* and it has to be *bounded*. The 2026-09-28 rollout
(issue #120) was neither: Cosmos answered 403 because the traffic arrived off
its private endpoint, the alert did not fire, and the client library spent its
full retry budget on every attempt.

## Two kinds of 403

A 403 from Cosmos has two entirely different causes, with two different owners:

| Cause | What it means | Log signature |
|---|---|---|
| Network | The account firewall rejected where the request came from — public network access is disabled and the traffic did not use the private endpoint. Infrastructure/DNS fault. | `Cosmos persistence denied by network rules (firewall)` |
| RBAC | The network path was fine; the identity is missing the Cosmos DB Built-in Data Contributor role assignment (or it has not propagated). Identity fault. | `Cosmos persistence denied by RBAC role assignment` |
| Neither matched | A 403 whose detail matches neither set of markers. Read the logged `detail=` before assuming a cause. | `Cosmos persistence denied (unclassified 403)` |

`CosmosService._bounded` classifies the failure from the service's own error
detail and logs the signature together with `operation=`, `status=`,
`substatus=` and the first line of the detail
(`src/backend/app/services/cosmos_service.py`). Never merge these signatures:
chasing an RBAC hypothesis through a network fault is what cost the most time
in #120.

## Latency bound

Beyond a refusal, the account can stop answering altogether — a blackholed
route gets no refusal, only dropped packets. Bounds keep that off the
response path:

- **Startup probe** — `_COSMOS_PROBE_TIMEOUT_S` (10s). One database read at
  init; if it does not answer, the service falls back to local SQLite instead
  of keeping a client whose every call stalls.
- **Per operation** — `_COSMOS_OPERATION_TIMEOUT_S` (2s). Single-item reads
  and writes run under `asyncio.wait_for`, so a call cannot spend the SDK's
  roughly 40-second retry ladder waiting for a response.
- **Per `/chat` request** — `COSMOS_PERSISTENCE_BUDGET_S` (750ms), owned by
  `src/backend/app/latency_budget.py` (ADR 0003).
  All Cosmos calls made while setting up and running a chat share one aggregate
  wait-time budget, including calls in the detached agent task and the
  Foundry-hosted-agent invocation. Time spent waiting for model execution
  between Cosmos calls does not consume the budget. This caps cumulative Cosmos
  delay without making a long-running model turn exhaust its persistence budget
  before the assistant response is saved.

The conversation-lease check that guards every message and conversation write
is a Cosmos read too, so it runs under the same bound and classification: a
firewall 403 on it logs the network signature rather than escaping
unclassified. `tests/test_hosted_agent_persistence_budget.py` proves this at the
hosted-agent invocation entry point against a faked network denial and a
healthy fake.

If `/chat` continues after Cosmos lease acquisition fails, it marks the hosted
invocation as persistence-disabled. The backend and hosted agent then skip
message and session-mapping writes for that run instead of writing without a
cross-replica lease.

A timeout logs `Cosmos persistence operation timed out` and raises, preserving
the existing behaviour at each call site. A timeout says only that the
operation exceeded its time budget; it does not distinguish an unreachable
account from a slow or throttled response. Firewall denials are classified
separately from the explicit 403 service response above.

`list_messages` is deliberately not bounded this way: it is a history query
whose legitimate duration scales with the conversation, not a per-turn write.

## Alerting

`infra/modules/app-insights.bicep` defines the
`*-hosted-agent-cosmos-persistence` scheduled query rule (severity 2, 15-minute
window, fires above two distinct invocation IDs). It searches **both** `traces` and
`exceptions`: a warning logged with `exc_info` lands in `exceptions`, which is
why the original traces-only query missed the 403 rows in #120. Keep the
signature list in that query synchronized with the constants in
`cosmos_service.py` and the persistence warnings in `src/hosted-agent/main.py`;
`infra/tests/test_foundry_dependencies.py` checks that they match.

The query deduplicates by `operation_Id` before counting. A failed invocation
can emit a classified denial plus user- and assistant-message warnings, and
each warning can appear in both telemetry tables; counting raw rows could
therefore alert on one invocation. `GreaterThan 2` means three distinct
invocations in the 15-minute window, avoiding an alert for a single transient
while retaining the existing 5-minute evaluation frequency. The alert covers
all persistence failure signatures, so inspect the classified signature to
distinguish network denial from RBAC, unclassified 403, or timeout.

## Recognition and verification runbook

### Recognize a network denial

Use the exact signature rather than treating every 403 as a firewall fault.
Search both telemetry tables and group by invocation so duplicate rows do not
inflate the count:

```kusto
union
  (traces | project timestamp, operation_Id, signal=message),
  (exceptions | project timestamp, operation_Id,
     signal=strcat(outerMessage, " ", innermostMessage, " ", tostring(customDimensions)))
| where timestamp between (datetime(<start-UTC>) .. datetime(<end-UTC>))
| where signal contains "Cosmos persistence denied by network rules (firewall)"
| summarize firstSeen=min(timestamp), lastSeen=max(timestamp) by operation_Id
| order by firstSeen desc
```

Compare with `Cosmos persistence denied by RBAC role assignment` and
`Cosmos persistence denied (unclassified 403)` to separate network, identity,
and unknown 403s. An empty result is evidence of no classified network denial
only when the interval contains representative traffic and telemetry ingestion
is confirmed.

### Verify after deployment

1. The existing deploy workflow is the only deployment path used for this
   verification. Its automatic entry point deploys to `turbo-akte-agent`; do
   not provision another environment or change Cosmos network access.
2. Compare the deployed hosted-agent `source_revision` (from its warmup
   response) with the merged `main` revision:

   ```bash
   git fetch origin main
   DEPLOYED_SOURCE_REVISION="<source_revision from the warmup response>"
   test "$DEPLOYED_SOURCE_REVISION" = "$(git rev-parse origin/main)"
   ```

3. The post-deploy e2e `03-chat` check asserts that the Container App backend
   persists both messages and observes no firewall-denial telemetry. Its
   healthy private-path round trip must fail if either message is missing.
   API-only mode remains available with `SKIP_BROWSER=1`. This check exercises
   the backend path; it does not prove that Foundry-hosted-agent compute can
   reach Cosmos. Count the smoke as Cosmos evidence only after confirming the
   backend startup log contains `Cosmos DB initialized` and does not contain
   the SQLite-fallback warning, and confirming from the running backend
   container that the hostname in `COSMOS_DB_ENDPOINT` resolves to the Cosmos
   private endpoint. Without both checks, report the result as an application
   smoke only; startup can fall back to SQLite after a failed reachability probe.
4. Live hosted-agent Cosmos persistence remains unverified. Hosted-agent unit
   coverage exercises both outcomes at the invocation seam:
   `test_network_denied_invocation_succeeds_within_one_persistence_budget`
   proves a simulated denial stays bounded and non-fatal, while
   `test_healthy_cosmos_persists_both_messages_within_the_budget` verifies both
   writes against a fake container. These are not live persistence evidence.
   Foundry-hosted-agent networking to the private endpoint is a known platform
   limitation: report an unreachable endpoint as unverified/blocked, not as a
   successful persistence check. If Cosmos is reachable from hosted-agent
   compute, its verification must fail when either the user or assistant write
   is missing.
5. Confirm the App Insights scheduled-query alert is enabled and its query is
   available. The compiled-template test validates the 15-minute window,
   three-distinct-invocation threshold, both telemetry tables, and the
   network-denial signature. Keep the Cosmos account's public network access
   disabled.

### Acceptance evidence for #138

| #138 acceptance criterion | Evidence |
|---|---|
| Deployed runtime includes the current persistence budget and network classification | Hosted-agent `source_revision` warmup check against merged `main`; shared-budget and classification tests |
| A controlled network denial stays within the shared budget | `uv run pytest tests/test_hosted_agent_persistence_budget.py` |
| Classified denials are queryable and repeated failures alert | Exact-signature KQL above; `ApplicationInsightsAlertTests` checks the enabled rule's query, window, and threshold |
| Normal backend persistence works when the private path is available | Post-deploy `03-chat` verifies user and assistant messages only when backend Cosmos initialization and private-endpoint DNS resolution are confirmed |
| Hosted-agent persistence works when its private path is available | Live hosted-agent persistence remains unverified; the healthy fake verifies code-path writes only, and a reachable live path must fail if either write is missing |

### Separate platform limitation

Foundry-hosted-agent compute still has no supported private-network path to the
Cosmos private endpoint. Track that as a separate platform-capability follow-up;
this verification does not change the local-only decision in
[ADR 0002](adr/0002-hosted-agent-local-only-skills.md) or the shared persistence
budget in [ADR 0003](adr/0003-interactive-request-latency-budget.md). An
unreachable hosted-agent path may fall back to local SQLite or log a classified
network denial, so it must never be recorded as a successful Cosmos round trip.
