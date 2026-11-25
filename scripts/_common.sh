#!/usr/bin/env bash
# Shared helpers for agent-flow-azure lifecycle scripts.
# shellcheck disable=SC2034
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
GENERATED_ENV="${ROOT_DIR}/.env.generated"

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
info() { log "INFO  $*"; }
warn() { log "WARN  $*"; }
err()  { log "ERROR $*"; }
die()  { err "$*"; exit 1; }

require_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "Required command not found: $c"
  done
}

load_env() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    die "Missing ${ENV_FILE}. Copy .env.example to .env and fill required values."
  fi
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
  if [[ -f "${GENERATED_ENV}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${GENERATED_ENV}"
    set +a
  fi
}

require_env() {
  local missing=()
  local k
  for k in "$@"; do
    if [[ -z "${!k:-}" ]]; then
      missing+=("$k")
    fi
  done
  if ((${#missing[@]})); then
    die "Missing required env vars: ${missing[*]}"
  fi
}

check_azure_login() {
  require_cmd az
  az account show >/dev/null 2>&1 || die "Not logged in. Run: az login"
  if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
    az account set --subscription "${AZURE_SUBSCRIPTION_ID}" >/dev/null
  fi
  AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
  AZURE_TENANT_ID="${AZURE_TENANT_ID:-$(az account show --query tenantId -o tsv)}"
  export AZURE_SUBSCRIPTION_ID AZURE_TENANT_ID
}

resource_name() {
  # <project>-<env>-<service>-<region_short>-<suffix>
  local service="$1"
  echo "${PROJECT_SHORT}-${ENVIRONMENT}-${service}-${AZURE_LOCATION_SHORT}-${NAME_SUFFIX:-01}"
}

write_generated_env() {
  local key="$1" value="$2"
  # Quote values with shell metacharacters (Logic App SAS URLs contain &).
  local stored="$value"
  if [[ "$stored" == *[\'\"\\\$\`\&\|\<\>\ \(\)]* ]]; then
    stored="${stored//\\/\\\\}"
    stored="${stored//\"/\\\"}"
    stored="\"${stored}\""
  fi
  touch "${GENERATED_ENV}"
  if grep -q "^${key}=" "${GENERATED_ENV}" 2>/dev/null; then
    local tmp
    tmp="$(mktemp)"
    awk -v k="${key}" -v v="${stored}" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' "${GENERATED_ENV}" >"${tmp}"
    mv "${tmp}" "${GENERATED_ENV}"
  else
    printf '%s=%s\n' "${key}" "${stored}" >>"${GENERATED_ENV}"
  fi
}

assert_no_secret_in_env_example() {
  # Guardrail: committed templates must not contain real secrets.
  if grep -Eiq '(sk-|ghp_|xox[baprs]-|AccountKey=)' "${ROOT_DIR}/.env.example" 2>/dev/null; then
    die ".env.example appears to contain a secret-like value"
  fi
}

dry_run_note() {
  if [[ "${DRY_RUN:-0}" == "1" ]] || [[ "${1:-}" == "--dry-run" ]]; then
    export DRY_RUN=1
    info "DRY_RUN enabled — commands will be printed, not executed"
  fi
}

run() {
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    info "DRY: $*"
    return 0
  fi
  "$@"
}

common_prereqs() {
  require_cmd az terraform helm python3 node npm
  assert_no_secret_in_env_example
}
