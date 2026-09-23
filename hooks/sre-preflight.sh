#!/usr/bin/env bash
# Noninteractive, opt-in checks. Never log in, install tools, change cloud
# registration/consent, or switch subscriptions/regions.
set +x
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/sre-lib.sh
. "${SCRIPT_DIR}/sre-lib.sh"

sre_load_environment "$@" || exit 1
ENABLED="$(sre_bool DEPLOY_SRE_AGENT "${DEPLOY_SRE_AGENT:-}" false)" || exit 1
[ "$ENABLED" = "true" ] || exit 0
sre_bool SRE_CONNECT_TELEMETRY "${SRE_CONNECT_TELEMETRY:-}" true >/dev/null || exit 1
sre_bool SRE_CONNECT_GITHUB "${SRE_CONNECT_GITHUB:-}" true >/dev/null || exit 1

SRE_REGION="${SRE_LOCATION:-${AZURE_LOCATION:-}}"
[[ "$SRE_REGION" =~ ^[a-zA-Z0-9][a-zA-Z0-9\ ]*$ ]] ||
  { sre_error "Set AZURE_LOCATION or SRE_LOCATION to a supported Azure region."; exit 1; }

# Restrict overrides to DNS-label names; ARM remains the authority on service
# naming/uniqueness. The default suffix is Bicep's 13-character uniqueString.
if [ -n "${SRE_AGENT_NAME_OVERRIDE:-}" ]; then
  CANDIDATE="$SRE_AGENT_NAME_OVERRIDE"
else
  CANDIDATE="${AZURE_RESOURCE_PREFIX:+${AZURE_RESOURCE_PREFIX}-}sre-0000000000000"
fi
[[ "$CANDIDATE" =~ ^[a-z][a-z0-9-]{1,61}[a-z0-9]$ ]] ||
  { sre_error "SRE_AGENT_NAME_OVERRIDE (or generated AZURE_RESOURCE_PREFIX name) must be a 3-63 character DNS label starting with a lowercase letter and ending with a letter/digit."; exit 1; }

case "${AZURE_PRINCIPAL_TYPE:-}" in
  "" | User | ServicePrincipal | Group) ;;
  *) sre_error "AZURE_PRINCIPAL_TYPE must be User, ServicePrincipal, or Group."; exit 1 ;;
esac

sre_check_context || exit 1
echo "Checking Azure SRE Agent provider metadata..."
PROVIDER="$(az provider show --namespace Microsoft.App --subscription "$AZURE_SUBSCRIPTION_ID" \
  --output json --only-show-errors 2>/dev/null)" ||
  { sre_error "Could not verify Microsoft.App availability (query failed or access denied). Check provider-read permission/network access and retry, or set DEPLOY_SRE_AGENT=false. Eligibility is unverified."; exit 1; }
jq -e 'type == "object" and (.registrationState | type == "string") and
  (.resourceTypes | type == "array")
' <<<"$PROVIDER" >/dev/null 2>&1 ||
  { sre_error "Malformed Microsoft.App provider response; availability is unverified."; exit 1; }
if [ "$(jq -r '.registrationState' <<<"$PROVIDER")" != "Registered" ]; then
  sre_error "Microsoft.App is not registered. An authorized operator must run az provider register --namespace Microsoft.App, or disable SRE; this hook never registers providers."
  exit 1
fi
AGENTS="$(jq -c '[.resourceTypes[] | select(.resourceType == "agents")]' <<<"$PROVIDER" 2>/dev/null)" ||
  { sre_error "Malformed Microsoft.App resource-type metadata; availability is unverified."; exit 1; }
jq -e 'length == 1 and (.[0].locations | type == "array" and length > 0 and
  all(.[]; type == "string" and test("^[a-zA-Z0-9 ]+$"))) and
  (.[0].apiVersions | type == "array" and all(.[]; type == "string"))
' <<<"$AGENTS" >/dev/null 2>&1 ||
  { sre_error "Microsoft.App/agents metadata or locations are absent/malformed. Registration alone does not prove SRE access. Check subscription availability at https://sre.azure.com or disable SRE."; exit 1; }
jq -e --arg api "$SRE_API_VERSION" '.[0].apiVersions | index($api) != null' <<<"$AGENTS" >/dev/null ||
  { sre_error "The pinned SRE API version is not advertised for this subscription. Check availability or disable SRE; no API version was substituted."; exit 1; }
jq -e --arg region "$(sre_region_key "$SRE_REGION")" '
  .[0].locations | map(ascii_downcase | gsub(" "; "")) | index($region) != null
' <<<"$AGENTS" >/dev/null ||
  {
    sre_error "The selected region does not offer SRE in provider metadata. Set SRE_LOCATION to a supported region; the application will not move."
    jq -r '.[0].locations[]' <<<"$AGENTS" >&2
    exit 1
  }

if [ -z "${AZURE_PRINCIPAL_TYPE:-}" ]; then
  case "$SRE_CALLER_TYPE" in
    servicePrincipal) PRINCIPAL_TYPE=ServicePrincipal ;;
    user) PRINCIPAL_TYPE=User ;;
    *) sre_error "Cannot determine operator principal type. Set AZURE_PRINCIPAL_TYPE explicitly before provisioning."; exit 1 ;;
  esac
  azd env set AZURE_PRINCIPAL_TYPE "$PRINCIPAL_TYPE" --environment "$AZURE_ENV_NAME" --no-prompt \
    >/dev/null 2>&1 ||
    { sre_error "Could not persist AZURE_PRINCIPAL_TYPE through azd. Correct environment write access and retry."; exit 1; }
fi

echo "Preflight passed for region '$SRE_REGION' and API $SRE_API_VERSION (Low / Review)."
echo "Provider metadata is not proof of SRE subscription eligibility. Confirm available regions at"
echo "https://sre.azure.com; ARM provisioning can still be denied. No registration or context was changed."
