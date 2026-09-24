#!/usr/bin/env bash
# Sourced after core verification by sre-setup.sh; no separate auth or core writes.

sre_telemetry_cli_state() {
  # Accept structured ARM leaf codes or anchored CLI codes, never message text.
  # A mixed template defect + policy denial must still fail.
  local codes
  codes="$(jq -Rrs '
    def leaves:
      if type != "object" or (.code | type) != "string" or
        (has("details") and (.details | type) != "array") then error("invalid ARM error")
      elif (.details | type) == "array" and (.details | length) > 0
      then .details[] | leaves
      else .code end;
    . as $text |
    (try (sub("^ERROR: "; "") | fromjson) catch null) as $json |
    if $json != null then [($json.error // $json) | leaves]
    else [$text | scan("(?:^|\\n)(?:ERROR: )?(?:\\(([A-Za-z]+)\\)|Code: ([A-Za-z]+))")
      | .[] | select(. != null and . != "")] end |
    if length == 0 then "failed"
    elif any(.[]; IN("AuthorizationFailed", "LinkedAuthorizationFailed", "Forbidden",
      "RequestDisallowedByPolicy", "SubscriptionNotRegistered",
      "ResourceNotFound", "PrincipalNotFound", "TooManyRequests", "ServiceUnavailable",
      "GatewayTimeout", "InternalServerError") | not) then "failed"
    elif any(.[]; IN("RequestDisallowedByPolicy", "SubscriptionNotRegistered"))
      then "unavailable"
    elif any(.[]; IN("AuthorizationFailed", "LinkedAuthorizationFailed", "Forbidden")) then "pending"
    else "retry" end
  ' "$ERROR_FILE" 2>/dev/null)" ||
    { sre_error "Telemetry: malformed error response; raw diagnostics suppressed."; return 1; }
  printf '%s' "$codes"
}

sre_telemetry_call() {
  local phase="$1" attempt response status state
  shift
  for ((attempt=1; attempt<=READY_ATTEMPTS; attempt++)); do
    response="$("$@" 2>"$ERROR_FILE")"
    status=$?
    if [ "$status" -eq 0 ]; then
      jq -e 'type == "object"' <<<"$response" >/dev/null 2>&1 ||
        { sre_error "Telemetry $TELEMETRY_SOURCE: malformed CLI JSON; raw response suppressed."; return 1; }
      TELEMETRY_RESPONSE="$response"
      if [ "$phase" != connector ]; then
        return 0
      fi
      # provisioningState is optional in the pinned connector contract; an
      # absent state permits configuration read-back, not a query-ready claim.
      state="$(jq -er '
        .properties | if type != "object" then error("invalid properties")
        elif has("provisioningState") then .provisioningState | select(type == "string")
        else "NotReported" end
      ' <<<"$response" 2>/dev/null)" ||
        { sre_error "Telemetry $TELEMETRY_SOURCE: malformed connector properties/state."; return 1; }
      case "$state" in
        NotReported | Succeeded) return 0 ;;
        Accepted | Creating | Updating | InProgress | Provisioning) state=retry ;;
        *) sre_error "Telemetry $TELEMETRY_SOURCE: terminal/unknown connector provisioning state."; return 1 ;;
      esac
    else
      state="$(sre_telemetry_cli_state)" || return 1
    fi
    case "$state" in
      retry)
        if [ "$attempt" -lt "$READY_ATTEMPTS" ]; then
          sleep "$READY_DELAY" || { sre_error "Telemetry retry wait failed."; return 1; }
          continue
        fi
        TELEMETRY_SOURCE_STATE=pending
        echo "Telemetry $TELEMETRY_SOURCE pending: readiness/transient/RBAC propagation exhausted $READY_ATTEMPTS attempts."
        return 3 ;;
      pending | unavailable)
        TELEMETRY_SOURCE_STATE="$state"
        echo "Telemetry $TELEMETRY_SOURCE $state: confirmed authorization or capability/policy restriction; no denial retry."
        return 2 ;;
      *)
        sre_error "Telemetry $TELEMETRY_SOURCE failed (CLI exit $status): unexpected/template error; raw diagnostics suppressed. Inspect the optional deployment privately."
        return 1 ;;
    esac
  done
}

