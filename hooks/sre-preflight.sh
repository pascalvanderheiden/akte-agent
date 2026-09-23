#!/usr/bin/env bash
#
# Preprovision checks for the opt-in Azure SRE Agent.
#
# Runs on every provision but does nothing unless DEPLOY_SRE_AGENT is true:
# environments that have not opted in make no Azure calls here at all.
#
# What it checks, and why here rather than in Bicep: an unsupported region, an
# unregistered provider, or a subscription without SRE service access all
# surface from ARM as an opaque template failure minutes into a provision.
# Checking first turns those into actionable messages before anything is
# created.
#
# What it deliberately does NOT do: log in, install tools, register providers,
# change consent, switch subscription or region, or prompt. It reads state and
# explains what the operator must do.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/sre-lib.sh
. "${SCRIPT_DIR}/sre-lib.sh"

SRE_API_VERSION="2025-05-01-preview"

ENABLED="$(sre_bool DEPLOY_SRE_AGENT "${DEPLOY_SRE_AGENT:-}" false)" || exit 1
if [ "$ENABLED" != "true" ]; then
  exit 0
fi

# Validate the optional-integration switches now too: a typo in either should
# fail before provisioning rather than halfway through setup.
sre_bool SRE_CONNECT_TELEMETRY "${SRE_CONNECT_TELEMETRY:-}" true >/dev/null || exit 1
sre_bool SRE_CONNECT_GITHUB "${SRE_CONNECT_GITHUB:-}" true >/dev/null || exit 1

echo "🔎 Azure SRE Agent is enabled for this environment — running preflight checks ..."

if ! command -v az >/dev/null 2>&1; then
  echo "❌ The Azure CLI (az) is required to provision the SRE Agent but was not found." >&2
  echo "   Install it (https://aka.ms/azure-cli) and sign in with 'az login', or set" >&2
  echo "   DEPLOY_SRE_AGENT=false to provision without SRE." >&2
  exit 1
fi

ACCOUNT="$(az account show --query id -o tsv 2>/dev/null)"
if [ -z "$ACCOUNT" ]; then
  echo "❌ No active Azure CLI session." >&2
  echo "   Sign in yourself with 'az login' and re-run — this hook never opens a" >&2
  echo "   browser or prompts." >&2
  exit 1
fi

CURRENT_SUB="$ACCOUNT"
if [ -n "${AZURE_SUBSCRIPTION_ID:-}" ] && [ "$CURRENT_SUB" != "${AZURE_SUBSCRIPTION_ID}" ]; then
  echo "❌ The Azure CLI is pointed at a different subscription than this azd environment." >&2
  echo "   azd environment: ${AZURE_SUBSCRIPTION_ID}" >&2
  echo "   Azure CLI:       ${CURRENT_SUB:-<none>}" >&2
  echo "   Select the right one yourself with:" >&2
  echo "     az account set --subscription ${AZURE_SUBSCRIPTION_ID}" >&2
  echo "   (this hook will not switch subscriptions for you)." >&2
  exit 1
fi

# The SRE Agent's own region. Defaults to the application region, and is only
# accepted when the service is actually offered there — an unsupported
# application region needs SRE_LOCATION, never a silent relocation.
SRE_REGION="${SRE_LOCATION:-${AZURE_LOCATION:-}}"
if [ -z "$SRE_REGION" ]; then
  echo "❌ No region to deploy the SRE Agent into." >&2
  echo "   Set AZURE_LOCATION for the environment, or SRE_LOCATION to place SRE in a" >&2
  echo "   supported region while the application stays where it is." >&2
  exit 1
fi
SRE_REGION_KEY="$(printf '%s' "$SRE_REGION" | tr -d ' ' | tr '[:upper:]' '[:lower:]')"

REGISTRATION="$(az provider show --namespace Microsoft.App --query registrationState -o tsv 2>/dev/null)"
if [ "$REGISTRATION" != "Registered" ]; then
  echo "❌ Resource provider Microsoft.App is '${REGISTRATION:-unknown}' in subscription ${CURRENT_SUB}." >&2
  echo "   Someone with permission on the subscription must register it:" >&2
  echo "     az provider register --namespace Microsoft.App" >&2
  echo "   (this hook does not change provider registration)." >&2
  exit 1
fi

# Provider registration alone proves nothing about SRE: the service is
# previewed per subscription and per region. The authoritative signal available
# without deploying is whether the agents resource type is offered here.
LOCATIONS="$(az provider show --namespace Microsoft.App \
  --query "resourceTypes[?resourceType=='agents'].locations | [0]" -o tsv 2>/dev/null)"
LOCATIONS_QUERY_STATUS=$?

if [ $LOCATIONS_QUERY_STATUS -ne 0 ]; then
  echo "⚠️  Could not verify SRE Agent availability for this subscription (the provider" >&2
  echo "   query failed). Continuing — provisioning may still fail if the service is" >&2
  echo "   not available here. This is not a confirmation that it is." >&2
elif [ -z "$LOCATIONS" ]; then
  echo "❌ The Microsoft.App/agents resource type is not offered to subscription ${CURRENT_SUB}." >&2
  echo "   Azure SRE Agent access is granted per subscription; registration of the" >&2
  echo "   Microsoft.App provider does not by itself make the service available." >&2
  echo "   Request access (https://aka.ms/sre-agent) or set DEPLOY_SRE_AGENT=false." >&2
  exit 1
else
  SUPPORTED=""
  while IFS= read -r loc; do
    [ -n "$loc" ] || continue
    key="$(printf '%s' "$loc" | tr -d ' ' | tr '[:upper:]' '[:lower:]')"
    if [ "$key" = "$SRE_REGION_KEY" ]; then
      SUPPORTED="yes"
      break
    fi
  done <<<"$LOCATIONS"

  if [ -z "$SUPPORTED" ]; then
    echo "❌ Region '${SRE_REGION}' does not offer the SRE Agent in this subscription." >&2
    echo "   Supported here:" >&2
    printf '%s\n' "$LOCATIONS" | sed 's/^/     /' >&2
    echo "   Pick one with 'azd env set SRE_LOCATION <region>'. The application stays" >&2
    echo "   in ${AZURE_LOCATION:-its current region} — nothing is relocated." >&2
    exit 1
  fi
fi

# Role assignments need the caller's principal type. A CI federated login is a
# ServicePrincipal and a developer's 'az login' is a User; guessing either way
# breaks the other. Recorded through 'azd env set', never by editing .azure/.
if [ -z "${AZURE_PRINCIPAL_TYPE:-}" ] && command -v azd >/dev/null 2>&1; then
  case "$(az account show --query user.type -o tsv 2>/dev/null)" in
    servicePrincipal) azd env set AZURE_PRINCIPAL_TYPE ServicePrincipal >/dev/null 2>&1 || true ;;
    user) azd env set AZURE_PRINCIPAL_TYPE User >/dev/null 2>&1 || true ;;
    *) ;;
  esac
fi

echo "   ✅ Preflight passed — SRE Agent will be provisioned in '${SRE_REGION}'"
echo "      using API version ${SRE_API_VERSION}, read-only (accessLevel Low, actionMode Review)."
exit 0
