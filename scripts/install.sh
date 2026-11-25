#!/usr/bin/env bash
# Install app artifacts: Key Vault secrets (external only), Functions, agents, Logic App, Helm.
# Azure data plane auth uses Managed Identity / Workload Identity — no storage/cosmos keys.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

dry_run_note "${1:-}"
load_env
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  info "DRY_RUN: would seed Key Vault (github-mcp-pat, nvd-api-key)"
  info "DRY_RUN: would publish Functions, register_catalog.py (agents+memory), Logic App workflow"
  exit 0
fi
common_prereqs
require_cmd func jq
check_azure_login
require_env KEY_VAULT_NAME FUNCTION_APP_NAME RESOURCE_GROUP_NAME

seed_keyvault_secret() {
  local name="$1" value="$2"
  if [[ -z "${value}" ]]; then
    warn "Skipping Key Vault secret '${name}' (empty)"
    return 0
  fi
  info "Seeding Key Vault secret: ${name} (value never logged)"
  run az keyvault secret set \
    --vault-name "${KEY_VAULT_NAME}" \
    --name "${name}" \
    --value "${value}" \
    --output none
}

info "Seeding external secrets into Key Vault (MI-backed access thereafter)"
seed_keyvault_secret "github-mcp-pat" "${GITHUB_MCP_PAT:-}"
seed_keyvault_secret "nvd-api-key" "${NVD_API_KEY:-}"
seed_keyvault_secret "servicenow-instance-url" "${SERVICENOW_INSTANCE_URL:-}"
seed_keyvault_secret "servicenow-username" "${SERVICENOW_USERNAME:-}"
seed_keyvault_secret "servicenow-password" "${SERVICENOW_PASSWORD:-}"
seed_keyvault_secret "servicenow-client-secret" "${SERVICENOW_CLIENT_SECRET:-}"

if [[ -z "${FOUNDRY_PROJECT_ENDPOINT:-}" ]] || [[ "${FOUNDRY_PROJECT_ENDPOINT}" != *"/api/projects/"* ]]; then
  die "FOUNDRY_PROJECT_ENDPOINT must be a Foundry project URL (…/api/projects/<name>). Run provision.sh and see setup.md."
fi
if [[ -z "${GITHUB_MCP_PAT:-}" ]]; then
  warn "GITHUB_MCP_PAT empty — GitHub MCP registration will fail until set (setup.md)"
fi

info "Publishing Azure Functions (system-assigned / UAMI already configured by Terraform)"
pushd "${ROOT_DIR}/tools/azure-functions" >/dev/null
run func azure functionapp publish "${FUNCTION_APP_NAME}" --python
popd >/dev/null

info "Registering Foundry skills, toolbox, MCP, memory, and prompt agents (fails hard — no stubs)"
python3 -m pip install -q -r "${ROOT_DIR}/agents/registry/requirements.txt"
run python3 "${ROOT_DIR}/agents/registry/register_catalog.py"

info "Deploying Logic Apps Consumption definitions (one Azure workflow per catalog flow)"
export AFA_ROOT="${ROOT_DIR}"
AFA_ROOT="${ROOT_DIR}" python3 - <<'PY'
import json, os, subprocess, pathlib, sys

