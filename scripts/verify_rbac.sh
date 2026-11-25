#!/usr/bin/env bash
# Prove gateway RBAC with real Keycloak tokens (no persona minting).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

load_env
KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:5750}"
REALM="${KEYCLOAK_REALM:-agent-flow}"
BACKEND="${BACKEND_URL:-http://localhost:5749}"
TARGET="${1:-backend}"

token_for() {
  local user="$1" pass="$2"
  curl -sf -X POST "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "client_id=afa-web" \
    -d "grant_type=password" \
    -d "username=${user}" \
    -d "password=${pass}" \
    -d "scope=openid" | jq -r '.access_token'
}

info "Minting Keycloak tokens via ROPC (local realm users)"
ADMIN_TOKEN="$(token_for admin ChangeMeAdmin1!)"
DEV_TOKEN="$(token_for developer ChangeMeUser1!)"
[[ -n "${ADMIN_TOKEN}" && "${ADMIN_TOKEN}" != "null" ]] || die "Failed to get admin token — is Keycloak up? Run ./scripts/bootstrap_users.sh"
[[ -n "${DEV_TOKEN}" && "${DEV_TOKEN}" != "null" ]] || die "Failed to get developer token"

roles_header() {
  python3 -c 'import jwt,sys; print(",".join(jwt.decode(sys.argv[1], options={"verify_signature":False}).get("roles") or []))' "$1"
}

call() {
  local token="$1" agent="$2"
  local url
  if [[ "${TARGET}" == "apim" ]]; then
    [[ -n "${APIM_GATEWAY_URL:-}" ]] || die "APIM_GATEWAY_URL required for apim target"
    url="${APIM_GATEWAY_URL}/api/agents/${agent}/chat"
  else
    url="${BACKEND}/api/agents/${agent}/chat"
  fi
  curl -s -o /tmp/afa-rbac-body.json -w '%{http_code}' -X POST "${url}" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -H "X-User-Roles: $(roles_header "${token}")" \
    -d '{"message":"rbac probe"}'
}

info "Admin → finops-analyst-agent via ${TARGET} (expect 2xx)"
code="$(call "${ADMIN_TOKEN}" "finops-analyst-agent")"
[[ "${code}" =~ ^2 ]] || die "Expected 2xx for admin/finops, got ${code}: $(cat /tmp/afa-rbac-body.json)"
info "PASS: Admin allowed for finops-analyst-agent"

info "Developer → finops-analyst-agent via ${TARGET} (expect 403)"
code="$(call "${DEV_TOKEN}" "finops-analyst-agent")"
[[ "${code}" == "403" ]] || die "Expected 403 for developer/finops, got ${code}: $(cat /tmp/afa-rbac-body.json)"
info "PASS: Developer denied for finops-analyst-agent"
info "RBAC verification complete"
