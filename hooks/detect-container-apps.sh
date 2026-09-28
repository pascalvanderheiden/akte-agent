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
# It is not best-effort. An unverified `false` is precisely the answer that
# overwrites a running image, and it is also the default the parameters file
# falls back to, so a query this hook cannot complete or record aborts the
# provision instead of letting that default through. The one exception is an
# environment that already records every service as existing: preserving an
# image is the harmless direction, so those runs continue unchanged.

set -uo pipefail

# The `host: containerapp` services in azure.yaml, by their azd service name —
# which is also the `azd-service-name` tag value their Bicep modules set. Adding
# such a service means adding it here; a test fails if the two lists drift.
SERVICES=(agent-service obo-mcp-server)

note() { echo "   $*"; }

flag_for() {
  # Two passes: mixing a character class with a literal in one `tr` set is not
  # portable (BSD tr on macOS reads it differently from GNU tr), and a
  # mistranslated name would silently leave the real flag untouched.
  printf 'SERVICE_%s_RESOURCE_EXISTS' "$(printf '%s' "$1" | tr '-' '_' | tr '[:lower:]' '[:upper:]')"
}

recorded_flag() {
  printf '%s\n' "$RECORDED" | grep "^$1=" | tail -n 1 | cut -d= -f2- | tr -d '"'
}

# Called when Azure cannot answer, or the answer cannot be written back.
# Continuing is only safe when azd already records every service as existing,
# because then no flag can fall back to the `false` that replaces a running
# image; anything else stops the provision.
give_up() {
  for service in "${SERVICES[@]}"; do
    if [ "$(recorded_flag "$(flag_for "$service")")" != "true" ]; then
      echo "   $* Provisioning now could replace a running application image" >&2
      echo "   with the bootstrap image, so this provision stops here." >&2
      exit 1
    fi
  done
  note "$* Every service is already recorded as existing, so its image stays preserved."
  exit 0
}

command -v azd >/dev/null 2>&1 || {
  echo "   azd not found; the service-existence flags cannot be read or written." >&2
  exit 1
}

RECORDED="$(azd env get-values 2>/dev/null)" || RECORDED=""

command -v az >/dev/null 2>&1 || give_up "az not found; cannot ask Azure which Container Apps exist."

# infra/main.bicep names the resource group `rg-<environmentName>`. The azd
# output wins when the environment has been refreshed.
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-}"
if [ -z "$RESOURCE_GROUP" ] && [ -n "${AZURE_ENV_NAME:-}" ]; then
  RESOURCE_GROUP="rg-${AZURE_ENV_NAME}"
fi
[ -n "$RESOURCE_GROUP" ] || give_up "No resource group to inspect."

# Empty unless the environment names a subscription; expanded with the `+`
# form so `set -u` does not trip over an empty array.
SUBSCRIPTION_ARGS=()
[ -n "${AZURE_SUBSCRIPTION_ID:-}" ] && SUBSCRIPTION_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")

GROUP_EXISTS="$(az group exists --name "$RESOURCE_GROUP" ${SUBSCRIPTION_ARGS[@]+"${SUBSCRIPTION_ARGS[@]}"} \
  --output tsv --only-show-errors 2>/dev/null)" || GROUP_EXISTS=""
case "$GROUP_EXISTS" in
  true)
    TAGS="$(az containerapp list --resource-group "$RESOURCE_GROUP" ${SUBSCRIPTION_ARGS[@]+"${SUBSCRIPTION_ARGS[@]}"} \
      --query "[].tags.\"azd-service-name\"" --output tsv --only-show-errors 2>/dev/null)" \
      || give_up "Could not list Container Apps in $RESOURCE_GROUP."
    ;;
  false)
    # A resource group that is not there cannot hold a Container App, so there
    # is no running image to protect: every service is a first create.
    TAGS=""
    ;;
  *)
    give_up "Could not reach $RESOURCE_GROUP."
    ;;
esac

for service in "${SERVICES[@]}"; do
  flag="$(flag_for "$service")"
  # Fed by here-string rather than a pipe: with `pipefail`, `grep -q` exiting on
  # its first match can leave the writer killed by SIGPIPE, and that status
  # would be read as a failure to classify.
  grep -qx -- "$service" <<<"$TAGS"
  case $? in
    0) value=true ;;
    1) value=false ;;
    # grep failed rather than reported "no match"; there is no answer to record.
    *) give_up "Could not classify $service." ;;
  esac
  if azd env set "$flag" "$value" >/dev/null 2>&1; then
    note "$flag=$value (from Azure)"
  else
    give_up "Could not record $flag=$value."
  fi
done

exit 0
