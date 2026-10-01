"""Interactive-request latency budget (ADR 0003).

Single numeric source for the `invoke_agent` latency budget, consumed by the
eval framework (per-scenario duration threshold) and mirrored by production
alerting. Keep these values synchronized with
`docs/adr/0003-interactive-request-latency-budget.md` and with any Azure Monitor
alert query that enforces the budget.
"""

# Targets for ordinary interactive requests, measured on the
# `gen_ai.client.operation.duration` histogram for `gen_ai.operation.name ==
# "invoke_agent"` over a rolling 15-minute window.
INTERACTIVE_P50_BUDGET_S = 2.0
INTERACTIVE_P95_BUDGET_S = 30.0

# Per-request ceiling for legitimately complex, high-context requests. A single
# request slower than this is out of budget regardless of how much work it did.
COMPLEX_REQUEST_CEILING_S = 120.0

# Aggregate time one invocation may spend waiting on Cosmos persistence, shared
# across every read and write it makes (preflight lookups, user message,
# assistant message). Model execution between calls does not consume it. Both
# the backend `/chat` route and the Foundry hosted agent enforce this one value
# through `app.services.cosmos_service.cosmos_persistence_budget`. It is a slice
# of the p50 budget so a denied or blackholed Cosmos cannot, on its own, push an
# ordinary request out of budget.
COSMOS_PERSISTENCE_BUDGET_S = 0.75

INTERACTIVE_P50_BUDGET_MS = int(INTERACTIVE_P50_BUDGET_S * 1000)
INTERACTIVE_P95_BUDGET_MS = int(INTERACTIVE_P95_BUDGET_S * 1000)
COMPLEX_REQUEST_CEILING_MS = int(COMPLEX_REQUEST_CEILING_S * 1000)
COSMOS_PERSISTENCE_BUDGET_MS = int(COSMOS_PERSISTENCE_BUDGET_S * 1000)

# Eval scenarios exercise full, tool-heavy hosted-agent turns, so the default
# per-scenario threshold is the complex-request ceiling. Scenarios that stand in
# for ordinary interactive requests set `max_duration_ms` to the tighter p95
# budget explicitly.
DEFAULT_SCENARIO_BUDGET_MS = COMPLEX_REQUEST_CEILING_MS


def scenario_budget_ms(max_duration_ms: int | None = None) -> int:
    """Resolve the duration threshold to enforce for one eval scenario."""
    if max_duration_ms is not None and max_duration_ms > 0:
        return max_duration_ms
    return DEFAULT_SCENARIO_BUDGET_MS
