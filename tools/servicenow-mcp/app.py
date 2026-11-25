"""ServiceNow OSS MCP (streamable HTTP) for Azure Container Apps."""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# ACA FQDNs fail the default DNS-rebinding Host allow-list.
_allowed = [h.strip() for h in (os.getenv("MCP_ALLOWED_HOSTS") or "*").split(",") if h.strip()]
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=os.getenv("MCP_DNS_REBINDING_PROTECTION", "false").lower()
    in ("1", "true", "yes"),
    allowed_hosts=_allowed,
)

mcp = FastMCP("servicenow-oss", transport_security=_security)


def _instance() -> str:
    url = (os.getenv("SERVICENOW_INSTANCE_URL") or "").rstrip("/")
    if not url:
        raise RuntimeError("SERVICENOW_INSTANCE_URL not set")
    return url


def _auth_header() -> str:
    user = os.getenv("SERVICENOW_USERNAME") or ""
    password = os.getenv("SERVICENOW_PASSWORD") or ""
    if user and password:
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    token = os.getenv("SERVICENOW_ACCESS_TOKEN") or ""
    if token:
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"
    raise RuntimeError("Set SERVICENOW_USERNAME+PASSWORD or SERVICENOW_ACCESS_TOKEN")


async def _sn_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{_instance()}{path}",
            params=params or {},
            headers={"Authorization": _auth_header(), "Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def _sn_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_instance()}{path}",
            json=body,
            headers={
                "Authorization": _auth_header(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def servicenow_search_incidents(query: str = "", limit: int = 10) -> str:
    """Search ServiceNow incidents. Optional encoded query (e.g. active=true^priority=1)."""
    params: dict[str, Any] = {"sysparm_limit": min(max(limit, 1), 50)}
    if query:
        params["sysparm_query"] = query
    data = await _sn_get("/api/now/table/incident", params)
    rows = data.get("result") or []
    lines = [
        f"- {r.get('number')}: {r.get('short_description')} "
        f"[state={r.get('state')} priority={r.get('priority')} sys_id={r.get('sys_id')}]"
        for r in rows
    ]
    return "\n".join(lines) or "No incidents found."


@mcp.tool()
async def servicenow_get_incident(sys_id: str) -> str:
    """Get one incident by sys_id."""
    data = await _sn_get(f"/api/now/table/incident/{sys_id}")
    r = data.get("result") or {}
    return (
        f"number={r.get('number')}\nshort_description={r.get('short_description')}\n"
        f"description={r.get('description')}\nstate={r.get('state')} priority={r.get('priority')}\n"
        f"sys_id={r.get('sys_id')}"
    )


@mcp.tool()
async def servicenow_create_incident(short_description: str, description: str = "", urgency: str = "3") -> str:
    """Create an incident. urgency 1=high, 2=medium, 3=low."""
    body = {
        "short_description": short_description,
        "description": description or short_description,
        "urgency": urgency,
        "impact": urgency,
    }
    data = await _sn_post("/api/now/table/incident", body)
    r = data.get("result") or {}
    return f"Created {r.get('number')} sys_id={r.get('sys_id')}"


@mcp.tool()
async def servicenow_search_knowledge(query: str, limit: int = 5) -> str:
    """Search published knowledge articles (kb_knowledge)."""
    params = {
        "sysparm_limit": min(max(limit, 1), 20),
        "sysparm_query": f"workflow_state=published^short_descriptionLIKE{query}",
    }
    data = await _sn_get("/api/now/table/kb_knowledge", params)
    rows = data.get("result") or []
    lines = [f"- {r.get('number')}: {r.get('short_description')} (sys_id={r.get('sys_id')})" for r in rows]
    return "\n".join(lines) or "No knowledge articles found."


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> Response:
    return JSONResponse({"status": "ok", "mcp": "servicenow-oss"})


app = mcp.streamable_http_app()
