# Setup prerequisites — agent-flow-azure (real Microsoft Foundry)

Fill `.env` from this checklist **before** `./scripts/provision.sh` and `./scripts/install.sh`.
Secrets go in `.env` locally and are seeded into **Key Vault** by `install.sh` (never committed).

Generated endpoints (Foundry project URL, APIM, etc.) land in `.env.generated` after provision — do not invent those by hand.

---

## 1. What you will deploy (real Foundry — no simulation)

| Layer | Resource |
| --- | --- |
| Foundry account | `Microsoft.CognitiveServices/accounts` kind `AIServices`, `project_management_enabled=true` |
| Foundry project | `.../accounts/.../projects/<name>` |
| Models | `gpt-4.1-mini`, `gpt-4.1`, `gpt-4o` (pool) + **`model-router`** (decider + failover) + `text-embedding-3-small` (memory) |
| Agents | Prompt agents on Copilot harness via `AIProjectClient.agents.create_version` |
| Skills | Foundry skills (`client.beta.skills.create_from_files`) from `agents/skills/*/SKILL.md` |
| Toolbox | Foundry toolbox with MCP + OpenAPI tools + skill refs + tool search |
| MCP | GitHub, Microsoft Learn, optional ServiceNow, audit-store (Functions behind APIM) |
| Registry | Azure API Center entries + local `catalog.generated.json` snapshot |
| Gateway | Azure API Management → Foundry **project** Responses/agent endpoint (MI) |
| Guardrails | Content Safety account + Prompt Shields API + deployment RAI policy |
| Agent tracing | Foundry project connection to Application Insights (`afa-*-ai-*`) — required for portal Traces / “tracer attached” |

---

## 2. Required env keys (you supply)

Copy into `.env` (see also `.env.example`).

### Azure identity & naming

| Key | Required | How to obtain |
| --- | --- | --- |
| `AZURE_SUBSCRIPTION_ID` | **Yes** | `az account show --query id -o tsv` |
| `AZURE_TENANT_ID` | **Yes** | `az account show --query tenantId -o tsv` |
| `AZURE_LOCATION` | **Yes** | Region for Foundry/APIM (default `eastus2`). Prefer a region with model-router + gpt-4.1. |
| `AZURE_LOCATION_SHORT` | **Yes** | Short code used in names (default `eus2`) |
| `AZURE_COMPUTE_LOCATION` | Recommended | Functions Y1 region if eastus2 quota is 0 (default `westus2`) |
| `PROJECT_NAME` | Yes | e.g. `agent-flow-azure` |
| `PROJECT_SHORT` | Yes | e.g. `afa` (used in resource names) |
| `ENVIRONMENT` | Yes | `dev` / `test` / `prod` |
| `NAME_SUFFIX` | Yes | e.g. `01` |
| `RESOURCE_GROUP_NAME` | Yes | e.g. `rg-agent-flow-azure-dev` |

**Access needed:** Contributor (or Owner) on the subscription/RG, plus rights to create Entra app registrations (Application Developer / Cloud Application Administrator is typical).

```bash
az login
az account set --subscription "<AZURE_SUBSCRIPTION_ID>"
az account show -o table
```

### Foundry models (router + fallback pool)

| Key | Required | Purpose / how to set |
| --- | --- | --- |
| `FOUNDRY_MODEL_NAME` | **Yes** | Primary chat deployment name agents use when not using router. Default: `gpt-4.1-mini` |
| `FOUNDRY_MODEL_VERSION` | **Yes** | Version of primary model. Default: `2025-04-14` |
| `FOUNDRY_MODEL_DEPLOYMENT` | **Yes** | Runtime deployment agents call. Prefer **`model-router`** so Foundry picks + fails over. |
| `FOUNDRY_ROUTER_VERSION` | Recommended | Model-router version. Default: `2025-11-18` |
| `FOUNDRY_FALLBACK_MODELS` | Recommended | Comma list `name:version` for the router subset (failover pool). Default: `gpt-4.1-mini:2025-04-14,gpt-4.1:2025-04-14,gpt-4o:2024-11-20` |
| `FOUNDRY_ROUTER_MODE` | Optional | `balanced` (default) \| `cost` \| `quality` — router selection bias |
| `FOUNDRY_EMBEDDING_DEPLOYMENT` | **Yes** (memory) | Embedding deployment for memory store. Default: `text-embedding-3-small` |
| `FOUNDRY_EMBEDDING_VERSION` | Recommended | Default: `1` |
| `FOUNDRY_PROJECT_NAME` | Recommended | Project name under the account. Default: `afa-agents` |
| `MEMORY_STORE_NAME` | Recommended | Default: `afa-agent-memory` |
| `FOUNDRY_HARNESS_TYPE` | Optional | Copilot harness label (`microsoft_copilot_preview`) |
| `ENABLE_CONTENT_SAFETY` | Recommended | `true` |
| `ENABLE_PROMPT_SHIELDS` | Recommended | `true` |

List models available on your account (after the Foundry resource exists):

```bash
az cognitiveservices account list-models \
  -g "$RESOURCE_GROUP_NAME" -n "<foundry-account-name>" -o table
```

