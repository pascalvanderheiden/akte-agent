#!/usr/bin/env bash
set -euo pipefail

if [ -z "${AZURE_RESOURCE_GROUP:-}" ]; then
  echo "AZURE_RESOURCE_GROUP is required to configure agent-service health probes." >&2
  exit 1
fi

: "${AGENT_SERVICE_INGRESS_TARGET_PORT:=8000}"
: "${AGENT_SERVICE_READINESS_PROBE_PERIOD_SECONDS:=10}"
: "${AGENT_SERVICE_READINESS_PROBE_FAILURE_THRESHOLD:=10}"
: "${AGENT_SERVICE_READINESS_PROBE_TIMEOUT_SECONDS:=5}"
: "${AGENT_SERVICE_LIVENESS_PROBE_PERIOD_SECONDS:=30}"
: "${AGENT_SERVICE_LIVENESS_PROBE_FAILURE_THRESHOLD:=3}"
: "${AGENT_SERVICE_LIVENESS_PROBE_TIMEOUT_SECONDS:=5}"
: "${AGENT_SERVICE_LIVENESS_PROBE_INITIAL_DELAY_SECONDS:=30}"

command -v az >/dev/null 2>&1 || {
  echo "az not found; cannot configure agent-service health probes." >&2
  exit 1
}
command -v jq >/dev/null 2>&1 || {
  echo "jq not found; cannot configure agent-service health probes." >&2
  exit 1
}

SUBSCRIPTION_ARGS=()
if [ -n "${AZURE_SUBSCRIPTION_ID:-}" ]; then
  SUBSCRIPTION_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")
fi

APP_NAME="$(az containerapp list \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  "${SUBSCRIPTION_ARGS[@]}" \
  --query "[?tags.\"azd-service-name\"=='agent-service'].name | [0]" \
  --output tsv --only-show-errors)"
if [ -z "$APP_NAME" ] || [ "$APP_NAME" = "None" ]; then
  echo "Could not find the agent-service Container App in $AZURE_RESOURCE_GROUP." >&2
  exit 1
fi

APP_ID="$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  "${SUBSCRIPTION_ARGS[@]}" \
  --query id --output tsv --only-show-errors)"
TEMPLATE="$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  "${SUBSCRIPTION_ARGS[@]}" \
  --query properties.template --output json --only-show-errors)"
PROBES="$(jq -cn \
  --argjson readinessPeriod "$AGENT_SERVICE_READINESS_PROBE_PERIOD_SECONDS" \
  --argjson readinessThreshold "$AGENT_SERVICE_READINESS_PROBE_FAILURE_THRESHOLD" \
  --argjson readinessTimeout "$AGENT_SERVICE_READINESS_PROBE_TIMEOUT_SECONDS" \
  --argjson port "$AGENT_SERVICE_INGRESS_TARGET_PORT" \
  --argjson livenessPeriod "$AGENT_SERVICE_LIVENESS_PROBE_PERIOD_SECONDS" \
  --argjson livenessThreshold "$AGENT_SERVICE_LIVENESS_PROBE_FAILURE_THRESHOLD" \
  --argjson livenessTimeout "$AGENT_SERVICE_LIVENESS_PROBE_TIMEOUT_SECONDS" \
  --argjson livenessInitialDelay "$AGENT_SERVICE_LIVENESS_PROBE_INITIAL_DELAY_SECONDS" \
  '[
    {
      type: "Readiness",
      httpGet: {path: "/health/ready", port: $port, scheme: "HTTP"},
      periodSeconds: $readinessPeriod,
      failureThreshold: $readinessThreshold,
      timeoutSeconds: $readinessTimeout
    },
    {
      type: "Liveness",
      httpGet: {path: "/health", port: $port, scheme: "HTTP"},
      initialDelaySeconds: $livenessInitialDelay,
      periodSeconds: $livenessPeriod,
      failureThreshold: $livenessThreshold,
      timeoutSeconds: $livenessTimeout
    }
  ]')"

if jq -e --argjson probes "$PROBES" '.containers[0].probes == $probes' \
  <<<"$TEMPLATE" >/dev/null; then
  echo "agent-service health probes already match the deployment configuration."
  exit 0
fi

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT
jq -cn --argjson template "$TEMPLATE" --argjson probes "$PROBES" \
  '{properties: {template: ($template | del(.scale.cooldownPeriod, .scale.pollingInterval) | .containers[0].probes = $probes)}}' \
  >"$BODY_FILE"
az rest \
  --method patch \
  --uri "${APP_ID}?api-version=2024-03-01" \
  --body "@$BODY_FILE" \
  "${SUBSCRIPTION_ARGS[@]}" \
  --output none --only-show-errors
echo "Configured agent-service readiness and liveness probes."
