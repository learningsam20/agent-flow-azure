#!/usr/bin/env python3
"""mcp-afa-audit-store — FastMCP server backed by Cosmos/Table via Managed Identity."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mcp-afa-audit-store")


def _cosmos():
    endpoint = os.environ["COSMOS_ENDPOINT"]
    cred = DefaultAzureCredential(
        managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None
    )
    token_provider = get_bearer_token_provider(cred, "https://cosmos.azure.com/.default")
    client = CosmosClient(endpoint, credential=token_provider)
    db = client.get_database_client("workflow")
    return db.get_container_client("checkpoints"), db.get_container_client("runs")


@mcp.tool()
def list_runs(user_oid: str, limit: int = 20) -> dict[str, Any]:
    """List workflow/agent runs for a user oid."""
    _, runs = _cosmos()
    items = list(
        runs.query_items(
            query="SELECT TOP @lim * FROM c WHERE c.userOid = @oid ORDER BY c._ts DESC",
            parameters=[{"name": "@oid", "value": user_oid}, {"name": "@lim", "value": limit}],
            enable_cross_partition_query=True,
        )
    )
    return {"runs": items}


@mcp.tool()
def get_node_state(run_id: str, node_id: str) -> dict[str, Any]:
    """Fetch checkpoint/state for a workflow node."""
    checkpoints, _ = _cosmos()
    items = list(
        checkpoints.query_items(
            query="SELECT * FROM c WHERE c.runId = @run AND c.nodeId = @node",
            parameters=[{"name": "@run", "value": run_id}, {"name": "@node", "value": node_id}],
            enable_cross_partition_query=True,
        )
    )
    return {"state": items[0] if items else None}


@mcp.tool()
def save_artifact(run_id: str, node_id: str, content: str, content_type: str = "text/markdown") -> dict[str, Any]:
    """Persist a node artifact metadata record (blob URI resolved by caller via MI)."""
    checkpoints, _ = _cosmos()
    doc = {
        "id": f"{run_id}:{node_id}:{datetime.now(timezone.utc).timestamp()}",
        "runId": run_id,
        "nodeId": node_id,
        "contentType": content_type,
        "contentPreview": content[:2000],
        "savedAt": datetime.now(timezone.utc).isoformat(),
    }
    checkpoints.upsert_item(doc)
    return {"saved": True, "id": doc["id"]}


@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "auth": "workload_identity"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    mcp.run(transport="sse", host="0.0.0.0", port=port)
