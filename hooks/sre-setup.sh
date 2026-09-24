#!/usr/bin/env bash
# Verify the deployed core. Rerun without redeploying:
#   ./hooks/sre-setup.sh --environment <azd-environment>
set +x
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/sre-lib.sh
. "${SCRIPT_DIR}/sre-lib.sh"

CORE_STATE=failed
TELEMETRY_STATE=disabled
GITHUB_STATE=disabled
APP_INSIGHTS_STATE=""
LOG_ANALYTICS_STATE=""
SETUP_FAILED=0
ERROR_FILE=""
TELEMETRY_TEMPLATE_FILE=""
finish() {
  [ -z "$ERROR_FILE" ] || rm -f "$ERROR_FILE"
  [ -z "$TELEMETRY_TEMPLATE_FILE" ] || rm -f "$TELEMETRY_TEMPLATE_FILE"
  sre_result "$CORE_STATE" "$TELEMETRY_STATE" "$GITHUB_STATE" \
    "${APP_INSIGHTS_STATE:-$TELEMETRY_STATE}" "${LOG_ANALYTICS_STATE:-$TELEMETRY_STATE}"
}
trap finish EXIT

sre_load_environment "$@" || exit 1
ENABLED="$(sre_bool DEPLOY_SRE_AGENT "${DEPLOY_SRE_AGENT:-}" false)" || exit 1
if [ "$ENABLED" != "true" ]; then
  CORE_STATE=disabled
  exit 0
fi
CONNECT_TELEMETRY="$(sre_bool SRE_CONNECT_TELEMETRY "${SRE_CONNECT_TELEMETRY:-}" true)" || exit 1
[ "$CONNECT_TELEMETRY" != "true" ] || TELEMETRY_STATE=pending
CONNECT_GITHUB="$(sre_bool SRE_CONNECT_GITHUB "${SRE_CONNECT_GITHUB:-}" true)" || exit 1
[ "$CONNECT_GITHUB" != "true" ] || GITHUB_STATE=pending

READY_ATTEMPTS="${SRE_READY_ATTEMPTS:-10}"
READY_DELAY="${SRE_READY_DELAY_SECONDS:-15}"
[[ "$READY_ATTEMPTS" =~ ^([1-9]|[1-5][0-9]|60)$ ]] &&
  [[ "$READY_DELAY" =~ ^([0-9]|[1-9][0-9]|[12][0-9][0-9]|300)$ ]] ||
  { sre_error "SRE_READY_ATTEMPTS must be an integer 1-60; SRE_READY_DELAY_SECONDS must be an integer 0-300."; exit 1; }

AGENT_ID="${SRE_AGENT_ID:-}"
[ -n "$AGENT_ID" ] ||
  { sre_error "No SRE_AGENT_ID output. Run azd provision with SRE enabled, or azd env refresh for an existing deployment, and retry."; exit 1; }
sre_check_context || exit 1
EXPECTED_RG="/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/rg-${AZURE_ENV_NAME}"
[[ "${SRE_AGENT_NAME:-}" =~ ^[a-z][a-z0-9-]{1,61}[a-z0-9]$ ]] ||
  { sre_error "Invalid SRE_AGENT_NAME output; refresh the selected environment."; exit 1; }
EXPECTED_AGENT="${EXPECTED_RG}/providers/Microsoft.App/agents/${SRE_AGENT_NAME:-}"
jq -en --arg actual "$AGENT_ID" --arg expected "$EXPECTED_AGENT" '
  ($actual | ascii_downcase) == ($expected | ascii_downcase)
' >/dev/null ||
  { sre_error "SRE_AGENT_ID/name does not belong to this azd subscription/environment. Refresh outputs; no resource was queried."; exit 1; }
[ "${SRE_AGENT_ENABLED:-}" = "true" ] ||
  { sre_error "SRE_AGENT_ENABLED is not true in deployment outputs. Refresh outputs or provision with SRE enabled."; exit 1; }
for key in SRE_AGENT_LOCATION SRE_AGENT_PRINCIPAL_ID SRE_AGENT_IDENTITY_ID SRE_APP_INSIGHTS_APP_ID; do
  [ -n "${!key:-}" ] ||
    { sre_error "Missing deployment output $key. Run azd env refresh and retry."; exit 1; }
done

