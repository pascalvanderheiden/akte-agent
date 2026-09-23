#!/usr/bin/env bash
#
# Grant tenant-wide admin consent for the OBO server app's delegated Microsoft
# Graph `User.Read` permission.
#
# Why this is a hook and not Bicep
# --------------------------------
# The app registration *requests* User.Read declaratively in
# infra/modules/obo-entra-app.bicep (`requiredResourceAccess`) — that needs no
# special rights. Actually *granting* it is a different operation: an
# `oauth2PermissionGrants` with consentType `AllPrincipals` consents on behalf of
# every user in the tenant, which Microsoft Graph refuses unless the caller holds
# an Entra directory role. Azure RBAC does not help — subscription Owner confers
# nothing in the directory.
#
# When the grant lived in Bicep, deploying into a tenant where consent is
# admin-gated (the default for most corporate tenants) failed the entire
# provision with a bare `Authorization_RequestDenied`, giving no hint that the
# problem was an Entra role rather than an Azure one. Bicep cannot attempt a
# resource and continue when it is forbidden, so the grant moved here, where a
# 403 can be reported clearly and the deployment allowed to finish.
#
# Consequence of a skipped grant: OBO still works, but the first user to sign in
# is prompted to consent to User.Read themselves, or an administrator grants it
# once (see the instructions this script prints on failure).
#
# Idempotent and best-effort: re-running is safe (an existing grant is detected
# and left alone) and no failure mode aborts the deploy — this script always
# exits 0.

set -uo pipefail

MS_GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
SCOPE="User.Read"

# Only meaningful when the OBO stack was actually provisioned. Mirrors the
# `condition:`/`if (deployObo)` gating in azure.yaml and infra/main.bicep.
case "$(printf '%s' "${DEPLOY_OBO:-true}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes) ;;
  *) exit 0 ;;
esac

APP_ID="${OBO_SERVER_APP_CLIENT_ID:-}"
if [ -z "$APP_ID" ]; then
  # Provisioning was skipped or the output is not in the environment. Nothing to
  # consent to; staying quiet avoids noise on every non-OBO deploy.
  exit 0
fi

echo "🔑 Granting admin consent for the OBO server app's Graph $SCOPE ..."

# The grant references service principal OBJECT ids, not app ids.
CLIENT_SP_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv 2>/dev/null || true)"
GRAPH_SP_ID="$(az ad sp show --id "$MS_GRAPH_APP_ID" --query id -o tsv 2>/dev/null || true)"

if [ -z "$CLIENT_SP_ID" ] || [ -z "$GRAPH_SP_ID" ]; then
  echo "   ⚠️  Could not resolve the service principals needed for the grant."
  echo "      Skipping — see the manual steps below."
  CLIENT_SP_ID=""
fi

if [ -n "$CLIENT_SP_ID" ]; then
  # Already consented? Re-granting would create a duplicate grant object rather
  # than failing, so check first to keep repeat deploys genuinely idempotent.
  EXISTING="$(az rest --method GET \
    --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?\$filter=clientId eq '${CLIENT_SP_ID}' and resourceId eq '${GRAPH_SP_ID}'" \
    --query "value[].scope" -o tsv 2>/dev/null || true)"

  # Each grant's scope is a space-separated list; match whole words only so
  # User.ReadWrite.All is never mistaken for User.Read.
  for granted in $EXISTING; do
    if [ "$granted" = "$SCOPE" ]; then
      echo "   ✅ Admin consent already in place (nothing to do)."
      exit 0
    fi
  done

  CONSENT_ERR="$(mktemp)"
  trap 'rm -f "$CONSENT_ERR"' EXIT

  if az rest --method POST \
      --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
      --headers "Content-Type=application/json" \
      --body "{\"clientId\":\"${CLIENT_SP_ID}\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"${GRAPH_SP_ID}\",\"scope\":\"${SCOPE}\"}" \
      >/dev/null 2>"$CONSENT_ERR"; then
    echo "   ✅ Admin consent granted."
    exit 0
  fi

  if grep -qiE "Authorization_RequestDenied|Insufficient privileges|Forbidden" "$CONSENT_ERR"; then
    echo "   ℹ️  Your account is not allowed to grant tenant-wide admin consent."
    echo "      This is expected in most corporate tenants and does NOT break the"
    echo "      deployment — everything else provisioned normally."
    echo "      Granting consent needs an Entra ID directory role — Global"
    echo "      Administrator, Privileged Role Administrator, or Cloud Application"
    echo "      Administrator. Azure RBAC (even subscription Owner) does not"
    echo "      include it."
  else
    echo "   ⚠️  The consent request failed for an unexpected reason:"
    sed -n '1,10p' "$CONSENT_ERR" | sed 's/^/      /'
  fi
fi

# Shown for every unsuccessful path, since the manual remedy is the same
# whether consent was forbidden, errored, or the lookups failed.
cat <<EOF
      Until someone grants it, OBO still works: the first user to sign in is
      asked to consent to Graph $SCOPE themselves.

      To grant it once, an administrator can run:
        az ad app permission admin-consent --id $APP_ID

      ...or open the app registration in the portal, go to
      "API permissions", and choose "Grant admin consent".
EOF

exit 0
