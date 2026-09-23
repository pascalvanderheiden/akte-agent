#!/usr/bin/env bash
#
# Postprovision setup for the opt-in Azure SRE Agent.
#
# Core-only in this release: it verifies that the agent provisioned by Bicep is
# actually ready and reports the shared result contract (see hooks/sre-lib.sh).
# Telemetry connectors and GitHub repository attachment are separate pieces of
# work; when their switches are on they are reported as `pending` with the
# reason, never as connected.
#
# Rerunnable on its own against an already-provisioned environment:
#   eval "$(azd env get-values | sed 's/^/export /')" && ./hooks/sre-setup.sh
#
# Noninteractive: it never prompts, opens a browser, or reads stdin.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/sre-lib.sh
. "${SCRIPT_DIR}/sre-lib.sh"

SRE_API_VERSION="2025-05-01-preview"
# Bounded: the agent becomes queryable shortly after ARM returns, but a wait
# that never ends is worse than a clear "gave up".
READY_ATTEMPTS="${SRE_READY_ATTEMPTS:-10}"
READY_DELAY="${SRE_READY_DELAY_SECONDS:-15}"

ENABLED="$(sre_bool DEPLOY_SRE_AGENT "${DEPLOY_SRE_AGENT:-}" false)" || exit 1

if [ "$ENABLED" != "true" ]; then
  # Not opted in: no Azure calls, no resources, nothing to report but the
  # contract itself.
  sre_result disabled disabled disabled
  exit 0
fi

CONNECT_TELEMETRY="$(sre_bool SRE_CONNECT_TELEMETRY "${SRE_CONNECT_TELEMETRY:-}" true)" || exit 1
CONNECT_GITHUB="$(sre_bool SRE_CONNECT_GITHUB "${SRE_CONNECT_GITHUB:-}" true)" || exit 1

# Optional integrations that were switched on but never got as far as being
# attempted, because core failed first, are pending rather than failed.
blocked_state() {
  [ "$1" = "true" ] && printf 'pending' || printf 'disabled'
}

AGENT_ID="${SRE_AGENT_ID:-}"
if [ -z "$AGENT_ID" ]; then
  echo "❌ DEPLOY_SRE_AGENT is true but this environment has no SRE_AGENT_ID." >&2
  echo "   That output comes from the infrastructure deployment, so either provisioning" >&2
  echo "   did not run with SRE enabled, or the outputs are stale. Run 'azd provision'" >&2
  echo "   (or 'azd env refresh' for an already-provisioned environment) and retry." >&2
  sre_result failed "$(blocked_state "$CONNECT_TELEMETRY")" "$(blocked_state "$CONNECT_GITHUB")"
  exit 1
fi

if ! command -v az >/dev/null 2>&1; then
  echo "❌ The Azure CLI (az) is required to verify the SRE Agent but was not found." >&2
  sre_result failed "$(blocked_state "$CONNECT_TELEMETRY")" "$(blocked_state "$CONNECT_GITHUB")"
  exit 1
fi

echo "🩺 Verifying the SRE Agent is ready ..."

attempt=1
STATE=""
while [ "$attempt" -le "$READY_ATTEMPTS" ]; do
  STATE="$(az resource show --ids "$AGENT_ID" --api-version "$SRE_API_VERSION" \
    --query properties.provisioningState -o tsv 2>/dev/null)"
  case "$STATE" in
    Succeeded)
      break
      ;;
    Failed | Canceled)
      echo "❌ The SRE Agent reports provisioningState '${STATE}'." >&2
      echo "   Inspect it with: az resource show --ids ${AGENT_ID} --api-version ${SRE_API_VERSION}" >&2
      sre_result failed "$(blocked_state "$CONNECT_TELEMETRY")" "$(blocked_state "$CONNECT_GITHUB")"
      exit 1
      ;;
    *)
      echo "   ... not ready yet (${STATE:-no response}); attempt ${attempt}/${READY_ATTEMPTS}"
      [ "$attempt" -lt "$READY_ATTEMPTS" ] && sleep "$READY_DELAY"
      ;;
  esac
  attempt=$((attempt + 1))
done

if [ "$STATE" != "Succeeded" ]; then
  echo "❌ The SRE Agent did not become ready after ${READY_ATTEMPTS} attempts (last state: ${STATE:-no response})." >&2
  echo "   The resource may still be provisioning. Re-run ./hooks/sre-setup.sh to check" >&2
  echo "   again, or inspect it with:" >&2
  echo "     az resource show --ids ${AGENT_ID} --api-version ${SRE_API_VERSION}" >&2
  sre_result failed "$(blocked_state "$CONNECT_TELEMETRY")" "$(blocked_state "$CONNECT_GITHUB")"
  exit 1
fi

echo "   ✅ SRE Agent '${SRE_AGENT_NAME:-$AGENT_ID}' is ready in '${SRE_AGENT_LOCATION:-unknown}'"
echo "      Read-only: accessLevel Low, actionMode Review — every action needs human review."
[ -n "${SRE_AGENT_PORTAL_URL:-}" ] && echo "      Portal: ${SRE_AGENT_PORTAL_URL}"

# Optional integrations. This release implements neither; saying so is the
# point of the contract, and is not the same as "unavailable" (which would
# claim the service refused) or "ready" (which would be a lie).
TELEMETRY_STATE="disabled"
if [ "$CONNECT_TELEMETRY" = "true" ]; then
  TELEMETRY_STATE="pending"
  echo "   ⏳ Telemetry connectors: not configured by this release."
  echo "      SRE writes its own logs to this environment's Application Insights, which"
  echo "      is not the same as permission to query your application's telemetry."
  echo "      Set SRE_CONNECT_TELEMETRY=false for an explicit core-only environment."
fi

GITHUB_STATE="disabled"
if [ "$CONNECT_GITHUB" = "true" ]; then
  GITHUB_STATE="pending"
  echo "   ⏳ GitHub repository attachment: not configured by this release."
  echo "      It needs an authorized data-plane session that a hook must not create on"
  echo "      your behalf. Set SRE_CONNECT_GITHUB=false for an explicit core-only"
  echo "      environment."
fi

echo "📋 SRE setup result:"
sre_result ready "$TELEMETRY_STATE" "$GITHUB_STATE"
exit 0