umask 077
ERROR_FILE="$(mktemp)" || { sre_error "Cannot create private diagnostic scratch file."; exit 1; }
STATE=""
for ((attempt=1; attempt<=READY_ATTEMPTS; attempt++)); do
  # Project only nonsecret verification fields; never log raw ARM responses.
  RESOURCE="$(az resource show --ids "$AGENT_ID" --subscription "$AZURE_SUBSCRIPTION_ID" \
    --api-version "$SRE_API_VERSION" --only-show-errors --output json \
    --query '{id:id,location:location,tags:tags,identity:identity,state:properties.provisioningState,action:properties.actionConfiguration,graph:properties.knowledgeGraphConfiguration,appId:properties.logConfiguration.applicationInsightsConfiguration.appId}' \
    2>"$ERROR_FILE")"
  STATUS=$?
  if [ "$STATUS" -ne 0 ]; then
    # Retry only known propagation/transient failures, never a permanent denial
    # or an arbitrary CLI error. Raw diagnostics can contain bearer tokens.
    if grep -Eq '^[[:space:]]*(ERROR: )?\((ResourceNotFound|TooManyRequests|ServiceUnavailable|GatewayTimeout|InternalServerError)\)' "$ERROR_FILE"; then
      STATE=TransientReadFailure
    else
      sre_error "SRE resource read failed (CLI exit $STATUS). Check resource-read permission, selected context, and network access; raw diagnostics suppressed. No retry on unknown/permanent errors."
      exit 1
    fi
  else
    jq -e 'type == "object" and (.state | type == "string" and length > 0)' <<<"$RESOURCE" >/dev/null 2>&1 ||
      { sre_error "Malformed SRE readiness response; no readiness claim was made."; exit 1; }
    STATE="$(jq -r '.state' <<<"$RESOURCE")"
    case "$STATE" in
      Succeeded) break ;;
      Accepted | Creating | Updating | InProgress | Provisioning) ;;
      Failed | Canceled | Cancelled)
        sre_error "SRE reports a terminal provisioning failure. Inspect the agent in Azure and retry after correcting it."
        exit 1 ;;
      *) sre_error "Unknown SRE provisioning state. Inspect the resource; no readiness claim was made."; exit 1 ;;
    esac
  fi
  echo "SRE not ready; attempt ${attempt}/${READY_ATTEMPTS}."
  if [ "$attempt" -lt "$READY_ATTEMPTS" ]; then
    sleep "$READY_DELAY" || { sre_error "Readiness wait failed."; exit 1; }
  fi
done
[ "$STATE" = "Succeeded" ] ||
  { sre_error "SRE did not become ready after $READY_ATTEMPTS attempts. Inspect the agent, then rerun ./hooks/sre-setup.sh --environment <azd-environment>."; exit 1; }

jq -e --arg id "$AGENT_ID" --arg env "$AZURE_ENV_NAME" \
  --arg region "$(sre_region_key "$SRE_AGENT_LOCATION")" --arg rg "$EXPECTED_RG" \
  --arg uami "$SRE_AGENT_IDENTITY_ID" --arg principal "$SRE_AGENT_PRINCIPAL_ID" \
  --arg app "$SRE_APP_INSIGHTS_APP_ID" '
  (.id | ascii_downcase) == ($id | ascii_downcase) and
  (.location | ascii_downcase | gsub(" "; "")) == $region and
  .tags["azd-env-name"] == $env and
  .action.accessLevel == "Low" and .action.mode == "Review" and
  (.action.identity | ascii_downcase) == ($uami | ascii_downcase) and
  (.graph.identity | ascii_downcase) == ($uami | ascii_downcase) and
  (.graph.managedResources | map(ascii_downcase)) == [($rg | ascii_downcase)] and
  (.identity.type | split(",") | map(gsub(" "; "")) | sort) == ["SystemAssigned", "UserAssigned"] and
  .identity.principalId == $principal and
  (.identity.userAssignedIdentities | keys | map(ascii_downcase)) == [($uami | ascii_downcase)] and
  .appId == $app
' <<<"$RESOURCE" >/dev/null 2>&1 ||
  { sre_error "SRE core verification failed: environment, region, Low/Review, discovery scope, identities, or shared AppId differs from deployment outputs. Inspect configuration drift; no automatic repair was attempted."; exit 1; }

CORE_STATE=ready
echo "SRE core ready: verified environment, read-only/review settings, identities, scope and shared AppId."
if [ "$CONNECT_TELEMETRY" = "true" ]; then
  # shellcheck source=hooks/sre-telemetry.sh
  if . "${SCRIPT_DIR}/sre-telemetry.sh"; then
    sre_setup_telemetry || SETUP_FAILED=1
  else
    sre_error "Cannot load telemetry setup."
    TELEMETRY_STATE=failed
    SETUP_FAILED=1
  fi
fi
if [ "$CONNECT_GITHUB" = "true" ]; then
  echo "GitHub pending: repository attachment is not implemented in this core release (#37)."
fi
if [ "$TELEMETRY_STATE" = pending ] || [ "$GITHUB_STATE" = pending ]; then
  echo "Degraded setup: set SRE_CONNECT_TELEMETRY=false and SRE_CONNECT_GITHUB=false for explicit core-only mode."
fi
exit "$SETUP_FAILED"