### MCP / tool secrets (Key Vault–seeded)

| Key | Required | How to obtain |
| --- | --- | --- |
| `GITHUB_MCP_PAT` | **Yes** for GitHub MCP | GitHub → Settings → Developer settings → **Fine-grained** or classic PAT with `repo` (and `read:org` if needed). Seeded to Key Vault and stored in a Foundry **project connection** (`github-mcp`) that points at the **Foundry catalog GitHub Official MCP** (`https://api.githubcopilot.com/mcp/`). Agents use `project_connection_id` — the PAT is never embedded in agent YAML. **Rotate any PAT that was pasted into chat.** |
| `NVD_API_KEY` | Recommended for CVE tool | Free key: https://nvd.nist.gov/developers/request-an-api-key — improves NVD rate limits (OSV still used as primary). |

#### ServiceNow OSS MCP (Azure-hosted)

ServiceNow has no built-in Foundry MCP. We host a FastMCP server from `tools/servicenow-mcp` on Azure Container Apps (streamable HTTP on port 8000). Foundry agents connect via a project RemoteTool connection to `https://<aca-fqdn>/mcp`.

| Key | Required for SN | How to obtain |
| --- | --- | --- |
| `ENABLE_SERVICENOW_MCP` | Yes to provision host | Set `true` in `.env` before `provision.sh` |
| `SERVICENOW_INSTANCE_URL` | **Yes** | e.g. `https://dev12345.service-now.com` (quote in `.env`) |
| `SERVICENOW_USERNAME` | **Yes** | Integration user with Table API rights |
| `SERVICENOW_PASSWORD` | **Yes** | **Always single-quote** in `.env` if it has `&`, `!`, `#`, `*`, etc. |
| `SERVICENOW_MCP_URL` | After deploy | Written by `./scripts/deploy_servicenow_mcp.sh` → `https://<aca-fqdn>/mcp` |

Steps:
1. Put instance URL + username + password in `.env` (`ENABLE_SERVICENOW_MCP=true`)
2. `./scripts/provision.sh` (creates ACR + Container App shell)
3. `./scripts/install.sh` (seeds Key Vault)
4. `./scripts/deploy_servicenow_mcp.sh` (clones echelon repo → ACR build → updates ACA)
5. `python3 agents/registry/register_catalog.py` (Foundry connection + agents)

**Keycloak:** keep local; use `FOUNDRY_DIRECT_MI=1` so the backend calls the Foundry project SDK (no public Keycloak URL required).

Microsoft Learn MCP (`https://learn.microsoft.com/api/mcp`) needs **no secret** (Foundry connection `microsoft-learn-mcp` with auth `None`).

Audit-store MCP is your Functions host — authenticated by gateway JWT / MI, not a third-party PAT.

**Keycloak:** leave local for now; set `FOUNDRY_DIRECT_MI=1` so the backend calls the Foundry project SDK until `KEYCLOAK_PUBLIC_URL` is a URL Azure APIM can reach.

### Keycloak (local IdP for the sample app + APIM JWT)

| Key | Required | How to obtain |
| --- | --- | --- |
| `KEYCLOAK_URL` | **Yes** (local) | Default `http://localhost:5750` — started by `./scripts/start.sh` / Docker |
| `KEYCLOAK_PUBLIC_URL` | **Yes for APIM path** | URL **Azure APIM can reach** for OpenID config/JWKS. Localhost is not reachable from Azure — use a tunnel (ngrok/cloudflare) or a hosted Keycloak. |
| `KEYCLOAK_REALM` | Yes | Default `agent-flow` |
| `KEYCLOAK_CLIENT_ID` | Yes | Default `afa-web` |
| `KEYCLOAK_API_AUDIENCE` | Yes | Default `afa-api` |
| `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` | Local only | Bootstrap admin for the container |

### Entra app registration (usually filled by Terraform)

| Key | Required | Notes |
| --- | --- | --- |
| `ENTRA_REDIRECT_URI` | Yes | Default `http://localhost:5748/` |
| `ENTRA_API_AUDIENCE` | Yes | Default `api://agent-flow-azure` |
| `ENTRA_WEB_CLIENT_ID` | From provision | Written to `.env.generated` |
| `ENTRA_API_CLIENT_ID` | From provision | Written to `.env.generated` |

### Runtime toggles

| Key | Required | Notes |
| --- | --- | --- |
| `USE_WORKLOAD_IDENTITY` | Yes | `true` |
| `USE_COSMOS` | Recommended | `1` for durable runs/learnings |
| `WEBAPP_FRONTEND_PORT` / `WEBAPP_BACKEND_PORT` | Local | `5748` / `5749` |
| `FOUNDRY_DIRECT_MI` | Deprecated | **Do not use for production.** Real path is APIM → Foundry project. Leave unset or `0`. |

---

## 3. Generated keys (after `./scripts/provision.sh`)

These appear in `.env.generated` — do not invent them:

