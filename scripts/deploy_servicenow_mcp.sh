#!/usr/bin/env bash
# Build/push local tools/servicenow-mcp (FastMCP streamable HTTP) to ACR and wire SERVICENOW_MCP_URL.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"
load_env
require_env RESOURCE_GROUP_NAME
check_azure_login

ACR_NAME="${ACR_NAME:-$(az acr list -g "${RESOURCE_GROUP_NAME}" --query "[?contains(name,'acrmcp')].name|[0]" -o tsv)}"
APP_NAME="${SERVICENOW_MCP_APP_NAME:-${PROJECT_SHORT}-${ENVIRONMENT}-snmcp-${AZURE_LOCATION_SHORT}-${NAME_SUFFIX:-01}}"
SRC_DIR="${SERVICENOW_MCP_SRC:-${ROOT_DIR}/tools/servicenow-mcp}"

if [[ -z "${ACR_NAME}" || "${ACR_NAME}" == "null" ]]; then
  die "No ACR for MCP found. Re-run provision with ENABLE_SERVICENOW_MCP=true (see setup.md)."
fi
if [[ ! -f "${SRC_DIR}/Dockerfile" ]]; then
  die "ServiceNow MCP source not found at ${SRC_DIR}"
fi

require_env SERVICENOW_INSTANCE_URL
require_env SERVICENOW_USERNAME
require_env SERVICENOW_PASSWORD

info "Building image from ${SRC_DIR} in ACR ${ACR_NAME}"
run az acr build -r "${ACR_NAME}" -t servicenow-mcp:latest "${SRC_DIR}"
DIGEST=$(az acr manifest list-metadata -r "${ACR_NAME}" -n servicenow-mcp --orderby time_desc --query "[0].digest" -o tsv)
if [[ -z "${DIGEST}" || "${DIGEST}" == "null" ]]; then
  die "Could not resolve servicenow-mcp image digest after build"
fi

info "Updating Container App ${APP_NAME} (port 8000 streamable HTTP /mcp @ ${DIGEST})"
LOGIN_SERVER=$(az acr show -n "${ACR_NAME}" --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n "${ACR_NAME}" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "${ACR_NAME}" --query "passwords[0].value" -o tsv)
run az containerapp registry set -g "${RESOURCE_GROUP_NAME}" -n "${APP_NAME}" \
  --server "${LOGIN_SERVER}" --username "${ACR_USER}" --password "${ACR_PASS}"

# Secrets first so env secretrefs resolve
run az containerapp secret set -g "${RESOURCE_GROUP_NAME}" -n "${APP_NAME}" --secrets \
  "servicenow-instance-url=${SERVICENOW_INSTANCE_URL}" \
  "servicenow-username=${SERVICENOW_USERNAME}" \
  "servicenow-password=${SERVICENOW_PASSWORD}"

REV_SUFFIX="mcp$(date +%H%M%S)"
run az containerapp update -g "${RESOURCE_GROUP_NAME}" -n "${APP_NAME}" \
  --image "${LOGIN_SERVER}/servicenow-mcp@${DIGEST}" \
  --revision-suffix "${REV_SUFFIX}" \
  --cpu 0.5 --memory 1.0Gi \
  --min-replicas 1 --max-replicas 3 \
  --set-env-vars \
    "SERVICENOW_INSTANCE_URL=secretref:servicenow-instance-url" \
    "SERVICENOW_USERNAME=secretref:servicenow-username" \
    "SERVICENOW_PASSWORD=secretref:servicenow-password" \
    "SERVICENOW_AUTH_TYPE=basic" \
    "PORT=8000" \
    "MCP_DNS_REBINDING_PROTECTION=false"

run az containerapp ingress update -g "${RESOURCE_GROUP_NAME}" -n "${APP_NAME}" --target-port 8000
run az containerapp revision set-mode -g "${RESOURCE_GROUP_NAME}" -n "${APP_NAME}" --mode single || true

FQDN=$(az containerapp show -g "${RESOURCE_GROUP_NAME}" -n "${APP_NAME}" --query properties.configuration.ingress.fqdn -o tsv)
MCP_URL="https://${FQDN}/mcp"
write_generated_env "SERVICENOW_MCP_URL" "${MCP_URL}"
if grep -q '^SERVICENOW_MCP_URL=' "${ENV_FILE}" 2>/dev/null; then
  tmp="$(mktemp)"
  awk -v v="'${MCP_URL}'" 'BEGIN{FS=OFS="="} $1=="SERVICENOW_MCP_URL"{$0="SERVICENOW_MCP_URL="v} {print}' "${ENV_FILE}" >"${tmp}"
  mv "${tmp}" "${ENV_FILE}"
else
  printf "\nSERVICENOW_MCP_URL='%s'\n" "${MCP_URL}" >>"${ENV_FILE}"
fi

info "Waiting for /health"
for i in $(seq 1 30); do
  if curl -fsS --max-time 5 "https://${FQDN}/health" | grep -q '"status"'; then
    info "ServiceNow MCP healthy: ${MCP_URL}"
    echo "SERVICENOW_MCP_URL=${MCP_URL}"
    exit 0
  fi
  sleep 3
done
die "ServiceNow MCP deployed but /health did not become ready. Check: az containerapp logs show -g ${RESOURCE_GROUP_NAME} -n ${APP_NAME} --tail 50"
