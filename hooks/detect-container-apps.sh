#!/usr/bin/env bash
#
# Record, from Azure rather than from local state, whether each `host:
# containerapp` service already exists.
#
# infra/modules/container-app-image.bicep preserves the image running on a
# Container App so that provisioning an already-deployed environment does not
# replace the application with its bootstrap stand-in. ARM cannot read a
# resource that may not exist, so that preservation is gated on a flag —
# `SERVICE_<NAME>_RESOURCE_EXISTS`, which azd writes into the environment after
# it deploys a service.
#
# That flag alone is not enough. It lives in `.azure/`, which is gitignored and
# therefore absent on a fresh CI runner: `.github/workflows/deploy.yml` creates
# or selects an environment and can provision before anything has repopulated
# it. The flag would default to `false` for a live Container App, and the
# provision would push the bootstrap image over a running application until the
# following deploy replaced it again.
#
# So the source of truth is Azure. This hook asks the resource group which
# Container Apps are there, keyed by the `azd-service-name` tag the modules
# stamp on them, and writes the answer back with `azd env set` before the
# parameters in infra/main.parameters.json are resolved.
#
# Conservative by construction: a flag is only lowered to `false` on a
# successful query that proves the app is absent. If az is missing, the
# subscription cannot be reached, or the query fails, whatever azd already
# recorded is left alone — a wrong `false` is the failure mode that overwrites a
# running image, and this hook must never introduce one. Always exits 0.

set -uo pipefail

# The `host: containerapp` services in azure.yaml, by their azd service name —
# which is also the `azd-service-name` tag value their Bicep modules set.
SERVICES=(agent-service obo-mcp-server)

note() { echo "   $*"; }

command -v az >/dev/null 2>&1 || {
  note "Azure CLI not found; leaving service-existence flags as azd recorded them."
  exit 0
}

# infra/main.bicep names the resource group `rg-<environmentName>`. The azd
# output wins when the environment has been refreshed.
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-}"
if [ -z "$RESOURCE_GROUP" ] && [ -n "${AZURE_ENV_NAME:-}" ]; then
  RESOURCE_GROUP="rg-${AZURE_ENV_NAME}"
fi
[ -n "$RESOURCE_GROUP" ] || {
  note "No resource group to inspect; leaving service-existence flags unchanged."
  exit 0
}

# Empty unless the environment names a subscription; expanded with the `+`
# form so `set -u` does not trip over an empty array.
SUBSCRIPTION_ARGS=()
[ -n "${AZURE_SUBSCRIPTION_ID:-}" ] && SUBSCRIPTION_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")

GROUP_EXISTS="$(az group exists --name "$RESOURCE_GROUP" ${SUBSCRIPTION_ARGS[@]+"${SUBSCRIPTION_ARGS[@]}"} \
  --output tsv --only-show-errors 2>/dev/null)" || GROUP_EXISTS=""
case "$GROUP_EXISTS" in
  true) ;;
  false)
    # A resource group that is not there cannot hold a Container App, so there
    # is no running image to protect: every service is a first create.
    TAGS=""
    ;;
  *)
    note "Could not reach $RESOURCE_GROUP; leaving service-existence flags unchanged."
    exit 0
    ;;
esac

if [ "$GROUP_EXISTS" = "true" ]; then
  TAGS="$(az containerapp list --resource-group "$RESOURCE_GROUP" ${SUBSCRIPTION_ARGS[@]+"${SUBSCRIPTION_ARGS[@]}"} \
    --query "[].tags.\"azd-service-name\"" --output tsv --only-show-errors 2>/dev/null)" || {
    note "Could not list Container Apps in $RESOURCE_GROUP; leaving service-existence flags unchanged."
    exit 0
  }
fi

for service in "${SERVICES[@]}"; do
  flag="SERVICE_$(printf '%s' "$service" | tr '[:lower:]-' '[:upper:]_')_RESOURCE_EXISTS"
  if printf '%s\n' "$TAGS" | grep -qx -- "$service"; then
    value=true
  else
    value=false
  fi
  if azd env set "$flag" "$value" >/dev/null 2>&1; then
    note "$flag=$value (from Azure)"
  else
    note "Could not record $flag; azd will use its own value."
  fi
done

exit 0
