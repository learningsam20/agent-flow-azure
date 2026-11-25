"""Derive React Flow graphs from Azure Logic Apps workflow definitions.

Catalog: workflows/catalog.json — multiple demonstrable flows.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "workflows" / "catalog.json"
WORKFLOWS_DIR = ROOT / "workflows" / "logic-app-standard"

SYSTEM_TYPES = {
    "InitializeVariable",
    "SetVariable",
    "Compose",
    "Response",
}

AGENT_URI_RE = re.compile(r"/api/agents/([a-zA-Z0-9_-]+)/")


def load_catalog() -> dict[str, Any]:
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text())
    return {"flows": []}


def _discover_local_workflow_flows(existing_ids: set[str]) -> list[dict[str, Any]]:
    """Pick up workflow.json folders under logic-app-standard not yet in catalog.json.

    Adding a Logic App only in Azure does NOT auto-register it — the app reads
    catalog.json (and local workflow folders). Deploy/import the workflow into
    workflows/logic-app-standard/<id>/workflow.json and optionally add a rich
    catalog entry for name/description/HITL roles.
    """
    discovered: list[dict[str, Any]] = []
    if not WORKFLOWS_DIR.is_dir():
        return discovered
    for child in sorted(WORKFLOWS_DIR.iterdir()):
        if not child.is_dir():
            continue
        wf = child / "workflow.json"
        if not wf.exists():
            continue
        fid = child.name
        if fid in existing_ids:
            continue
        discovered.append(
            {
                "id": fid,
                "name": fid.replace("-", " ").replace("_", " ").title(),
                "description": (
                    f"Auto-discovered from {wf.relative_to(ROOT)}. "
                    "Add an entry to workflows/catalog.json for demo prompt, HITL roles, and Logic App name."
                ),
                "features": ["discovered"],
                "workflowPath": str(wf.relative_to(ROOT)),
                "discovered": True,
            }
        )
    return discovered


def list_flows() -> list[dict[str, Any]]:
    """Local catalog ∪ local workflow folders ∪ last Azure sync cache."""
    catalog = list(load_catalog().get("flows") or [])
    existing = {str(f.get("id")) for f in catalog if f.get("id")}
    merged = list(catalog) + _discover_local_workflow_flows(existing)
    existing = {str(f.get("id")) for f in merged if f.get("id")}
    try:
        from azure_sync import synced_flows

        for f in synced_flows():
            fid = str(f.get("id") or "")
            if not fid:
                continue
            if fid in existing:
                # Enrich catalog entry with Azure logicAppName / trigger metadata without replacing demos.
                for i, cur in enumerate(merged):
                    if cur.get("id") == fid:
                        enrich = {
                            k: v
                            for k, v in f.items()
                            if k in ("logicAppName", "logicAppId", "state", "source", "workflowPath")
                            and v
                            and not cur.get(k)
                        }
                        if enrich:
                            merged[i] = {**cur, **enrich, "synced": True}
                        break
            else:
                merged.append({**f, "synced": True})
                existing.add(fid)
    except Exception:
        pass
    return merged


def get_flow_meta(flow_id: str) -> dict[str, Any] | None:
    for f in list_flows():
        if f.get("id") == flow_id:
            return f
    return None


def workflow_path_for(flow_id: str) -> Path:
    meta = get_flow_meta(flow_id)
    if meta and meta.get("workflowPath"):
        p = ROOT / meta["workflowPath"]
        if p.exists():
            return p
        # Absolute path from sync cache
        abs_p = Path(str(meta["workflowPath"]))
        if abs_p.is_absolute() and abs_p.exists():
            return abs_p
    candidate = WORKFLOWS_DIR / flow_id / "workflow.json"
    if candidate.exists():
        return candidate
    synced = ROOT / ".data" / "synced-workflows" / flow_id / "workflow.json"
    if synced.exists():
        return synced
    return WORKFLOWS_DIR / "multi-agent-flow" / "workflow.json"


def load_workflow_definition(flow_id: str = "multi-agent-flow") -> dict[str, Any]:
    p = workflow_path_for(flow_id)
    raw = json.loads(p.read_text())
    return raw.get("definition", raw)


def _agent_from_action(action: dict[str, Any]) -> str | None:
    inputs = action.get("inputs") or {}
    uri = str(inputs.get("uri") or "")
    m = AGENT_URI_RE.search(uri)
    if m:
        return m.group(1)
    body = inputs.get("body")
    if isinstance(body, dict) and body.get("agentId"):
        return str(body["agentId"])
    return None


def _tools_hint(action: dict[str, Any]) -> list[str]:
    body = (action.get("inputs") or {}).get("body")
    if isinstance(body, dict) and body.get("toolsRequired"):
        return list(body["toolsRequired"])
    return []


def _label_for(action_name: str, action: dict[str, Any], agent: str | None) -> str:
    if agent:
        return agent.replace("-agent", "").replace("-", " ").title()
    t = action.get("type", "")
    if t == "HttpWebhook":
        return "HITL Wait / Input"
    if action_name in ("Path_Router", "Conditional_Router"):
        return "Path Router (A∥ / B)"
    if action_name == "HITL_Decision":
        return "HITL Branch"
    if action_name.startswith("Join_"):
        return "Join Parallel"
    if action_name.startswith("Final_"):
        return action_name.replace("_", " ")
    if t == "If":
        return "Conditional"
    return action_name.replace("_", " ")


def _flatten_actions(
    actions: dict[str, Any],
    *,
    parent_id: str | None = None,
    branch: str | None = None,
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for name, action in (actions or {}).items():
        run_after = action.get("runAfter")
        if run_after is None:
            run_after = {parent_id: ["Succeeded"]} if parent_id else {}
        elif parent_id and not run_after:
            run_after = {parent_id: ["Succeeded"]}

        agent = _agent_from_action(action) if action.get("type") == "Http" else None
        node = {
            "id": name,
            "label": _label_for(name, action, agent),
            "type": action.get("type"),
            "kind": action.get("kind"),
            "agent": agent,
            "system": action.get("type") in SYSTEM_TYPES and not name.startswith("Join_"),
            "runAfter": list((run_after or {}).keys()),
            "branch": branch,
            "parallelGroup": None,
            "toolsHint": _tools_hint(action),
        }
        nodes.append(node)

        if action.get("type") == "If":
            then_actions = action.get("actions") or {}
            else_actions = (action.get("else") or {}).get("actions") or {}
            then_branch = "approve" if name == "HITL_Decision" else "pathB"
            else_branch = "reject" if name == "HITL_Decision" else "pathA"
            for child in _flatten_actions(then_actions, parent_id=name, branch=then_branch):
                if not child["runAfter"] or child["runAfter"] == [name]:
                    child["runAfter"] = [name]
                nodes.append(child)
            else_children = _flatten_actions(else_actions, parent_id=name, branch=else_branch)
            roots = [c for c in else_children if c["runAfter"] == [name] or not c["runAfter"]]
            if len(roots) >= 2 and name != "HITL_Decision":
                for c in roots:
                    c["parallelGroup"] = f"{name}_parallel"
                    c["runAfter"] = [name]
            for child in else_children:
                if not child["runAfter"]:
                    child["runAfter"] = [name]
                nodes.append(child)

    return nodes


def _layout(nodes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    ids = {n["id"] for n in nodes}
    level: dict[str, int] = {}
    for n in nodes:
        preds = [p for p in n["runAfter"] if p in ids]
        if not preds:
            level[n["id"]] = 0
        else:
            level[n["id"]] = max(level.get(p, 0) for p in preds) + 1

    by_level: dict[int, list[dict[str, Any]]] = {}
    for n in nodes:
        by_level.setdefault(level.get(n["id"], 0), []).append(n)

    pos: dict[str, dict[str, float]] = {}
    for lvl, group in sorted(by_level.items()):

        def sort_key(n: dict[str, Any]) -> tuple:
            b = n.get("branch") or ""
            order = {"pathA": 0, "reject": 0, "pathB": 2, "approve": 2}.get(b, 1)
            return (order, n["id"])

        group = sorted(group, key=sort_key)
        width = max(len(group), 1)
        for i, n in enumerate(group):
            x_base = (i - (width - 1) / 2) * 260 + 280
            if n.get("branch") == "pathA":
                x_base -= 60
            elif n.get("branch") == "pathB":
                x_base += 60
            elif n.get("branch") == "approve":
                x_base += 100
            elif n.get("branch") == "reject":
                x_base -= 100
            pos[n["id"]] = {"x": x_base, "y": lvl * 150}
    return pos


def build_graph(flow_id: str = "multi-agent-flow") -> dict[str, Any]:
    definition = load_workflow_definition(flow_id)
    meta = get_flow_meta(flow_id) or {"id": flow_id, "name": flow_id}
    actions = definition.get("actions") or {}
    nodes = _flatten_actions(actions)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for n in nodes:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        unique.append(n)

    by_preds: dict[tuple, list[str]] = defaultdict(list)
    for n in unique:
        key = tuple(sorted(n["runAfter"]))
        if key:
            by_preds[key].append(n["id"])
    for key, ids in by_preds.items():
        if len(ids) >= 2:
            for nid in ids:
                for n in unique:
                    if n["id"] == nid and not n.get("parallelGroup"):
                        n["parallelGroup"] = f"parallel_{'_'.join(key)}"

    layout = _layout(unique)
    edges: list[dict[str, Any]] = []
    id_set = {n["id"] for n in unique}
    for n in unique:
        for src in n["runAfter"]:
            if src not in id_set:
                continue
            label = None
            if n.get("branch") == "pathA":
                label = "∥ Path A"
            elif n.get("branch") == "pathB":
                label = "Path B"
            elif n.get("branch") == "approve":
                label = "Approve"
            elif n.get("branch") == "reject":
                label = "Reject"
            if n.get("parallelGroup") and not label:
                label = "∥ parallel"
            edges.append(
                {
                    "id": f"e_{src}_{n['id']}",
                    "source": src,
                    "target": n["id"],
                    "label": label,
                }
            )

    graph_nodes = []
    for n in unique:
        graph_nodes.append(
            {
                "id": n["id"],
                "label": n["label"],
                "agent": n.get("agent"),
                "type": n.get("type"),
                "system": n.get("system", False),
                "branch": n.get("branch"),
                "parallelGroup": n.get("parallelGroup"),
                "toolsHint": n.get("toolsHint") or [],
                "position": layout.get(n["id"], {"x": 0, "y": 0}),
                "status": "Pending",
                "input": None,
                "output": None,
                "skills": [],
                "mcps": [],
                "tools": [],
                "toolExecutions": [],
                "policy": None,
                "usage": None,
                "guardrails": None,
                "model": None,
                "startedAt": None,
                "endedAt": None,
                "durationMs": None,
            }
        )

    wp = workflow_path_for(flow_id)
    try:
        source = str(wp.relative_to(ROOT))
    except ValueError:
        source = str(wp)

    return {
        "flowId": flow_id,
        "name": meta.get("name"),
        "description": meta.get("description"),
        "demoPrompt": meta.get("demoPrompt"),
        "features": meta.get("features") or [],
        "hitlReviewerRoles": meta.get("hitlReviewerRoles") or [],
        "source": source,
        "nodes": graph_nodes,
        "edges": edges,
    }


def runtime_nodes_from_graph(graph: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for n in graph["nodes"]:
        out.append(
            {
                "id": n["id"],
                "label": n["label"],
                "agent": n.get("agent"),
                "type": n.get("type"),
                "system": n.get("system", False),
                "branch": n.get("branch"),
                "parallelGroup": n.get("parallelGroup"),
                "toolsHint": n.get("toolsHint") or [],
                "status": "Pending",
                "input": None,
                "output": None,
                "skills": [],
                "mcps": [],
                "tools": [],
                "toolExecutions": [],
                "policy": None,
                "usage": None,
                "guardrails": None,
                "model": None,
                "startedAt": None,
                "endedAt": None,
                "durationMs": None,
                "correlationId": None,
            }
        )
    return out
