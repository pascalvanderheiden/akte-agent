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
route gets no refusal, only dropped packets. Two bounds keep that off the
response path:

- **Startup probe** — `_COSMOS_PROBE_TIMEOUT_S` (10s). One database read at
  init; if it does not answer, the service falls back to local SQLite instead
  of keeping a client whose every call stalls.
- **Per operation** — `_COSMOS_OPERATION_TIMEOUT_S` (2s). Every single-item
  read and write on the response path runs under `asyncio.wait_for`, because
  the account can become unreachable *after* the probe passed. Without it the
  SDK's retry ladder stalls each call for roughly 40 seconds. The bound is four
  times the 500 ms slow-operation warning threshold, so a healthy call never
  trips it.

A timeout logs `Cosmos persistence unreachable (timed out)` and raises, which
preserves the existing behaviour at each call site: the hosted agent catches it
and answers without persisting (fail-open), while callers that treat
persistence as required still fail — just in 2 seconds rather than 40.

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
