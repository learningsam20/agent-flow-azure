#!/usr/bin/env bash
# Start Keycloak (if needed), local API + UI. Azure compute is pay-per-use (Consumption).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

dry_run_note "${1:-}"
load_env
require_cmd python3 npm docker

PID_DIR="${ROOT_DIR}/.pids"
mkdir -p "${PID_DIR}" "${ROOT_DIR}/logs"

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:5750}"
FRONTEND_PORT="${WEBAPP_FRONTEND_PORT:-5748}"
BACKEND_PORT="${WEBAPP_BACKEND_PORT:-5749}"

info "Ensuring Keycloak on ${KEYCLOAK_URL}"
if ! curl -sf "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM:-agent-flow}" >/dev/null 2>&1; then
  run docker compose -f "${ROOT_DIR}/infra/keycloak/docker-compose.yml" up -d
  for i in $(seq 1 36); do
    if curl -sf "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM:-agent-flow}" >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
fi

info "Starting FastAPI backend on :${BACKEND_PORT}"
if [[ "${DRY_RUN:-0}" != "1" ]]; then
  (
    cd "${ROOT_DIR}/webapp/backend"
    python3 -m venv .venv 2>/dev/null || true
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install -q -r requirements.txt
    export KEYCLOAK_URL KEYCLOAK_REALM KEYCLOAK_CLIENT_ID KEYCLOAK_API_AUDIENCE
    export KEYCLOAK_ISSUER="${KEYCLOAK_ISSUER:-${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM:-agent-flow}}"
    # Azure orchestration (from .env.generated) — without TRIGGER_URL, Logic App history stays empty
    export FOUNDRY_PROJECT_ENDPOINT FOUNDRY_DIRECT_MI APIM_GATEWAY_URL \
      LOGIC_APP_NAME LOGIC_APP_TRIGGER_URL HITL_NOTIFY_URL PUBLIC_API_URL \
      CONTENT_SAFETY_ENDPOINT ENABLE_CONTENT_SAFETY ENABLE_PROMPT_SHIELDS \
      COSMOS_ENDPOINT USE_COSMOS KEY_VAULT_URI APPLICATIONINSIGHTS_CONNECTION_STRING \
      MEMORY_STORE_NAME FOUNDRY_MODEL_DEPLOYMENT AZURE_CLIENT_ID 2>/dev/null || true
    exec uvicorn main:app --host 0.0.0.0 --port "${BACKEND_PORT}" --reload
  ) >"${ROOT_DIR}/logs/backend.log" 2>&1 &
  echo $! >"${PID_DIR}/backend.pid"
fi

info "Starting React frontend on :${FRONTEND_PORT}"
if [[ "${DRY_RUN:-0}" != "1" ]]; then
  (
    cd "${ROOT_DIR}/webapp/frontend"
    if [[ ! -d node_modules ]]; then npm install; fi
    exec npm run dev -- --host --port "${FRONTEND_PORT}"
  ) >"${ROOT_DIR}/logs/frontend.log" 2>&1 &
  echo $! >"${PID_DIR}/frontend.pid"
fi

cat <<EOF

────────────────────────────────────────────────────────────
 agent-flow-azure
 Frontend:   http://localhost:${FRONTEND_PORT}
 Backend:    http://localhost:${BACKEND_PORT}
 API docs:   http://localhost:${BACKEND_PORT}/docs
 Keycloak:   ${KEYCLOAK_URL}
 Auth:       Keycloak OIDC (no demo personas)

 Bootstrap users (realm import):
   admin / ChangeMeAdmin1!       → App.Admin
   developer / ChangeMeUser1!    → App.User + Agent.Developer
   auditor / ChangeMeUser2!      → App.User + Agent.SecurityAuditor
────────────────────────────────────────────────────────────
EOF