sre_telemetry_source() {
  local resource_id resource_type output_key api deployment connector_id
  TELEMETRY_SOURCE="$1"
  TELEMETRY_SOURCE_STATE=failed
  case "$TELEMETRY_SOURCE" in
    app-insights)
      resource_id="${SRE_APP_INSIGHTS_ID:-}"
      resource_type="Microsoft.Insights/components"
      output_key=SRE_APP_INSIGHTS_ID
      api="2020-02-02" ;;
    log-analytics)
      resource_id="${SRE_LOG_ANALYTICS_ID:-}"
      resource_type="Microsoft.OperationalInsights/workspaces"
      output_key=SRE_LOG_ANALYTICS_ID
      api="2023-09-01" ;;
  esac

  # Refuse stale outputs before granting any identity access to a target.
  jq -e --arg key "$output_key" --arg id "$resource_id" '
    (.[$key] | type == "string" and length > 0) and
    (.[$key] | ascii_downcase) == ($id | ascii_downcase)
  ' <<<"$TELEMETRY_VALUES" >/dev/null 2>&1 ||
    { sre_error "Telemetry $TELEMETRY_SOURCE: injected output differs from selected environment; refresh outputs."; return 1; }
  jq -en --arg id "$resource_id" --arg prefix "$EXPECTED_RG/providers/$resource_type/" '
    ($id | ascii_downcase | startswith($prefix | ascii_downcase)) and
    ($id[($prefix | length):] | test("^[A-Za-z0-9][A-Za-z0-9_.()-]*$"))
  ' >/dev/null 2>&1 ||
    { sre_error "Telemetry $TELEMETRY_SOURCE: missing/foreign monitoring output; azd env refresh and retry."; return 1; }

  sre_telemetry_call source az resource show --ids "$resource_id" --subscription "$AZURE_SUBSCRIPTION_ID" \
    --api-version "$api" --only-show-errors --output json \
    --query '{id:id,tags:tags,appId:properties.AppId,workspaceId:properties.WorkspaceResourceId}' || return $?
  jq -e --arg id "$resource_id" --arg env "$AZURE_ENV_NAME" --arg source "$TELEMETRY_SOURCE" \
    --arg app "$SRE_APP_INSIGHTS_APP_ID" --arg workspace "${SRE_LOG_ANALYTICS_ID:-}" '
    (.id | ascii_downcase) == ($id | ascii_downcase) and .tags["azd-env-name"] == $env and
    ($source != "app-insights" or
      (.appId == $app and (.workspaceId | ascii_downcase) == ($workspace | ascii_downcase)))
  ' <<<"$TELEMETRY_RESPONSE" >/dev/null 2>&1 ||
    { sre_error "Telemetry $TELEMETRY_SOURCE: source ownership/AppId/workspace verification failed; refresh outputs and inspect drift."; return 1; }

  # A one-character source prefix preserves the complete 63-character agent
  # name while staying inside ARM's 64-character deployment-name limit.
  case "$TELEMETRY_SOURCE" in
    app-insights) deployment="a${SRE_AGENT_NAME}" ;;
    log-analytics) deployment="l${SRE_AGENT_NAME}" ;;
  esac
  sre_telemetry_call deploy az deployment group create --name "$deployment" \
    --resource-group "rg-${AZURE_ENV_NAME}" --subscription "$AZURE_SUBSCRIPTION_ID" \
    --mode Incremental --template-file "$TELEMETRY_TEMPLATE_FILE" \
    --parameters "agentName=$SRE_AGENT_NAME" "agentPrincipalId=$SRE_AGENT_PRINCIPAL_ID" \
      "source=$TELEMETRY_SOURCE" "appInsightsResourceId=${SRE_APP_INSIGHTS_ID:-}" \
      "appInsightsAppId=$SRE_APP_INSIGHTS_APP_ID" "logAnalyticsResourceId=${SRE_LOG_ANALYTICS_ID:-}" \
    --only-show-errors --output json --query '{state:properties.provisioningState}' || return $?
  jq -e '.state == "Succeeded"' <<<"$TELEMETRY_RESPONSE" >/dev/null 2>&1 ||
    { sre_error "Telemetry $TELEMETRY_SOURCE: deployment did not report Succeeded."; return 1; }

  connector_id="${AGENT_ID}/connectors/${TELEMETRY_SOURCE}"
  sre_telemetry_call connector az resource show --ids "$connector_id" --subscription "$AZURE_SUBSCRIPTION_ID" \
    --api-version "$SRE_API_VERSION" --only-show-errors --output json \
    --query '{id:id,properties:properties}' || return $?
  jq -e --arg id "$connector_id" --arg resource "$resource_id" --arg source "$TELEMETRY_SOURCE" \
    --arg app "$SRE_APP_INSIGHTS_APP_ID" '
    (.id | ascii_downcase) == ($id | ascii_downcase) and
    .properties.identity == "system" and
    .properties.dataConnectorType == (if $source == "app-insights" then "AppInsights" else "LogAnalytics" end) and
    (.properties.dataSource | ascii_downcase) == ($resource | ascii_downcase) and
    (.properties.extendedProperties.armResourceId | ascii_downcase) == ($resource | ascii_downcase) and
    .properties.extendedProperties.resource.name == ($resource | split("/") | last) and
    ($source != "app-insights" or .properties.extendedProperties.appId == $app)
  ' <<<"$TELEMETRY_RESPONSE" >/dev/null 2>&1 ||
    { sre_error "Telemetry $TELEMETRY_SOURCE: connector read-back differs from requested identity/target/AppId."; return 1; }
  TELEMETRY_SOURCE_STATE=pending
  echo "Telemetry $TELEMETRY_SOURCE configured/read back; pending connector-identity query verification (not ready)."
}

