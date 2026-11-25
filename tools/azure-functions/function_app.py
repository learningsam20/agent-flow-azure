"""Azure Functions (Python v2) tools + MCP-style endpoints.

Auth: DefaultAzureCredential with AZURE_CLIENT_ID (UAMI). Secrets from Key Vault only.
No connection-string keys for Cosmos / app data plane.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import azure.functions as func
import httpx
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.keyvault.secrets import SecretClient

# Auth edge is APIM (Entra JWT). Functions trust gateway headers; no function keys.
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_credential: DefaultAzureCredential | None = None


def credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential(
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None
        )
    return _credential


def kv_secret(name: str, default: str = "") -> str:
    uri = os.getenv("KEY_VAULT_URI")
    if not uri:
        return default
    try:
        client = SecretClient(vault_url=uri, credential=credential())
        return client.get_secret(name).value or default
    except Exception as exc:  # noqa: BLE001
        logging.warning("Key Vault secret %s unavailable: %s", name, exc)
        return default


def roles_from_headers(req: func.HttpRequest) -> list[str]:
    raw = req.headers.get("x-user-roles") or req.headers.get("X-User-Roles") or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


def require_roles(req: func.HttpRequest, allowed: set[str]) -> func.HttpResponse | None:
    roles = set(roles_from_headers(req))
    if "Agent.Admin" in roles or "App.Admin" in roles:
        return None
    if roles & allowed:
        return None
    return func.HttpResponse(
        json.dumps(
            {
                "error": "Policy Violation: Role lacks permission",
                "required_any": sorted(allowed),
                "roles": sorted(roles),
            }
        ),
        status_code=403,
        mimetype="application/json",
    )


@app.route(route="validate-request", methods=["POST"])
def validate_request(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"valid": False, "error": "invalid json"}), status_code=400)

    roles = roles_from_headers(req)
    oid = req.headers.get("x-user-id") or req.headers.get("X-User-Id") or ""
    schema_ok = isinstance(body, dict) and "message" in body
    return func.HttpResponse(
        json.dumps(
            {
                "valid": schema_ok,
                "user_oid": oid,
                "roles": roles,
                "claims_present": bool(roles),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        mimetype="application/json",
    )


@app.route(route="cve-lookup", methods=["POST"])
def cve_lookup(req: func.HttpRequest) -> func.HttpResponse:
    denied = require_roles(req, {"Agent.SecurityAuditor", "Agent.Admin"})
    if denied:
        return denied
    body = req.get_json()
    package = (body or {}).get("package", "")
    version = (body or {}).get("version", "")
    api_key = kv_secret("nvd-api-key")

    findings: list[dict[str, Any]] = []
    # Prefer OSV (no key). NVD key from Key Vault if present.
    try:
        with httpx.Client(timeout=20.0) as client:
            osv = client.post(
                "https://api.osv.dev/v1/query",
                json={"package": {"name": package, "ecosystem": (body or {}).get("ecosystem", "PyPI")}, "version": version}
                if version
                else {"package": {"name": package, "ecosystem": (body or {}).get("ecosystem", "PyPI")}},
            )
            if osv.status_code == 200:
                for vuln in osv.json().get("vulns", [])[:10]:
                    findings.append(
                        {
                            "id": vuln.get("id"),
                            "summary": vuln.get("summary"),
                            "severity": (vuln.get("database_specific") or {}).get("severity"),
                        }
                    )
            if api_key and not findings:
                logging.info("OSV empty; NVD key present in Key Vault (not logged)")
    except Exception as exc:  # noqa: BLE001
        logging.exception("CVE lookup failed: %s", exc)
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=502)

    return func.HttpResponse(
        json.dumps({"package": package, "version": version, "findings": findings}),
        mimetype="application/json",
    )


@app.route(route="cost-estimator", methods=["POST"])
def cost_estimator(req: func.HttpRequest) -> func.HttpResponse:
    denied = require_roles(req, {"Agent.Admin"})
    if denied:
        return denied
    body = req.get_json() or {}
    resources = body.get("resources", [])
    # Simple heuristic estimator for Free/Consumption experiment stack
    catalog = {
        "apim_consumption": 0.0,
        "functions_y1": 0.0,
        "cosmos_serverless": 5.0,
        "logic_ws1": 180.0,
        "aca_consumption": 0.0,
        "key_vault": 0.03,
        "storage_lrs": 1.0,
        "foundry_tokens_est": float(body.get("token_usd", 15.0)),
    }
    total = 0.0
    lines = []
    for r in resources or list(catalog.keys()):
        cost = catalog.get(r, 10.0)
        total += cost
        lines.append({"resource": r, "est_monthly_usd": cost})
    return func.HttpResponse(
        json.dumps(
            {
                "currency": "USD",
                "est_monthly_total": round(total, 2),
                "lines": lines,
                "notes": "Prefer Free/Consumption; stop Logic Apps via stop.sh when idle.",
            }
        ),
        mimetype="application/json",
    )


@app.route(route="repo-metrics", methods=["POST"])
def repo_metrics(req: func.HttpRequest) -> func.HttpResponse:
    denied = require_roles(req, {"Agent.Developer", "Agent.Admin"})
    if denied:
        return denied
    body = req.get_json() or {}
    repo = body.get("repo", "unknown/unknown")
    return func.HttpResponse(
        json.dumps(
            {
                "repo": repo,
                "open_prs": body.get("open_prs", 0),
                "open_issues": body.get("open_issues", 0),
                "last_commit_days": body.get("last_commit_days", None),
                "source": "fn-afa-repo-metrics",
            }
        ),
        mimetype="application/json",
    )


# Minimal MCP-compatible tool surface hosted on Functions (audit helpers)
@app.route(route="mcp/list_runs", methods=["GET", "POST"])
def mcp_list_runs(req: func.HttpRequest) -> func.HttpResponse:
    from azure.cosmos import CosmosClient

    endpoint = os.environ["COSMOS_ENDPOINT"]
    # AAD token for Cosmos — local auth disabled in Terraform
    token_provider = get_bearer_token_provider(credential(), "https://cosmos.azure.com/.default")
    client = CosmosClient(endpoint, credential=token_provider)
    db = client.get_database_client("workflow")
    container = db.get_container_client("runs")
    oid = req.headers.get("x-user-id") or req.params.get("userOid") or ""
    query = "SELECT * FROM c WHERE c.userOid = @oid ORDER BY c._ts DESC"
    items = list(
        container.query_items(
            query=query,
            parameters=[{"name": "@oid", "value": oid}],
            enable_cross_partition_query=True,
        )
    )[:50]
    return func.HttpResponse(json.dumps({"runs": items}), mimetype="application/json")


@app.function_name(name="health")
@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok", "auth": "workload_identity"}), mimetype="application/json")
