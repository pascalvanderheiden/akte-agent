# `invoke_agent` latency-outlier runbook

Use this runbook when the informational 1 s rolling-p95 signal fires or an
individual invocation looks slow. The signal is for diagnosis, not a budget
breach: see [ADR 0003](../adr/0003-interactive-request-latency-budget.md).

## First query: one invocation's gap

In Application Insights Logs, set `traceId` to the invocation's
`operation_Id` and `invokeSpanId` to its `id`. Use a recent lookback that
includes the invocation. The query reports the invocation duration, direct
phase-child durations, the wall-clock time covered by those children, and the
remaining gap in milliseconds:

```kusto
let traceId = "<operation_Id>";
let invokeSpanId = "<invoke_agent span id>";
let spans =
    union dependencies, requests
    | where timestamp > ago(7d)
    | where operation_Id == traceId
    | extend
        parentId = operation_ParentId,
        opName = tostring(customDimensions["gen_ai.operation.name"]),
        phase = tostring(customDimensions["kratos.phase"]),
        durationMs = todouble(duration / 1ms)
    | project timestamp, id, parentId, name, opName, phase, duration, durationMs, customDimensions;
let invocation =
    spans
    | where id == invokeSpanId
    | project invokeId = id, invokeMs = durationMs,
        preHandlerMs = todouble(customDimensions["kratos.request_stage.pre_handler_remainder_ms"]);
let children =
    spans
    | where parentId == invokeSpanId
    | extend cause = case(
        phase == "hosted_agent_proxy" or name == "hosted_agent.invoke", "proxy",
        opName == "chat" or name startswith "gen_ai.chat", "model",
        opName == "execute_tool" or name startswith "execute_tool", "tool",
        "other")
    | project timestamp, endTime = timestamp + duration, cause, durationMs;
let phaseTotals =
    children
    | summarize
        proxyMs = sumif(durationMs, cause == "proxy"),
        modelMs = sumif(durationMs, cause == "model"),
        toolMs = sumif(durationMs, cause == "tool"),
        otherChildMs = sumif(durationMs, cause == "other");
let boundaries =
    union
        (children | project t = timestamp, delta = 1),
        (children | project t = endTime, delta = -1)
    | summarize delta = sum(delta) by t
    | sort by t asc
    | serialize
    | extend active = row_cumsum(delta), nextTime = next(t)
    | summarize childWallMs = sumif(datetime_diff("millisecond", nextTime, t), active > 0);
invocation
| extend joinKey = 1
| join kind=leftouter (phaseTotals | extend joinKey = 1) on joinKey
| join kind=leftouter (boundaries | extend joinKey = 1) on joinKey
| extend
    proxyMs = coalesce(proxyMs, 0.0),
    modelMs = coalesce(modelMs, 0.0),
    toolMs = coalesce(toolMs, 0.0),
    otherChildMs = coalesce(otherChildMs, 0.0),
    childWallMs = coalesce(childWallMs, 0.0),
    preHandlerMs = coalesce(preHandlerMs, 0.0)
| extend unattributedMs = max_of(0.0, invokeMs - childWallMs)
| project invokeMs, preHandlerMs, proxyMs, modelMs, toolMs, otherChildMs, childWallMs, unattributedMs
```

Use the invocation's `kratos.invocation_id` to confirm the rows belong to the
same run before interpreting them. The query deliberately sums only direct
children so nested spans are not double-counted. `proxyMs`, `modelMs`,
`toolMs`, and `otherChildMs` are gross span durations and can overlap; use
`childWallMs` for the gap arithmetic, not their sum. A missing child is not
proof that the phase did not run: check trace completeness and sampling first.
The pre-handler remainder is reported separately because it is measured from
handler entry to span attachment; include it in the end-to-end total only if
the selected invocation span's start boundary excludes that interval. If
request-entry time is unavailable in telemetry, use the Container Apps/platform
request logs to measure pre-handler delay; do not infer it from an absent span.

| Pattern | Interpretation | Action and owner |
|---|---|---|
| Large pre-handler remainder, or request-entry-to-handler delay | Ingress, queueing, or scale-from-zero before agent work | Compare platform request/replica events and cold-start state. **Container Apps/platform on-call** checks ingress, queueing, and replica startup; escalate a repeated platform delay to the platform owner. |
| Proxy child dominates | Time in the backend-to-hosted-agent proxy or its transport | Check proxy timing, trace-context continuity, and hosted-agent availability. **Agent-service on-call** investigates proxy/transport; involve the hosted-agent owner if the remote service is slow. |
| Model spans dominate | Model/provider latency | Compare model spans and provider status for the same time range. **Model/provider owner** investigates throttling, service health, or deployment-specific latency. |
| Tool spans dominate | Tool or skill execution latency | Identify the slow tool by its safe tool name and inspect its duration/error metadata. **Tool/skill owner** investigates that dependency; involve the MCP owner for MCP-backed tools. |
| Remaining gap is large, or spans are absent/unmatched | Uninstrumented work, mismatched correlation, or incomplete telemetry; cause is unknown | First verify `operation_Id`, invocation ID, parent IDs, sampling, and exporter ingestion. **Service on-call/observability owner** records the unresolved gap and adds instrumentation only after confirming telemetry is complete. |

## Recurrence and closure

Treat the pattern as recurring if there are **3 or more invocations over 1 s in
any rolling 24 hours**, or **2 or more such outliers on the same replica or
revision in that period**. Capture the operation ID, invocation ID, UTC time,
revision, replica, cold-start flag, phase timings, and the runbook conclusion;
then open/continue an incident with the owner in the table. The 1 s condition
is an investigation trigger, not a paging threshold. A request over the ADR 0003
120 s single-request ceiling is independently out of budget and should follow
the normal latency incident path even if it is the only occurrence.

If the outlier cannot be reproduced and the traces reconcile on the next
representative invocation, record **“not reproducible with the new
instrumentation”**, preserve the sanitized query result and IDs, and close the
investigation as a one-off. Do not claim a root cause, change the latency
budget, or tune replicas/models/tools without supporting evidence; continue
monitoring the recurrence criteria.

## Correlation and privacy

Use only these fields to join telemetry and communicate an investigation:

- `operation_Id` (trace ID), `id` (span ID), and `operation_ParentId` (parent
  span ID) establish trace and span relationships.
- `kratos.invocation_id` identifies the run across invocation spans.
- Revision name, replica name, and the cold-start boolean distinguish runtime
  instances and first-use latency.
- `kratos.use_case` is allowed only when it is a synthetic persona/use-case ID.
- `kratos.request_stage.pre_handler_remainder_ms` and the phase span durations
  provide timing, not content.

**Never add, query for, or copy into a ticket/report:** prompt text, system
instructions, message or model content, tool arguments/results, user or
customer identifiers, access tokens/credentials, or service endpoints/URLs.
Keep GenAI content recording disabled. Existing traces may contain
conversation-linked IDs or content-bearing attributes; do not share those
values, and redact them from exports/screenshots. Prefer the invocation ID and
trace/span IDs above over conversation IDs when correlating.
