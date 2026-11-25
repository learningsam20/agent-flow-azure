"""Invoke Azure Functions tools / MCP endpoints declared on agent YAML.

Results are attached to agent responses so flow nodes show tools used.
Auth: forward user JWT roles; Functions use workload identity for Key Vault/Cosmos.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from agent_runtime import tools_for_agent

logger = logging.getLogger("afa.tools")

OPENAPI_TOOL_ROUTES = {
    "validate-request": "/api/validate-request",
    "cve-lookup": "/api/cve-lookup",
    "cost-estimator": "/api/cost-estimator",
    "repo-metrics": "/api/repo-metrics",
}

MCP_ROUTES = {
    "mcp-afa-audit-store": "/api/mcp/list_runs",
    "github": None,  # external MCP via gateway — recorded as configured
    "microsoft-learn": None,
}


def functions_base_url() -> str | None:
    explicit = os.getenv("FUNCTION_APP_URL") or os.getenv("MCP_HOST")
    if explicit:
        return explicit.rstrip("/")
    name = os.getenv("FUNCTION_APP_NAME")
    if name:
        return f"https://{name}.azurewebsites.net"
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _guess_package(message: str) -> tuple[str, str]:
    m = re.search(r"(?:package|cve|vuln(?:erability)?)\s*[:=]?\s*([a-zA-Z0-9_.-]+)", message, re.I)
    pkg = m.group(1) if m else "requests"
    v = re.search(r"version\s*[:=]?\s*([0-9][a-zA-Z0-9_.-]*)", message, re.I)
    return pkg, (v.group(1) if v else "")


def _guess_repo(message: str) -> str:
    m = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", message)
    return m.group(1) if m else "contoso/agent-flow-azure"


def build_tool_payload(tool_name: str, message: str, user_oid: str) -> dict[str, Any]:
    if tool_name == "validate-request":
        return {"message": message, "userOid": user_oid}
    if tool_name == "cve-lookup":
        pkg, ver = _guess_package(message)
        return {"package": pkg, "version": ver or "2.31.0", "ecosystem": "PyPI"}
    if tool_name == "cost-estimator":
        return {
            "resources": [
                "apim_consumption",
                "functions_y1",
                "cosmos_serverless",
                "key_vault",
                "storage_lrs",
                "foundry_tokens_est",
            ],
            "token_usd": 12.0,
            "context": message[:500],
        }
    if tool_name == "repo-metrics":
        return {"repo": _guess_repo(message), "open_prs": 3, "open_issues": 7}
    return {"message": message}


async def _post_function(
    path: str,
    payload: dict[str, Any],
    *,
    user: Any,
    correlation_id: str,
) -> dict[str, Any]:
    base = functions_base_url()
    gateway = (os.getenv("APIM_GATEWAY_URL") or "").rstrip("/")
    # Prefer Functions host for tool routes; APIM may proxy same paths under /api/tools/*
    urls = []
    if base:
        urls.append(f"{base}{path}")
    if gateway:
        urls.append(f"{gateway}/api/tools{path.replace('/api', '', 1)}" if path.startswith("/api/") else f"{gateway}{path}")
        urls.append(f"{gateway}{path}")

    roles = list(user.roles or [])
    if getattr(user, "is_admin", lambda: False)() and "Agent.Admin" not in roles:
        roles.append("Agent.Admin")
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": user.oid,
        "X-User-Roles": ",".join(roles),
        "X-Correlation-Id": correlation_id,
    }
    if user.raw_token:
        headers["Authorization"] = f"Bearer {user.raw_token}"

    last_err: dict[str, Any] | None = None
    async with httpx.AsyncClient(timeout=45.0) as client:
        for url in urls:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code < 400:
                    try:
                        body = resp.json()
                    except Exception:
                        body = {"raw": resp.text[:2000]}
                    return {"ok": True, "url": url, "status": resp.status_code, "result": body}
                last_err = {"ok": False, "url": url, "status": resp.status_code, "error": resp.text[:800]}
                if resp.status_code in (401, 403, 404):
                    continue
            except Exception as exc:
                last_err = {"ok": False, "url": url, "error": str(exc)}
                continue
    return last_err or {"ok": False, "error": "no tool endpoint configured"}


async def invoke_tools_for_agent(
    user: Any,
    agent_id: str,
    message: str,
    *,
    correlation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Call openapi/MCP tools declared on the agent; return per-tool execution records."""
    correlation_id = correlation_id or str(uuid.uuid4())
    declared = tools_for_agent(agent_id)
    executions: list[dict[str, Any]] = []

    for tool in declared:
        ttype = (tool.get("type") or "").lower()
        name = tool.get("name") or "unknown"
        started = _now()
        record: dict[str, Any] = {
            "name": name,
            "type": ttype or "tool",
            "status": "Pending",
            "startedAt": started,
            "endedAt": None,
            "durationMs": None,
            "input": None,
            "output": None,
            "error": None,
            "correlationId": correlation_id,
        }

        if ttype == "toolbox":
            record["status"] = "Discovered"
            record["output"] = {"note": "Toolbox discovery via API Center / Foundry", "resolve_from": tool.get("resolve_from")}
            record["endedAt"] = _now()
            executions.append(record)
            continue

        if ttype == "code_interpreter":
            record["status"] = "Available"
            record["output"] = {"note": "Code Interpreter available on synthesizer harness"}
            record["endedAt"] = _now()
            executions.append(record)
            continue

        if ttype == "mcp":
            route = MCP_ROUTES.get(name)
            if route is None:
                record["status"] = "Configured"
                record["output"] = {
                    "note": f"MCP '{name}' resolved via API Center / gateway",
                    "gateway_required": tool.get("gateway_required", True),
                }
                record["endedAt"] = _now()
                executions.append(record)
                continue
            # Invoke hosted MCP helper on Functions
            base = functions_base_url()
            if not base:
                record["status"] = "Skipped"
                record["error"] = "FUNCTION_APP_URL / MCP_HOST not set"
                record["endedAt"] = _now()
                executions.append(record)
                continue
            roles = list(user.roles or [])
            if getattr(user, "is_admin", lambda: False)() and "Agent.Admin" not in roles:
                roles.append("Agent.Admin")
            headers = {
                "X-User-Id": user.oid,
                "X-User-Roles": ",".join(roles),
                "X-Correlation-Id": correlation_id,
            }
            if user.raw_token:
                headers["Authorization"] = f"Bearer {user.raw_token}"
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.get(f"{base}{route}", headers=headers, params={"userOid": user.oid})
                    record["input"] = {"route": route, "userOid": user.oid}
                    if resp.status_code < 400:
                        record["status"] = "Succeeded"
                        try:
                            record["output"] = resp.json()
                        except Exception:
                            record["output"] = {"raw": resp.text[:1000]}
                    else:
                        record["status"] = "Failed"
                        record["error"] = resp.text[:800]
            except Exception as exc:
                record["status"] = "Failed"
                record["error"] = str(exc)
            record["endedAt"] = _now()
            executions.append(record)
            continue

        if ttype == "openapi" or name in OPENAPI_TOOL_ROUTES:
            route = OPENAPI_TOOL_ROUTES.get(name)
            if not route:
                record["status"] = "Skipped"
                record["error"] = f"No route mapping for openapi tool {name}"
                record["endedAt"] = _now()
                executions.append(record)
                continue
            payload = build_tool_payload(name, message, user.oid)
            record["input"] = payload
            result = await _post_function(route, payload, user=user, correlation_id=correlation_id)
            if result.get("ok"):
                record["status"] = "Succeeded"
                record["output"] = result.get("result")
                record["endpoint"] = result.get("url")
            else:
                record["status"] = "Failed"
                record["error"] = result.get("error") or result
                record["endpoint"] = result.get("url")
            record["endedAt"] = _now()
            executions.append(record)
            continue

        record["status"] = "Skipped"
        record["error"] = f"Unsupported tool type: {ttype}"
        record["endedAt"] = _now()
        executions.append(record)

    for r in executions:
        if r.get("startedAt") and r.get("endedAt"):
            try:
                s = datetime.fromisoformat(r["startedAt"])
                e = datetime.fromisoformat(r["endedAt"])
                r["durationMs"] = int((e - s).total_seconds() * 1000)
            except Exception:
                pass

    return executions


def summarize_tools(executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact chip-friendly tool list for nodes/UI."""
    out = []
    for e in executions:
        out.append(
            {
                "name": e.get("name"),
                "type": e.get("type"),
                "status": e.get("status"),
                "durationMs": e.get("durationMs"),
                "error": e.get("error"),
            }
        )
    return out
