#!/usr/bin/env bash
#
# solace-bootstrap.sh — provision the `verdictcouncil` VPN + `vc-agent` client
# user on the local Solace broker so the web-gateway, SAM agents, and
# layer2-aggregator can authenticate.
#
# Mirrors k8s/base/solace-bootstrap-job.yaml (skipping the HA redundancy wait,
# which does not apply to the single-container dev broker). Idempotent:
# re-running after the VPN/user already exist is a no-op.

set -euo pipefail

SEMP_HOST="${SEMP_HOST:-localhost}"
SEMP_PORT="${SEMP_PORT:-8080}"
ADMIN_USERNAME="${SOLACE_ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${SOLACE_ADMIN_PASSWORD:-admin}"
VPN="${SOLACE_BROKER_VPN:-verdictcouncil}"
USER="${SOLACE_BROKER_USERNAME:-vc-agent}"
PASS="${SOLACE_BROKER_PASSWORD:-vc-agent-password}"

SEMP="http://${SEMP_HOST}:${SEMP_PORT}/SEMP/v2/config"
AUTH="${ADMIN_USERNAME}:${ADMIN_PASSWORD}"

# ----- wait for SEMPv2 -----
printf "Waiting for Solace SEMPv2 on %s:%s ...\n" "$SEMP_HOST" "$SEMP_PORT"
ready=0
for i in $(seq 1 60); do
  if curl -sfu "$AUTH" "${SEMP}/about/api" >/dev/null 2>&1; then
    printf "  SEMPv2 responsive after %ss\n" "$((i * 2))"
    ready=1
    break
  fi
  sleep 2
done
if (( ! ready )); then
  printf "error: SEMPv2 did not respond within 120s — is vc-solace running?\n" >&2
  exit 1
fi

# semp_post <url> <json-payload> <log-label>
# Accepts HTTP 200 (created) and 400 (already exists); fails on anything else.
semp_post() {
  local url="$1" body="$2" label="$3"
  local code
  code=$(curl -sS -u "$AUTH" -o /dev/null -w "%{http_code}" \
    -X POST -H 'Content-Type: application/json' \
    "$url" -d "$body") || code=000
  case "$code" in
    200) printf "  created %s\n" "$label" ;;
    400) printf "  %s already exists (ok)\n" "$label" ;;
    *)   printf "error: %s failed (HTTP %s)\n" "$label" "$code" >&2; exit 1 ;;
  esac
}

# semp_patch <url> <json-payload> <log-label>
semp_patch() {
  local url="$1" body="$2" label="$3"
  local code
  code=$(curl -sS -u "$AUTH" -o /dev/null -w "%{http_code}" \
    -X PATCH -H 'Content-Type: application/json' \
    "$url" -d "$body") || code=000
  if [[ "$code" != "200" ]]; then
    printf "error: %s patch failed (HTTP %s)\n" "$label" "$code" >&2
    exit 1
  fi
  printf "  patched %s\n" "$label"
}

# ----- VPN -----
semp_post "${SEMP}/msgVpns" "$(cat <<EOF
{
  "msgVpnName": "${VPN}",
  "enabled": true,
  "authenticationBasicEnabled": true,
  "authenticationBasicType": "internal",
  "maxMsgSpoolUsage": 1500,
  "maxConnectionCount": 100
}
EOF
)" "msgVpn ${VPN}"

# ----- ACL profile -----
semp_post "${SEMP}/msgVpns/${VPN}/aclProfiles" '{
  "aclProfileName": "vc-acl",
  "clientConnectDefaultAction": "allow",
  "publishTopicDefaultAction": "allow",
  "subscribeTopicDefaultAction": "allow"
}' "aclProfile vc-acl"

# ----- client profile -----
semp_post "${SEMP}/msgVpns/${VPN}/clientProfiles" '{
  "clientProfileName": "vc-client",
  "allowGuaranteedEndpointCreateEnabled": true,
  "allowGuaranteedMsgSendEnabled": true,
  "allowGuaranteedMsgReceiveEnabled": true,
  "allowTransactedSessionsEnabled": true
}' "clientProfile vc-client"

# ----- client username -----
semp_post "${SEMP}/msgVpns/${VPN}/clientUsernames" "$(cat <<EOF
{
  "clientUsername": "${USER}",
  "password": "${PASS}",
  "enabled": true,
  "aclProfileName": "vc-acl",
  "clientProfileName": "vc-client"
}
EOF
)" "clientUsername ${USER}"

# Reset password/profiles in case the user pre-existed with stale values.
semp_patch "${SEMP}/msgVpns/${VPN}/clientUsernames/${USER}" "$(cat <<EOF
{
  "password": "${PASS}",
  "enabled": true,
  "aclProfileName": "vc-acl",
  "clientProfileName": "vc-client"
}
EOF
)" "clientUsername ${USER}"

printf "Solace bootstrap complete: VPN=%s user=%s\n" "$VPN" "$USER"
