"""Create Foundry project connections (RemoteTool) for MCP auth — secrets stay in ARM, not agent YAML."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any


def _project_arm_id() -> str:
    explicit = os.environ.get("FOUNDRY_PROJECT_ID", "").strip()
    if explicit:
        return explicit.rstrip("/")
    sub = os.environ["AZURE_SUBSCRIPTION_ID"]
    rg = os.environ["RESOURCE_GROUP_NAME"]
    account = os.environ.get("FOUNDRY_ACCOUNT_NAME") or ""
    project = os.environ.get("FOUNDRY_PROJECT_NAME", "afa-agents")
    if not account:
        ep = os.environ.get("FOUNDRY_ENDPOINT") or os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or ""
        if "://" in ep:
            host = ep.split("://", 1)[1].split("/", 1)[0]
            account = host.split(".")[0]
    if not account:
        raise RuntimeError("FOUNDRY_ACCOUNT_NAME or FOUNDRY_ENDPOINT required to build project ARM id")
    return (
        f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices"
        f"/accounts/{account}/projects/{project}"
    )


def upsert_remote_tool_connection(
    *,
    name: str,
    target: str,
    auth_type: str = "CustomKeys",
    keys: dict[str, str] | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    """Create/update a RemoteTool project connection via az rest (avoids local SSL issues)."""
    project_id = _project_arm_id()
    conn_id = f"{project_id}/connections/{name}"
    url = f"https://management.azure.com{conn_id}?api-version=2025-06-01"
    props: dict[str, Any] = {
        "authType": auth_type,
        "category": "RemoteTool",
        "target": target,
        "isSharedToAll": True,
        "metadata": metadata or {"ApiType": "MCP"},
    }
    if auth_type == "CustomKeys":
        if not keys:
            raise ValueError("CustomKeys auth requires keys")
        props["credentials"] = {"keys": keys}
    elif auth_type == "None":
        props["credentials"] = {}
    body = {"properties": props}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(body, fh)
        path = fh.name
    try:
        result = subprocess.run(
            ["az", "rest", "--method", "put", "--url", url, "--body", f"@{path}", "-o", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if result.returncode != 0:
        raise RuntimeError(f"Connection '{name}' create failed: {(result.stderr or result.stdout)[:2000]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    return payload.get("id") or conn_id


def ensure_github_mcp_connection(pat: str) -> str:
    if not pat:
        raise RuntimeError("GITHUB_MCP_PAT required for GitHub Foundry MCP connection — see setup.md")
    token = pat if pat.lower().startswith("bearer ") else f"Bearer {pat}"
    conn_id = upsert_remote_tool_connection(
        name="github-mcp",
        target="https://api.githubcopilot.com/mcp/",
        auth_type="CustomKeys",
        keys={"Authorization": token},
        metadata={
            "ApiType": "MCP",
            "connectorName": "foundrygithubmcp",
            "toolEntityId": "github-mcp-server",
        },
    )
    print("Foundry project connection ready: github-mcp -> GitHub Official MCP")
    return conn_id


def ensure_learn_mcp_connection() -> str:
    conn_id = upsert_remote_tool_connection(
        name="microsoft-learn-mcp",
        target="https://learn.microsoft.com/api/mcp",
        auth_type="None",
        metadata={"ApiType": "MCP"},
    )
    print("Foundry project connection ready: microsoft-learn-mcp")
    return conn_id


def ensure_servicenow_mcp_connection(*, mcp_url: str, username: str = "", password: str = "") -> str:
    if not mcp_url:
        raise RuntimeError("SERVICENOW_MCP_URL required (hosted MCP endpoint)")
    keys = {}
    if username and password:
        import base64

        keys["Authorization"] = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    auth = "CustomKeys" if keys else "None"
    conn_id = upsert_remote_tool_connection(
        name="servicenow-mcp",
        target=mcp_url.rstrip("/"),
        auth_type=auth,
        keys=keys or None,
        metadata={"ApiType": "MCP", "provider": "echelon-ai-labs/servicenow-mcp"},
    )
    print(f"Foundry project connection ready: servicenow-mcp -> {mcp_url}")
    return conn_id