sre_setup_telemetry() {
  local source status failed=0
  APP_INSIGHTS_STATE=failed
  LOG_ANALYTICS_STATE=failed
  TELEMETRY_STATE=failed
  TELEMETRY_VALUES="$(sre_read_environment "$AZURE_ENV_NAME")" || return 1
  jq -e --arg agent "$AGENT_ID" --arg principal "$SRE_AGENT_PRINCIPAL_ID" \
    --arg app "$SRE_APP_INSIGHTS_APP_ID" --arg env "$AZURE_ENV_NAME" --arg sub "$AZURE_SUBSCRIPTION_ID" '
    .AZURE_ENV_NAME == $env and
    (.AZURE_SUBSCRIPTION_ID | ascii_downcase) == ($sub | ascii_downcase) and
    .SRE_AGENT_ID == $agent and .SRE_AGENT_PRINCIPAL_ID == $principal and
    .SRE_APP_INSIGHTS_APP_ID == $app
  ' <<<"$TELEMETRY_VALUES" >/dev/null 2>&1 ||
    { sre_error "Telemetry: injected agent/principal/AppId outputs differ from selected environment; refresh outputs."; return 1; }

  # Check installation before build; submit compiled JSON so deployment cannot
  # auto-install a compiler. Never expose raw compiler/ARM diagnostics.
  AZURE_BICEP_CHECK_VERSION=false az bicep version >/dev/null 2>"$ERROR_FILE" ||
    { sre_error "Telemetry needs an installed Bicep CLI. Run az bicep install yourself and retry; no installer was run."; return 1; }
  TELEMETRY_TEMPLATE_FILE="$(mktemp)" ||
    { sre_error "Cannot create private telemetry template scratch file."; return 1; }
  AZURE_BICEP_CHECK_VERSION=false az bicep build --file "${SCRIPT_DIR}/../infra/sre-telemetry.bicep" \
    --stdout --only-show-errors >"$TELEMETRY_TEMPLATE_FILE" 2>"$ERROR_FILE" ||
    { sre_error "Telemetry template compilation failed. Inspect Bicep locally; raw diagnostics suppressed."; return 1; }
  jq -e 'type == "object" and (.resources | type == "array" or type == "object")' \
    "$TELEMETRY_TEMPLATE_FILE" >/dev/null 2>&1 ||
    { sre_error "Telemetry compiler returned malformed ARM JSON."; return 1; }
  for source in app-insights log-analytics; do
    sre_telemetry_source "$source"
    status=$?
    if [ "$status" -eq 1 ]; then
      TELEMETRY_SOURCE_STATE=failed
      failed=1
    elif [ "$status" -eq 3 ]; then
      failed=1
    elif [ "$status" -ne 0 ] && [ "$status" -ne 2 ]; then
      sre_error "Telemetry $source: unexpected setup exit $status."
      TELEMETRY_SOURCE_STATE=failed
      failed=1
    fi
    case "$source" in
      app-insights) APP_INSIGHTS_STATE="$TELEMETRY_SOURCE_STATE" ;;
      log-analytics) LOG_ANALYTICS_STATE="$TELEMETRY_SOURCE_STATE" ;;
    esac
  done
  # Precedence preserves partial results in the preceding per-source line.
  TELEMETRY_STATE=pending
  if [ "$APP_INSIGHTS_STATE" = unavailable ] || [ "$LOG_ANALYTICS_STATE" = unavailable ]; then
    TELEMETRY_STATE=unavailable
  fi
  if [ "$APP_INSIGHTS_STATE" = failed ] || [ "$LOG_ANALYTICS_STATE" = failed ]; then
    TELEMETRY_STATE=failed
  fi
  echo "Telemetry retry: azd env refresh --environment <azd-environment> --no-prompt; ./hooks/sre-setup.sh --environment <azd-environment>."
  echo "For core-only use SRE_CONNECT_TELEMETRY=false. No core or working connector was removed. See docs/sre-agent.md for identity-bound live verification."
  return "$failed"
}
