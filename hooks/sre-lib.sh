#!/usr/bin/env bash
#
# Shared helpers and the setup/result contract for the SRE Agent hooks.
#
# Sourced by hooks/sre-preflight.sh and hooks/sre-setup.sh. Follow-on work
# (telemetry connectors, GitHub attachment) reports through the same contract,
# so a degraded setup can never be summarised as fully connected.
#
# The contract
# ------------
# Three components are reported independently:
#   core       the SRE Agent resource itself   — disabled | ready | failed
#   telemetry  workload telemetry connectors   — disabled | ready | pending |
#   github     repository attachment             unavailable | failed
#
# States mean:
#   disabled     switched off for this environment; nothing was attempted
#   ready        verified working, not merely created
#   pending      needs an authorization or a value that a hook may not supply
#   unavailable  confirmed unsupported by the service, policy, or tenant
#   failed       attempted and errored
#
# The last line of a setup run is always machine-readable:
#   SRE_RESULT core=<state> telemetry=<state> github=<state>

# Normalise a boolean environment value. Malformed input is an explicit error
# rather than a silent "off", so `DEPLOY_SRE_AGENT=ture` cannot quietly skip
# provisioning the operator asked for.
#
# Usage: value="$(sre_bool NAME "${NAME:-}" <default>)" || exit 1
sre_bool() {
  local name="$1" raw="${2:-}" fallback="${3:-}"
  [ -n "$raw" ] || raw="$fallback"
  case "$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')" in
    true | 1 | yes) printf 'true' ;;
    false | 0 | no) printf 'false' ;;
    *)
      echo "❌ ${name} must be true or false (got '${raw}')." >&2
      echo "   Fix it with: azd env set ${name} false" >&2
      return 1
      ;;
  esac
}

# Emit the result contract. Every setup run ends with exactly one of these.
sre_result() {
  local core="$1" telemetry="$2" github="$3"
  echo "   core:      ${core}"
  echo "   telemetry: ${telemetry}"
  echo "   github:    ${github}"
  echo "SRE_RESULT core=${core} telemetry=${telemetry} github=${github}"
}
