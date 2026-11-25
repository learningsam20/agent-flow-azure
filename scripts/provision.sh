#!/usr/bin/env bash
# Provision Azure resources via Terraform and capture outputs (workload identity, no keys).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

dry_run_note "${1:-}"
load_env
require_env PROJECT_SHORT ENVIRONMENT AZURE_LOCATION AZURE_LOCATION_SHORT
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  info "DRY_RUN: would terraform init/apply with project=${PROJECT_SHORT} env=${ENVIRONMENT} loc=${AZURE_LOCATION}"
  info "DRY_RUN: would write outputs to ${GENERATED_ENV}"
  exit 0
fi
common_prereqs
require_cmd jq
check_azure_login

TF_DIR="${ROOT_DIR}/infra/terraform"
info "Terraform init/apply in ${TF_DIR}"

# Sensitive ServiceNow values via TF_VAR_ (avoids shell metachar breakage in -var=)
export TF_VAR_servicenow_instance_url="${SERVICENOW_INSTANCE_URL:-}"
export TF_VAR_servicenow_username="${SERVICENOW_USERNAME:-}"
export TF_VAR_servicenow_password="${SERVICENOW_PASSWORD:-}"

run terraform -chdir="${TF_DIR}" init -upgrade
run terraform -chdir="${TF_DIR}" apply -auto-approve \
  -var="subscription_id=${AZURE_SUBSCRIPTION_ID}" \
  -var="tenant_id=${AZURE_TENANT_ID}" \
  -var="environment=${ENVIRONMENT}" \
  -var="location=${AZURE_LOCATION}" \
  -var="location_short=${AZURE_LOCATION_SHORT}" \
  -var="compute_location=${AZURE_COMPUTE_LOCATION:-westus2}" \
  -var="project_short=${PROJECT_SHORT}" \
  -var="name_suffix=${NAME_SUFFIX:-01}" \
  -var="foundry_project_name=${FOUNDRY_PROJECT_NAME:-afa-agents}" \
  -var="foundry_model_name=${FOUNDRY_MODEL_NAME}" \
  -var="foundry_model_version=${FOUNDRY_MODEL_VERSION}" \
  -var="foundry_router_version=${FOUNDRY_ROUTER_VERSION:-2025-11-18}" \
  -var="foundry_router_mode=${FOUNDRY_ROUTER_MODE:-balanced}" \
  -var="foundry_agent_deployment=${FOUNDRY_MODEL_DEPLOYMENT:-gpt-4.1-mini}" \
  -var="enable_model_router=${ENABLE_MODEL_ROUTER:-false}" \
  -var="foundry_embedding_deployment=${FOUNDRY_EMBEDDING_DEPLOYMENT:-text-embedding-3-small}" \
  -var="foundry_embedding_version=${FOUNDRY_EMBEDDING_VERSION:-1}" \
  -var="enable_content_safety=${ENABLE_CONTENT_SAFETY}" \
  -var="enable_prompt_shields=${ENABLE_PROMPT_SHIELDS}" \
  -var="enable_servicenow_mcp=${ENABLE_SERVICENOW_MCP:-false}" \
  -var="web_redirect_uri=${ENTRA_REDIRECT_URI:-http://localhost:5748/}" \
  -var="keycloak_url=${KEYCLOAK_PUBLIC_URL:-${KEYCLOAK_URL:-http://localhost:5750}}" \
  -var="keycloak_realm=${KEYCLOAK_REALM:-agent-flow}" \
  -var="keycloak_api_audience=${KEYCLOAK_API_AUDIENCE:-afa-api}"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  info "Skipping output capture (dry-run)"
  exit 0
fi

info "Writing Terraform outputs to ${GENERATED_ENV}"
: > "${GENERATED_ENV}"
while IFS=$'\t' read -r key value; do
  [[ -z "${key}" ]] && continue
  # Upper-case keys for .env consumption
  env_key="$(echo "${key}" | tr '[:lower:]' '[:upper:]')"
  write_generated_env "${env_key}" "${value}"
done < <(terraform -chdir="${TF_DIR}" output -json | jq -r 'to_entries[] | "\(.key)\t\(.value.value|tostring)"')

info "Provision complete. Review ${GENERATED_ENV} (endpoints + managed identity client IDs only — no keys)."
