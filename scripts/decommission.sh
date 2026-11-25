#!/usr/bin/env bash
# Tear down Helm releases and Terraform-managed resources. Purges soft-deleted KV/APIM.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

YES=0
DRY_ARG=""
for arg in "$@"; do
  case "${arg}" in
    --yes|-y) YES=1 ;;
    --dry-run) DRY_ARG="--dry-run" ;;
  esac
done
dry_run_note "${DRY_ARG}"
load_env
common_prereqs
check_azure_login

if [[ "${YES}" != "1" ]]; then
  read -r -p "Destroy ALL agent-flow-azure resources in ${ENVIRONMENT}? Type 'destroy' to confirm: " confirm
  [[ "${confirm}" == "destroy" ]] || die "Aborted"
fi

info "Uninstalling Helm release afa-mcp"
run helm uninstall afa-mcp -n afa-mcp || warn "Helm uninstall skipped (not installed)"

TF_DIR="${ROOT_DIR}/infra/terraform"
info "Terraform destroy"
run terraform -chdir="${TF_DIR}" destroy -auto-approve \
  -var="subscription_id=${AZURE_SUBSCRIPTION_ID}" \
  -var="tenant_id=${AZURE_TENANT_ID}" \
  -var="environment=${ENVIRONMENT}" \
  -var="location=${AZURE_LOCATION}" \
  -var="location_short=${AZURE_LOCATION_SHORT}" \
  -var="project_short=${PROJECT_SHORT}" \
  -var="name_suffix=${NAME_SUFFIX:-01}" \
  -var="foundry_model_name=${FOUNDRY_MODEL_NAME}" \
  -var="foundry_model_version=${FOUNDRY_MODEL_VERSION}"

KV_NAME="${KEY_VAULT_NAME:-$(resource_name kv)}"
APIM_NAME="${APIM_NAME:-$(resource_name apim)}"

info "Purging soft-deleted Key Vault ${KV_NAME} (if present)"
run az keyvault purge --name "${KV_NAME}" --location "${AZURE_LOCATION}" 2>/dev/null || warn "KV purge skipped"

info "Purging soft-deleted APIM ${APIM_NAME} (if present)"
run az apim deletedservice purge --service-name "${APIM_NAME}" --location "${AZURE_LOCATION}" 2>/dev/null \
  || warn "APIM purge skipped"

rm -f "${GENERATED_ENV}"
info "Decommission complete."
