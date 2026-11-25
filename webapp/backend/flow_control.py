"""Workflow control plane: HITL reviewers, pause/resume/stop/retry, HITL re-route.

Execution follows the Logic Apps graph (parallel Path A, Path B, HITL branches).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

logger = logging.getLogger("afa.flow")

DEFAULT_HITL_ROLES = ["App.Admin", "Agent.Admin", "Agent.SecurityAuditor"]

# Ordered agent steps for Path A (parallel) and Path B
PATH_A_AGENTS = [
    ("repo-ops-agent", "Invoke_Repo_Ops"),
    ("sec-audit-agent", "Invoke_Sec_Audit"),
]
PATH_B_AGENTS = [
    ("finops-analyst-agent", "Node3C_FinOps"),
]
SYNTH_NODE = "Node5_Synthesize"
HITL_NODE = "Wait_For_HITL_Callback"
ROUTER_NODE = "Path_Router"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_hitl_roles() -> list[str]:
    raw = os.getenv("HITL_REVIEWER_ROLES", ",".join(DEFAULT_HITL_ROLES))
    return [r.strip() for r in raw.split(",") if r.strip()]


def default_hitl_oids() -> list[str]:
    raw = os.getenv("HITL_REVIEWER_OIDS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def resolve_hitl_reviewers(
    *,
    requester_oid: str,
    explicit_oids: list[str] | None = None,
    explicit_roles: list[str] | None = None,
) -> dict[str, Any]:
    oids = list(dict.fromkeys((explicit_oids or []) + default_hitl_oids()))
    roles = list(dict.fromkeys(explicit_roles or default_hitl_roles()))
    return {
        "hitlReviewerOids": oids,
        "hitlReviewerRoles": roles,
        "requestedByOid": requester_oid,
    }


def user_is_hitl_reviewer(user: Any, run: dict[str, Any]) -> bool:
    if user.oid in (run.get("hitlReviewerOids") or []):
        return True
    user_roles = set(user.app_roles or []) | set(user.agent_roles or []) | set(user.roles or [])
    for r in run.get("hitlReviewerRoles") or []:
        if r in user_roles:
            return True
    return False


def user_can_view_run(user: Any, run: dict[str, Any]) -> bool:
    if run.get("userOid") == user.oid:
        return True
    if user.is_admin():
        return True
    return user_is_hitl_reviewer(user, run)


def user_can_control_run(user: Any, run: dict[str, Any]) -> bool:
    """Owner or admin can pause/resume/stop/retry."""
    return run.get("userOid") == user.oid or user.is_admin()


def user_can_approve_hitl(user: Any, run: dict[str, Any]) -> bool:
    if not user.can_hitl() and not user.is_admin():
        return False
    if user.is_admin():
        return True
    return user_is_hitl_reviewer(user, run)


def node_index(run: dict[str, Any], node_id: str) -> int:
    for i, n in enumerate(run.get("nodes") or []):
        if n["id"] == node_id:
            return i
    return -1


def find_node_id(run: dict[str, Any], *candidates: str) -> str | None:
    ids = {n["id"] for n in run.get("nodes") or []}
    for c in candidates:
        if c in ids:
            return c
    return None


def reset_from_node(run: dict[str, Any], node_id: str) -> None:
    """Clear status of node_id and all downstream (by edge reachability)."""
    edges = run.get("edges") or []
    downstream: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        if cur in downstream:
            continue
        downstream.add(cur)
        for e in edges:
            if e.get("source") == cur and e.get("target") not in downstream:
                stack.append(e["target"])
    for n in run["nodes"]:
        if n["id"] in downstream:
            n["status"] = "Pending"
            n["input"] = None
            n["output"] = None
            n["startedAt"] = None
            n["endedAt"] = None
            n["durationMs"] = None
            n["usage"] = None
            n["guardrails"] = None
            n["skills"] = []
            n["mcps"] = []
            n["tools"] = []
            n["policy"] = None


async def callback_logic_app_hitl(
    run: dict[str, Any],
    *,
    decision: str,
    notes: str,
    approver_oid: str,
    path_override: str | None = None,
    restart_from_node: str | None = None,
    extra_input: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """POST human decision to Logic Apps HttpWebhook callbackUrl if present."""
    import httpx

    url = (run.get("hitl") or {}).get("callbackUrl") or os.getenv("LOGIC_APP_HITL_CALLBACK_URL")
    if not url:
        return None
    payload = {
        "decision": decision,
        "notes": notes,
        "approverOid": approver_oid,
        "runId": run["id"],
        "pathOverride": path_override,
        "restartFromNode": restart_from_node,
        "input": extra_input or {},
        "at": _now(),
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            return {"status": resp.status_code, "body": resp.text[:500]}
    except Exception as exc:
        logger.warning("HITL callback failed: %s", exc)
        return {"error": str(exc)}


InvokeFn = Callable[..., Awaitable[dict]]
SetNodeFn = Callable[..., Awaitable[None]]
PersistFn = Callable[[dict], None]


async def execute_path_agents(
    run: dict[str, Any],
    user: Any,
    *,
    invoke: InvokeFn,
    set_node: SetNodeFn,
    request: Any = None,
    from_node: str | None = None,
) -> None:
    """Run Path A (parallel) or Path B agents based on run['path']. Respects pause/cancel."""
    path = (run.get("path") or "A").upper()
    correlation_id = run.get("correlationId")
    message = run.get("message") or ""

    router = find_node_id(run, "Path_Router", "Conditional_Router")
    if router and (not from_node or from_node in (router, "Set_Path", "Checkpoint_Node1", "Node1_Triage")):
        await set_node(router, status="Succeeded", output={"path": path})

    if path == "B":
        for _, nid in PATH_A_AGENTS:
            if find_node_id(run, nid):
                await set_node(nid, status="Skipped")
        join = find_node_id(run, "Join_Parallel_PathA")
        if join:
            await set_node(join, status="Skipped")
        for agent, nid in PATH_B_AGENTS:
            if run.get("cancelled") or run.get("paused"):
                return
            if from_node and node_index(run, nid) < node_index(run, from_node):
                continue
            if not find_node_id(run, nid):
                continue
            await set_node(nid, status="Running", input={"message": message})
            try:
                out = await invoke(
                    user, agent, message, meta={"correlationId": correlation_id}, request=request
                )
                await set_node(
                    nid,
                    status="Succeeded",
                    output=out,
                    skills=out.get("skills") or [],
                    mcps=out.get("mcps") or [],
                    tools=out.get("tools") or [],
                    usage=out.get("usage"),
                    guardrails=out.get("guardrails"),
                    model=out.get("model"),
                    policy={"gateway": "allowed", "correlationId": correlation_id},
                )
            except HTTPException as exc:
                await set_node(nid, status="Failed", output=exc.detail)
                run["status"] = "Failed"
                return
    else:
        for _, nid in PATH_B_AGENTS:
            if find_node_id(run, nid):
                await set_node(nid, status="Skipped")
        # Parallel Path A — fire both (await sequentially but mark as parallel group)
        for agent, nid in PATH_A_AGENTS:
            if run.get("cancelled") or run.get("paused"):
                return
            if from_node and node_index(run, nid) < node_index(run, from_node) and from_node not in {x[1] for x in PATH_A_AGENTS}:
                # If retrying one parallel sibling, still allow that sibling
                if nid != from_node:
                    continue
            if not find_node_id(run, nid):
                continue
            # skip if already succeeded and not the retry target
            existing = next((n for n in run["nodes"] if n["id"] == nid), None)
            if existing and existing.get("status") == "Succeeded" and nid != from_node:
                continue
            await set_node(nid, status="Running", input={"message": message, "parallel": True})
            try:
                out = await invoke(
                    user, agent, message, meta={"correlationId": correlation_id, "parallel": True}, request=request
                )
                await set_node(
                    nid,
                    status="Succeeded",
                    output=out,
                    skills=out.get("skills") or [],
                    mcps=out.get("mcps") or [],
                    tools=out.get("tools") or [],
                    usage=out.get("usage"),
                    guardrails=out.get("guardrails"),
                    model=out.get("model"),
                    policy={"gateway": "allowed", "correlationId": correlation_id, "parallel": True},
                )
            except HTTPException as exc:
                await set_node(nid, status="Failed", output=exc.detail)
                run["status"] = "Failed"
                return
        join = find_node_id(run, "Join_Parallel_PathA")
        if join:
            await set_node(
                join,
                status="Succeeded",
                output={
                    "repo": next((n.get("output") for n in run["nodes"] if n["id"] == "Invoke_Repo_Ops"), None),
                    "sec": next((n.get("output") for n in run["nodes"] if n["id"] == "Invoke_Sec_Audit"), None),
                },
            )


async def pause_at_hitl(run: dict[str, Any], set_node: SetNodeFn) -> None:
    hitl = find_node_id(run, HITL_NODE, "Node4_HITL") or HITL_NODE
    await set_node(
        hitl,
        status="Waiting_HITL",
        input={
            "message": "Awaiting human approval / input",
            "requiredInput": ["decision", "notes"],
            "options": ["approve", "reject", "reroute"],
            "reviewers": {
                "oids": run.get("hitlReviewerOids") or [],
                "roles": run.get("hitlReviewerRoles") or [],
            },
        },
    )
    run["status"] = "Waiting_HITL"
    run["paused"] = True


async def execute_post_hitl(
    run: dict[str, Any],
    user: Any,
    *,
    decision: str,
    notes: str,
    invoke: InvokeFn,
    set_node: SetNodeFn,
    request: Any = None,
    path_override: str | None = None,
    restart_from_node: str | None = None,
) -> None:
    """Continue after HITL: approve → synthesize; reject → Final_Rejected; reroute → re-exec path."""
    hitl = find_node_id(run, HITL_NODE, "Node4_HITL") or HITL_NODE
    decision_node = find_node_id(run, "HITL_Decision")
    correlation_id = run.get("correlationId")

    await set_node(
        hitl,
        status="Succeeded",
        output={
            "decision": decision,
            "notes": notes,
            "approver": user.oid,
            "pathOverride": path_override,
            "restartFromNode": restart_from_node,
            "at": _now(),
        },
    )
    if decision_node:
        await set_node(decision_node, status="Succeeded", output={"decision": decision})

    if decision == "reject":
        reject_notes = find_node_id(run, "Node_HITL_Reject_Notes")
        final_rej = find_node_id(run, "Final_Rejected", "Cancelled_Response")
        if reject_notes:
            await set_node(reject_notes, status="Succeeded", output={"decision": "reject", "notes": notes})
        if final_rej:
            await set_node(final_rej, status="Succeeded", output={"status": "Cancelled", "reason": notes})
        # skip approve branch
        for nid in (SYNTH_NODE, "Final_Approved", "Final_Response"):
            if find_node_id(run, nid):
                await set_node(nid, status="Skipped")
        run["status"] = "Cancelled"
        run["paused"] = False
        return

    if decision == "reroute":
        new_path = (path_override or run.get("path") or "A").upper()
        if new_path not in ("A", "B"):
            raise HTTPException(status_code=400, detail="pathOverride must be A or B")
        run["path"] = new_path
        start = restart_from_node or ROUTER_NODE
        if not find_node_id(run, start):
            start = find_node_id(run, "Path_Router", "Conditional_Router", "Set_Path") or ROUTER_NODE
        reset_from_node(run, start)
        # Mark HITL as needing re-approval after re-route execution
        run["status"] = "Running"
        run["paused"] = False
        run["hitl"] = {**(run.get("hitl") or {}), "lastDecision": "reroute", "notes": notes}
        await execute_path_agents(
            run, user, invoke=invoke, set_node=set_node, request=request, from_node=start
        )
        if run.get("status") == "Failed" or run.get("cancelled"):
            return
        if run.get("paused"):
            return
        await pause_at_hitl(run, set_node)
        return

    # approve
    for nid in ("Node_HITL_Reject_Notes", "Final_Rejected"):
        if find_node_id(run, nid):
            await set_node(nid, status="Skipped")

    synth = find_node_id(run, SYNTH_NODE, "Node5_Synth") or SYNTH_NODE
    await set_node(synth, status="Running", input={"message": run["message"], "hitlNotes": notes})
    try:
        out = await invoke(
            user,
            "exec-synthesizer-agent",
            f"{run['message']}\n\nHITL notes: {notes}" if notes else run["message"],
            meta={"correlationId": correlation_id, "hitlNotes": notes},
            request=request,
        )
        await set_node(
            synth,
            status="Succeeded",
            output=out,
            skills=out.get("skills") or [],
            mcps=out.get("mcps") or [],
            tools=out.get("tools") or [],
            usage=out.get("usage"),
            guardrails=out.get("guardrails"),
            model=out.get("model"),
        )
        final_ok = find_node_id(run, "Final_Approved", "Final_Response")
        if final_ok:
            await set_node(final_ok, status="Succeeded", output={"status": "Succeeded", "output": out})
        run["status"] = "Succeeded"
    except HTTPException as exc:
        await set_node(synth, status="Failed", output=exc.detail)
        run["status"] = "Failed"
    run["paused"] = False
