#!/usr/bin/env bash
# Stop local processes and scale down billable Azure compute (preserve state).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

dry_run_note "${1:-}"
load_env
PID_DIR="${ROOT_DIR}/.pids"

stop_pidfile() {
  local f="$1" name="$2"
  if [[ -f "${f}" ]]; then
    local pid
    pid="$(cat "${f}")"
    if kill -0 "${pid}" 2>/dev/null; then
      info "Stopping ${name} (pid ${pid})"
      run kill "${pid}" || true
    fi
    rm -f "${f}"
  fi
}

stop_pidfile "${PID_DIR}/frontend.pid" "frontend"
stop_pidfile "${PID_DIR}/backend.pid" "backend"

# Also clear orphaned listeners on configured ports
for port in "${WEBAPP_FRONTEND_PORT:-5748}" "${WEBAPP_BACKEND_PORT:-5749}"; do
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${pids}" ]]; then
      info "Freeing port ${port}"
      # shellcheck disable=SC2086
      run kill ${pids} || true
    fi
  fi
done

# Logic Apps Consumption: disable workflow to stop runs without deleting definition
if [[ -n "${LOGIC_APP_NAME:-}" && -n "${RESOURCE_GROUP_NAME:-}" ]]; then
  info "Disabling Consumption Logic App ${LOGIC_APP_NAME} (definition preserved)"
  check_azure_login
  run az logic workflow update --name "${LOGIC_APP_NAME}" --resource-group "${RESOURCE_GROUP_NAME}" --state Disabled \
    || warn "Logic App disable skipped"
fi

info "Stop complete — idle Azure spend minimized; resources & Cosmos checkpoints retained."