| Key | Meaning |
| --- | --- |
| `FOUNDRY_ENDPOINT` | AI Services account endpoint |
| `FOUNDRY_PROJECT_ENDPOINT` | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `FOUNDRY_PROJECT_ID` | ARM id of the project |
| `FOUNDRY_MODEL_DEPLOYMENT` | Usually `model-router` |
| `APIM_GATEWAY_URL` | AI gateway base URL |
| `APIM_NAME` | APIM service name |
| `API_CENTER_NAME` / `API_CENTER_ID` | Registry |
| `KEY_VAULT_URI` / `KEY_VAULT_NAME` | Secrets |
| `CONTENT_SAFETY_ENDPOINT` | Guardrails |
| `COSMOS_ENDPOINT` | Run/learning store |
| `STORAGE_ACCOUNT_NAME` | Skills blobs (backup) + artifacts |
| `FUNCTION_APP_NAME` / `MCP_HOST` | Tools + audit MCP host |
| `LOGIC_APP_NAME` | Orchestration |
| `AZURE_CLIENT_ID` | Workload UAMI client id |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry |
| `MEMORY_STORE_NAME` | Confirmed store name |

---

## 4. Process overview

```text
1. Fill .env (section 2) — especially subscription, tenant, GITHUB_MCP_PAT
2. az login && az account set
3. ./scripts/provision.sh     → creates Foundry account+project, models, APIM, Functions, …
4. Review .env.generated      → FOUNDRY_PROJECT_ENDPOINT must be a /api/projects/… URL
5. ./scripts/install.sh       → Key Vault secrets, Functions publish, register_catalog (agents/skills/MCP/toolbox)
6. ./scripts/start.sh         → Keycloak + local web app
7. Open http://localhost:5748 — chat/flow hits APIM → Foundry agents (tools/MCP inside Foundry)
```

### Verify Foundry project exists

```bash
az rest --method get \
  --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP_NAME}/providers/Microsoft.CognitiveServices/accounts/<foundry-name>/projects?api-version=2025-06-01"
```

Portal: [https://ai.azure.com](https://ai.azure.com) → your account → **Projects** → agents / tools / skills.

### Re-register agents after YAML/skill changes

```bash
python3 agents/registry/register_catalog.py
```

(`install.sh` fails if registration fails — no silent stubs.)

---

## 5. Minimal `.env` starter (secrets blank — you fill)

```bash
PROJECT_NAME=agent-flow-azure
PROJECT_SHORT=afa
ENVIRONMENT=dev
NAME_SUFFIX=01
RESOURCE_GROUP_NAME=rg-agent-flow-azure-dev

AZURE_SUBSCRIPTION_ID=   # REQUIRED
AZURE_TENANT_ID=         # REQUIRED
AZURE_LOCATION=eastus2
AZURE_LOCATION_SHORT=eus2
AZURE_COMPUTE_LOCATION=westus2

FOUNDRY_PROJECT_NAME=afa-agents
FOUNDRY_MODEL_NAME=gpt-4.1-mini
FOUNDRY_MODEL_VERSION=2025-04-14
FOUNDRY_MODEL_DEPLOYMENT=model-router
FOUNDRY_ROUTER_VERSION=2025-11-18
FOUNDRY_ROUTER_MODE=balanced
FOUNDRY_FALLBACK_MODELS=gpt-4.1-mini:2025-04-14,gpt-4.1:2025-04-14,gpt-4o:2024-11-20
FOUNDRY_EMBEDDING_DEPLOYMENT=text-embedding-3-small
FOUNDRY_EMBEDDING_VERSION=1
MEMORY_STORE_NAME=afa-agent-memory
ENABLE_CONTENT_SAFETY=true
ENABLE_PROMPT_SHIELDS=true

GITHUB_MCP_PAT=          # REQUIRED for GitHub MCP
NVD_API_KEY=             # recommended

# Optional ServiceNow MCP
# SERVICENOW_INSTANCE_URL=
# SERVICENOW_USERNAME=
# SERVICENOW_PASSWORD=
# SERVICENOW_MCP_URL=

KEYCLOAK_URL=http://localhost:5750
KEYCLOAK_PUBLIC_URL=     # REQUIRED for APIM JWT when Keycloak is not public
KEYCLOAK_REALM=agent-flow
KEYCLOAK_CLIENT_ID=afa-web
KEYCLOAK_API_AUDIENCE=afa-api
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=admin

ENTRA_REDIRECT_URI=http://localhost:5748/
ENTRA_API_AUDIENCE=api://agent-flow-azure
USE_WORKLOAD_IDENTITY=true
USE_COSMOS=1
FOUNDRY_DIRECT_MI=0
```

---

## 6. Security notes

- Never commit `.env` or PATs. Only `.env.example` / this doc.
- MCP tokens are stored in Key Vault (`github-mcp-pat`, `servicenow-*`, `nvd-api-key`) and resolved with Managed Identity.
- Prefer fine-grained GitHub tokens and ServiceNow OAuth over long-lived passwords.
- For APIM → Foundry, use Managed Identity (`Cognitive Services OpenAI User` / Azure AI User on the project) — no API keys on the account (`local_auth_enabled=false`).