root = pathlib.Path(os.environ["AFA_ROOT"])
catalog = json.loads((root / "workflows/catalog.json").read_text())
rg = os.environ["RESOURCE_GROUP_NAME"]
sub = os.environ["AZURE_SUBSCRIPTION_ID"]
loc = os.environ.get("AZURE_LOCATION", "eastus2")
apim = os.environ.get("APIM_GATEWAY_URL", "")
triggers = {}
failed = False
for flow in catalog.get("flows") or []:
    flow_id = flow["id"]
    name = flow.get("logicAppName") or os.environ.get("LOGIC_APP_NAME")
    wf_rel = flow.get("workflowPath") or f"workflows/logic-app-standard/{flow_id}/workflow.json"
    wf_path = root / wf_rel
    if not name or not wf_path.exists():
        print(f"SKIP {flow_id}: missing logicAppName or {wf_path}", file=sys.stderr)
        continue
    show = subprocess.run(
        ["az", "logic", "workflow", "show", "-g", rg, "-n", name, "-o", "none"],
        capture_output=True, text=True,
    )
    if show.returncode != 0:
        print(f"Creating Logic App {name} for {flow_id}")
        empty_def = {
            "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
            "contentVersion": "1.0.0.0",
            "triggers": {},
            "actions": {},
            "outputs": {},
        }
        subprocess.run(
            ["az", "rest", "--method", "put",
             "--url", f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Logic/workflows/{name}?api-version=2019-05-01",
             "--body", json.dumps({
                 "location": loc,
                 "identity": {"type": "SystemAssigned"},
                 "properties": {"state": "Enabled", "definition": empty_def},
             })],
            check=False,
        )
    raw = json.loads(wf_path.read_text())
    definition = raw.get("definition", raw)
    declared = set((definition.get("parameters") or {}).keys())
    params = {}
    if "APIM_GATEWAY_URL" in declared:
        params["APIM_GATEWAY_URL"] = {"value": apim}
    if "$connections" in declared:
        params["$connections"] = {"value": {}}
    body = {
        "location": loc,
        "identity": {"type": "SystemAssigned"},
        "properties": {
            "state": "Enabled",
            "definition": definition,
            "parameters": params,
        },
    }
    url = f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Logic/workflows/{name}?api-version=2019-05-01"
    path = pathlib.Path(f"/tmp/afa-logic-{flow_id}.json")
    path.write_text(json.dumps(body))
    r = subprocess.run(["az", "rest", "--method", "put", "--url", url, "--body", f"@{path}"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAIL deploy {flow_id} → {name}: {(r.stderr or r.stdout)[-800:]}", file=sys.stderr)
        failed = True
        continue
    cb = subprocess.run(
        ["az", "rest", "--method", "post", "--url",
         f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Logic/workflows/{name}/triggers/manual/listCallbackUrl?api-version=2019-05-01",
         "--query", "value", "-o", "tsv"],
        capture_output=True, text=True,
    )
    if cb.returncode == 0 and cb.stdout.strip():
        triggers[flow_id] = cb.stdout.strip()
        print(f"OK {flow_id} → {name}")
    else:
        print(f"WARN no callback URL for {name}", file=sys.stderr)

pathlib.Path("/tmp/afa-logic-triggers.json").write_text(json.dumps(triggers))
print(f"TRIGGER_COUNT={len(triggers)}")
raise SystemExit(1 if failed and not triggers else 0)
PY
if [[ -f /tmp/afa-logic-triggers.json ]]; then
  TRIGGERS_JSON="$(cat /tmp/afa-logic-triggers.json)"
  write_generated_env "LOGIC_APP_TRIGGER_URLS" "${TRIGGERS_JSON}"
  MULTI_URL="$(python3 -c 'import json; print(json.load(open("/tmp/afa-logic-triggers.json")).get("multi-agent-flow",""))')"
  if [[ -n "${MULTI_URL}" ]]; then
    write_generated_env "LOGIC_APP_TRIGGER_URL" "${MULTI_URL}"
  fi
  info "Wrote LOGIC_APP_TRIGGER_URLS for $(python3 -c 'import json; print(len(json.load(open("/tmp/afa-logic-triggers.json"))))') flows"
else
  warn "Logic App catalog deploy produced no trigger map"
fi

info "Helm chart optional (custom MCP runs on Functions; no ACA required)"
HELM_CHART="${ROOT_DIR}/infra/helm/agent-flow-mcp"
if kubectl config current-context >/dev/null 2>&1; then
  run helm upgrade --install afa-mcp "${HELM_CHART}" \
    --namespace afa-mcp --create-namespace \
    --set identity.clientId="${AZURE_CLIENT_ID:-}" \
    --set keyVault.uri="${KEY_VAULT_URI:-}" \
    --set cosmos.endpoint="${COSMOS_ENDPOINT:-}" \
    --set storage.accountName="${STORAGE_ACCOUNT_NAME:-}" \
    --set workloadIdentity.enabled=true
else
  warn "No kubectl context — skipping Helm (MCP on Functions)"
fi

info "Install complete. Runtime credentials resolve via Managed Identity → Key Vault."
