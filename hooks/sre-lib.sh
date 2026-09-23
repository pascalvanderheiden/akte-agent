#!/usr/bin/env bash
# Shared core/setup boundary. No token acquisition or credential persistence.

export SRE_API_VERSION="2025-05-01-preview"

sre_error() {
  printf 'SRE error: %s\n' "$1" >&2
  return 1
}

sre_bool() {
  case "${2:-$3}" in
    true | false) printf '%s' "${2:-$3}" ;;
    *) sre_error "$1 must be true or false. Fix it with: azd env set $1 false" ;;
  esac
}

sre_require_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 ||
      { sre_error "Required tool '$tool' is missing. Install it yourself, or set DEPLOY_SRE_AGENT=false."; return 1; }
  done
}

sre_read_environment() {
  local values
  values="$(azd env get-values --environment "$1" --output json --no-prompt 2>/dev/null)" ||
    { sre_error "Cannot read the selected azd environment. Select/create it yourself and retry."; return 1; }
  jq -e 'type == "object" and all(.[]; type == "string")' <<<"$values" >/dev/null 2>&1 ||
    { sre_error "azd returned malformed environment values; nothing was loaded."; return 1; }
  printf '%s' "$values"
}

# Standalone reruns explicitly select an environment. Import only nonsecret
# SRE inputs/outputs as data, never shell-evaluate azd output or retain stale
# values from another environment.
sre_load_environment() {
  [ "$#" -gt 0 ] || return 0
  if [ "$#" -ne 2 ] || [ "$1" != "--environment" ] || [ -z "$2" ]; then
    sre_error "Usage: $0 [--environment <azd-environment>]"
    return 1
  fi
  sre_require_tools azd jq || return 1
  local values key value
  values="$(sre_read_environment "$2")" || return 1
  jq -e --arg name "$2" '.AZURE_ENV_NAME == $name' <<<"$values" >/dev/null ||
    { sre_error "The returned azd environment does not match --environment."; return 1; }
  for key in AZURE_ENV_NAME AZURE_SUBSCRIPTION_ID AZURE_TENANT_ID AZURE_LOCATION \
    AZURE_RESOURCE_PREFIX AZURE_RESOURCE_GROUP AZURE_PRINCIPAL_ID AZURE_PRINCIPAL_TYPE \
    DEPLOY_SRE_AGENT SRE_CONNECT_TELEMETRY SRE_CONNECT_GITHUB SRE_LOCATION \
    SRE_AGENT_NAME_OVERRIDE SRE_AGENT_ENABLED SRE_AGENT_ID SRE_AGENT_NAME SRE_AGENT_LOCATION \
    SRE_AGENT_PRINCIPAL_ID SRE_AGENT_IDENTITY_ID SRE_AGENT_IDENTITY_PRINCIPAL_ID \
    SRE_AGENT_PORTAL_URL SRE_APP_INSIGHTS_APP_ID SRE_APP_INSIGHTS_ID SRE_LOG_ANALYTICS_ID; do
    value="$(jq -r --arg key "$key" '.[$key] // ""' <<<"$values")" || return 1
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done
}

sre_check_context() {
  sre_require_tools az azd jq || return 1
  local values account
  [ -n "${AZURE_ENV_NAME:-}" ] && [ -n "${AZURE_SUBSCRIPTION_ID:-}" ] ||
    { sre_error "AZURE_ENV_NAME and AZURE_SUBSCRIPTION_ID are required; use azd or --environment for a standalone run."; return 1; }
  values="$(sre_read_environment "$AZURE_ENV_NAME")" || return 1
  jq -e --arg name "$AZURE_ENV_NAME" --arg sub "$AZURE_SUBSCRIPTION_ID" '
    .AZURE_ENV_NAME == $name and
    ((.AZURE_SUBSCRIPTION_ID // "" | ascii_downcase) == ($sub | ascii_downcase))
  ' <<<"$values" >/dev/null 2>&1 ||
    { sre_error "Injected context differs from the selected azd environment; clear stale shell values and retry."; return 1; }
  account="$(az account show --output json --only-show-errors 2>/dev/null)" ||
    { sre_error "Cannot read the Azure CLI session. Sign in yourself with az login and retry."; return 1; }
  jq -e 'type == "object" and (.id | type == "string" and length > 0) and
    (.tenantId | type == "string" and length > 0) and (.user.type | type == "string")
  ' <<<"$account" >/dev/null 2>&1 ||
    { sre_error "Azure CLI returned malformed account context."; return 1; }
  jq -e --arg sub "$AZURE_SUBSCRIPTION_ID" --arg tenant "${AZURE_TENANT_ID:-}" '
    (.id | ascii_downcase) == ($sub | ascii_downcase) and
    ($tenant == "" or (.tenantId | ascii_downcase) == ($tenant | ascii_downcase))
  ' <<<"$account" >/dev/null ||
    { sre_error "Azure CLI subscription/tenant differs from azd. Use az account set --subscription <selected-subscription> or az login yourself; no context was switched."; return 1; }
  SRE_CALLER_TYPE="$(jq -r '.user.type' <<<"$account")"
  export SRE_CALLER_TYPE
}

sre_region_key() {
  printf '%s' "$1" | tr -d ' ' | tr '[:upper:]' '[:lower:]'
}

# Keep the original final line and three-argument interface unchanged.
# Follow-on telemetry setup can supply independent source states as args 4/5.
# Optional states: disabled, ready, pending, unavailable, failed.
# pending includes capabilities not implemented in this core-only release.
sre_result() {
  local core="$1" telemetry="$2" github="$3"
  printf 'SRE_TELEMETRY_RESULT app_insights=%s log_analytics=%s\n' "${4:-$telemetry}" "${5:-$telemetry}"
  printf 'SRE_RESULT core=%s telemetry=%s github=%s\n' "$core" "$telemetry" "$github"
}
