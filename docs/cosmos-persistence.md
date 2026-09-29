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
- **Per `/chat` request** — `_COSMOS_REQUEST_PERSISTENCE_BUDGET_S` (750ms).
  All Cosmos calls made while setting up and running a chat share one aggregate
  wait-time budget, including calls in the detached agent task and the
  Foundry-hosted-agent invocation. Time spent waiting for model execution
  between Cosmos calls does not consume the budget. This caps cumulative Cosmos
  delay without making a long-running model turn exhaust its persistence budget
  before the assistant response is saved.

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
window, fires above two occurrences). It searches **both** `traces` and
`exceptions`: a warning logged with `exc_info` lands in `exceptions`, which is
why the original traces-only query missed the 403 rows in #120. Keep the
signature list in that query synchronized with the constants in
`cosmos_service.py` and the persistence warnings in `src/hosted-agent/main.py`;
`infra/tests/test_foundry_dependencies.py` checks that they match.
