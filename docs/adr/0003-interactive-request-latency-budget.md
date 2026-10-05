---
status: accepted
---

# Interactive-request latency budget and subagent routing decision rule

`invoke_agent` has no agreed answer to "how slow is too slow". The September 25,
2026 incident (11:13–11:33 UTC) pushed p95 from a 275 ms baseline to 250.8 s
with four requests running 105–249 s, and it was only found afterwards through
manual trace review. Every one of those requests succeeded; the time was spent
on genuine high-context model work (124,802–579,222 input tokens, 12–28 tool
calls per request, time-to-first-token under 3.2 s throughout).

This ADR fixes the numbers so "budget exceeded" has one meaning, and records the
rule for deciding whether a request should be delegated to a subagent. It does
**not** change ADR 0001: the three routing roles (`orchestrator`,
`deep-reasoning`, `fast`) and the own-routing-policy decision stand as-is.

## Budget

Measured on `gen_ai.client.operation.duration` where
`gen_ai.operation.name == "invoke_agent"`, over a rolling 15-minute window:

| Metric | Budget | Baseline (pre-incident) | Incident |
|---|---|---|---|
| p50 | **2 s** | 0.115 s | 3.2 s |
| p95 | **30 s** | 0.275 s | 250.8 s |
| single-request ceiling | **120 s** | — | 105–249 s (4 requests) |

Rationale for the headroom. The historical baseline is the reference point, not
the budget: alerting at 0.275 s would fire on every legitimately complex,
tool-heavy request. p50 is set at roughly 17× the baseline p50 so ordinary
interactive turns still have to stay snappy while a normal amount of tool work
is absorbed; the incident's 3.2 s p50 breaches it, which is the intended
signal. p95 at 30 s tolerates the high-context tail this agent genuinely has
(large akte documents, many tool hops) while the incident's 250.8 s misses it by
an order of magnitude. The 120 s single-request ceiling bounds the acknowledged
complex-request tail: a request above it is out of budget no matter how much
work it did, and is worth an operator's attention.

Conversation persistence gets a fixed slice of that budget: one invocation may
spend at most 750 ms in aggregate waiting on Cosmos, across every read and
write it makes (preflight lookups, the user message and the assistant message
share it — it is not per message). Persistence is non-fatal, so a denied or
blackholed Cosmos costs at most that slice and the response still succeeds.
The slice sits well inside the 2 s p50 budget, so Cosmos alone cannot push an
ordinary request out of budget. The backend `/chat` route and the Foundry
hosted agent enforce it through the same `cosmos_persistence_budget` context
manager; see `docs/cosmos-persistence.md`.

The budget is a policy decision, not a fitted statistic. Revisit it here — with
new before/after measurements — rather than quietly relaxing an alert threshold.

## Two consumers, one set of numbers

The values live in `src/backend/app/latency_budget.py`
(`INTERACTIVE_P50_BUDGET_S`, `INTERACTIVE_P95_BUDGET_S`,
`COMPLEX_REQUEST_CEILING_S`, `COSMOS_PERSISTENCE_BUDGET_S` and their `_MS`
equivalents). Keep that module, this ADR and any alert query synchronized;
tests import the values from there rather than restating them.

**Eval scenarios.** `EvalScenario.max_duration_ms` is the per-scenario duration
threshold. Eval scenarios exercise full tool-heavy turns, so an unset threshold
defaults to the 120 s complex-request ceiling; a scenario standing in for an
ordinary interactive request sets `max_duration_ms: 30000` explicitly. Each
`ScenarioResult` records the threshold it was judged against
(`latency_budget_ms`) and whether it broke it (`latency_budget_exceeded`), in
the run record, the streamed JSONL and `eval_report.json`, so a before/after
tuning comparison has the same numbers as production.

**Production alerting.** The same thresholds drive an Azure Monitor
scheduled-query rule, following the `hostedAgentCosmosPersistenceAlert` pattern
in `infra/modules/app-insights.bicep` (comment pointing at the source names the
query depends on). The query shape:

```kusto
customMetrics
| where name == "gen_ai.client.operation.duration"
| extend role = tostring(customDimensions["kratos.routing.role"])
| summarize p95 = percentile(value, 95), p50 = percentile(value, 50) by bin(timestamp, 15m)
| where p95 > 30 or p50 > 2
```

Slowness attributable to the Blob-fallback (#65) or Cosmos-persistence (#63)
signatures is excluded or flagged separately there, so this budget's alert stays
about model and tool work.

## Ingress and invocation correlation

Invocation spans record `kratos.pre_handler_delay_ms` only when the request
contains a recognized platform request-entry timestamp (`x-request-start` or
`x-envoy-request-start-time`). The value measures elapsed wall time from that
timestamp to handler entry, including ingress and queueing; it is not the
existing in-process `pre_handler_remainder_ms`. When no valid timestamp is
available, spans set `kratos.pre_handler_delay_available=false` and
`kratos.pre_handler_delay_source=platform_logs` rather than reporting a guessed
zero. In that case, query the platform ingress/system logs for request arrival
times, filtering by application, revision and replica over the relevant time
window, then compare those with the invocation span start times.
Foundry-hosted invocation timing must use its platform ingress logs when its
runtime does not forward a request-entry timestamp.

Invocation spans carry the trace ID, span ID, operation ID, revision name,
replica name, first-invocation-on-replica boolean, and validated synthetic
use-case ID. Prompt/message content, conversation identifiers, access tokens,
tool arguments/results and service endpoints are not span attributes.

## Subagent routing decision rule

Delegation buys context isolation and a better-suited role, and costs an extra
model round-trip on the critical path. Route a request to a subagent only when
the expected benefit clears that cost:

1. **Delegate** when the task is separable and self-contained (it can be stated
   in a prompt without replaying the conversation), *and* either it needs a
   different role's capability (`deep-reasoning` for genuinely hard analysis,
   `fast` for bulk mechanical work) or it keeps a large body of context — long
   tool output, bulk document text — out of the orchestrator's prompt.
2. **Keep it on the orchestrator** when the task is short, needs the full
   conversation context anyway, or would merely relay the orchestrator's own
   answer. A delegation hop that costs more than 25% of the p95 budget (7.5 s)
   without a measurable quality or token-volume gain is not worth taking.
3. **The total still has to fit.** Expected orchestrator time plus expected
   delegation time must stay inside the p95 budget for ordinary requests, and
   inside the 120 s ceiling for complex ones. Two sequential delegations on the
   same turn need a reason beyond "it might help".
4. **Measure, don't assume.** Any change to the default subagent set or to a
   persona's `extraSubagents` is justified by a before/after eval run on a
   representative high-context, tool-heavy scenario, recording p50/p95
   `duration_ms` against the threshold above *and* the existing pass/fail
   criteria — latency tuning must not regress answer quality. Delegation hops
   carry their own duration and token attribution in telemetry, so "which hop
   was slow" is answerable from the run.

This rule is a standard to check routing changes against. It introduces no new
routing mechanism.
