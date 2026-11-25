"""Execute catalog flows: agents + tools, parallel groups, HITL, pause/retry/stop."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from flow_control import (
    callback_logic_app_hitl,
    find_node_id,
    pause_at_hitl,
    reset_from_node,
    resolve_hitl_reviewers,
    user_can_approve_hitl,
    user_can_control_run,
    user_can_view_run,
)
from workflow_graph import build_graph, get_flow_meta, list_flows, runtime_nodes_from_graph

logger = logging.getLogger("afa.engine")

InvokeFn = Callable[..., Awaitable[dict]]
SetNodeFn = Callable[..., Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_map(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in run.get("nodes") or []}


def _successors(run: dict[str, Any], node_id: str) -> list[str]:
    return [e["target"] for e in (run.get("edges") or []) if e.get("source") == node_id]


def _predecessors(run: dict[str, Any], node_id: str) -> list[str]:
    return [e["source"] for e in (run.get("edges") or []) if e.get("target") == node_id]


def _agent_nodes(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in run["nodes"] if n.get("agent")]


async def apply_agent_result(set_node: SetNodeFn, node_id: str, out: dict, correlation_id: str) -> None:
    await set_node(
        node_id,
        status="Succeeded",
        output=out,
        skills=out.get("skills") or [],
        mcps=out.get("mcps") or [],
        tools=out.get("tools") or [],
        toolExecutions=out.get("toolExecutions") or [],
        usage=out.get("usage"),
        guardrails=out.get("guardrails"),
        model=out.get("model"),
        # Promote Foundry turn trace onto the node for the details panel
        trace=out.get("trace") or [],
        responseId=out.get("responseId"),
        policy={"gateway": "allowed", "correlationId": correlation_id, "secrets": out.get("secrets")},
    )


async def run_agent_node(
    run: dict[str, Any],
    user: Any,
    node: dict[str, Any],
    *,
    invoke: InvokeFn,
    set_node: SetNodeFn,
    request: Any = None,
    extra_message: str = "",
) -> dict:
    agent = node["agent"]
    message = run["message"]
    if extra_message:
        message = f"{message}\n\n{extra_message}"
    await set_node(
        node["id"],
        status="Running",
        input={
            "message": run["message"],
            "agent": agent,
            "toolsHint": node.get("toolsHint") or [],
            "parallel": bool(node.get("parallelGroup")),
        },
    )
    out = await invoke(
        user,
        agent,
        message,
        meta={
            "correlationId": run.get("correlationId"),
            "nodeId": node["id"],
            "flowId": run.get("flowId"),
            "parallel": bool(node.get("parallelGroup")),
        },
        request=request,
    )
    await apply_agent_result(set_node, node["id"], out, run.get("correlationId") or "")
    return out


def _topo_agent_batches(run: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Group agent nodes into sequential batches; same parallelGroup (or same preds) = one batch."""
    agents = _agent_nodes(run)
    if not agents:
        return []

    # Exclude post-HITL agents until after HITL
    hitl_id = find_node_id(run, "Wait_For_HITL_Callback", "Node4_HITL")
    post_hitl: set[str] = set()
    if hitl_id:
        stack = [hitl_id]
        seen = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for s in _successors(run, cur):
                post_hitl.add(s)
                stack.append(s)

    pre = [n for n in agents if n["id"] not in post_hitl]
    # Order by graph depth
    depth: dict[str, int] = {}

    def depth_of(nid: str) -> int:
        if nid in depth:
            return depth[nid]
        preds = _predecessors(run, nid)
        if not preds:
            depth[nid] = 0
        else:
            depth[nid] = max(depth_of(p) for p in preds) + 1
        return depth[nid]

    for n in pre:
        depth_of(n["id"])

    by_depth: dict[int, list[dict[str, Any]]] = {}
    for n in pre:
        by_depth.setdefault(depth[n["id"]], []).append(n)

    batches: list[list[dict[str, Any]]] = []
    for d in sorted(by_depth):
        group = by_depth[d]
        # Split by parallelGroup if mixed
        pg: dict[str | None, list] = {}
        for n in group:
            key = n.get("parallelGroup")
            # Nodes sharing predecessors are parallel even without explicit group
            if not key and len(group) > 1:
                key = f"depth_{d}"
            pg.setdefault(key, []).append(n)
        for nodes in pg.values():
            batches.append(nodes)
    return batches


async def execute_pre_hitl(
    run: dict[str, Any],
    user: Any,
    *,
    invoke: InvokeFn,
    set_node: SetNodeFn,
    request: Any = None,
    from_node: str | None = None,
) -> None:
    """Run system + agent nodes until HITL. Honors path for multi-agent-flow."""
    nm = _node_map(run)
    if "Init_State" in nm and (not from_node or from_node == "Init_State"):
        await set_node("Init_State", status="Succeeded", output={"runId": run["id"], "flowId": run["flowId"]})

    # Path handling for multi-agent-flow
    flow_id = run.get("flowId") or ""
    path = (run.get("path") or "A").upper()

    if flow_id == "finops-approval-flow":
        run["path"] = "B"
        path = "B"

    batches = _topo_agent_batches(run)
    started = not from_node

    for batch in batches:
        if run.get("cancelled"):
            run["status"] = "Cancelled"
            return
        if run.get("paused") and run.get("status") == "Paused":
            return

        # Skip until from_node encountered
        if not started:
            if any(n["id"] == from_node for n in batch):
                started = True
            else:
                continue

        # Path filtering for multi-agent-flow
        filtered = []
        for n in batch:
            if flow_id == "multi-agent-flow":
                if path == "B" and n["id"] in ("Invoke_Repo_Ops", "Invoke_Sec_Audit"):
                    await set_node(n["id"], status="Skipped")
                    continue
                if path == "A" and n["id"] == "Node3C_FinOps":
                    await set_node(n["id"], status="Skipped")
                    continue
            # Already succeeded and not the retry target
            if n.get("status") == "Succeeded" and n["id"] != from_node:
                continue
            filtered.append(n)

        if not filtered:
            continue

        # Mark Set_Path / Path_Router when triage done
        if any(n["id"] == "Node1_Triage" for n in filtered):
            pass

        # Execute batch (parallel conceptually — await sequentially but mark parallel)
        for n in filtered:
            if run.get("cancelled") or (run.get("paused") and run.get("status") == "Paused"):
                return
            try:
                out = await run_agent_node(run, user, n, invoke=invoke, set_node=set_node, request=request)
                if n["id"] == "Node1_Triage":
                    rec = out.get("recommended_path")
                    if rec and flow_id == "multi-agent-flow":
                        run["path"] = str(rec).upper()
                        path = run["path"]
                    for sys_id in ("Set_Path", "Path_Router", "Conditional_Router", "Checkpoint_Node1"):
                        if sys_id in nm:
                            await set_node(sys_id, status="Succeeded", output={"path": run.get("path")})
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
                gr = detail.get("guardrails") if isinstance(detail, dict) else None
                blocked = isinstance(gr, dict) and bool(gr.get("blocked"))
                await set_node(
                    n["id"],
                    status="Failed",
                    output=detail,
                    tools=detail.get("tools") or [],
                    toolExecutions=detail.get("toolExecutions") or [],
                    mcps=detail.get("mcps") or [],
                    guardrails=gr,
                    policy={
                        "gateway": "blocked" if blocked else "error",
                        "reason": detail.get("error") if isinstance(detail, dict) else str(detail),
                        "correlationId": run.get("correlationId"),
                    },
                )
                run["status"] = "Failed"
                run["error"] = (
                    detail.get("error")
                    if isinstance(detail, dict)
                    else str(detail)
                )
                return

        # Join nodes after parallel batch
        for n in batch:
            for succ in _successors(run, n["id"]):
                sn = nm.get(succ)
                if sn and sn.get("type") == "Compose" and succ.startswith("Join_"):
                    preds = _predecessors(run, succ)
                    if all(_node_map(run).get(p, {}).get("status") in ("Succeeded", "Failed", "Skipped") for p in preds):
                        await set_node(
                            succ,
                            status="Succeeded",
                            output={p: _node_map(run).get(p, {}).get("output") for p in preds},
                        )

    if run.get("status") == "Failed" or run.get("cancelled"):
        return

    await pause_at_hitl(run, set_node)


async def apply_hitl_decision(
    run: dict[str, Any],
    user: Any,
    *,
    decision: str,
    notes: str,
    set_node: SetNodeFn,
    path_override: str | None = None,
    restart_from_node: str | None = None,
    extra_input: dict[str, Any] | None = None,
    callback_result: dict[str, Any] | None = None,
) -> str | None:
    """Mark HITL resolved immediately so the UI leaves Waiting_HITL before long agent work.

    Returns restart node id when decision is reroute; otherwise None.
    """
    if not user_can_approve_hitl(user, run):
        raise HTTPException(status_code=403, detail="Not an assigned HITL reviewer for this run")

    hitl = find_node_id(run, "Wait_For_HITL_Callback", "Node4_HITL") or "Wait_For_HITL_Callback"
    decision_node = find_node_id(run, "HITL_Decision")
    hitl_input = {
        "decision": decision,
        "notes": notes,
        "pathOverride": path_override,
        "restartFromNode": restart_from_node,
        **(extra_input or {}),
    }
    hitl_output = {
        "decision": decision,
        "notes": notes,
        "message": f"HITL {decision}" + (f": {notes}" if notes else ""),
        "output_markdown": (
            f"**Decision:** {decision}\n\n**Notes:** {notes or '—'}"
            + (f"\n\n**Extra input:** `{json.dumps(extra_input)}`" if extra_input else "")
        ),
        "approver": user.oid,
        "pathOverride": path_override,
        "restartFromNode": restart_from_node,
        "input": extra_input or {},
        "at": _now(),
    }

    run["hitl"] = {
        **(run.get("hitl") or {}),
        "lastDecision": decision,
        "notes": notes,
        "input": extra_input or {},
        "approverOid": user.oid,
        "callbackResult": callback_result,
        "at": _now(),
    }
    run["hitlDecision"] = decision
    run["paused"] = False

    # Clear every waiting HITL node (id mismatches used to leave the canvas stuck).
    for n in run.get("nodes") or []:
        if n.get("status") == "Waiting_HITL" or n["id"] in (hitl, "Node4_HITL", "Wait_For_HITL_Callback"):
            await set_node(n["id"], status="Succeeded", input=hitl_input, output=hitl_output)
    if decision_node:
        await set_node(decision_node, status="Succeeded", output={"decision": decision})

    def _skip_branch(branch: str, reason: str) -> None:
        for n in run.get("nodes") or []:
            if n.get("branch") == branch and n["id"] != decision_node:
                n["status"] = "Skipped"
                n["output"] = {"skipped": True, "reason": reason, "takenPath": decision}
                n["endedAt"] = _now()

    if decision == "reject":
        _skip_branch("approve", "HITL rejected — approve path not taken")
        for nid in ("Node_HITL_Reject_Notes", "Final_Rejected", "Cancelled_Response"):
            if find_node_id(run, nid):
                await set_node(
                    nid,
                    status="Succeeded",
                    output={"status": "Rejected", "decision": "reject", "reason": notes},
                )
        for nid in ("Node5_Synthesize", "Final_Approved", "Final_Response"):
            if find_node_id(run, nid):
                await set_node(
                    nid,
                    status="Skipped",
                    output={"skipped": True, "reason": "HITL rejected"},
                )
        run["status"] = "Cancelled"
        return None

    if decision == "reroute":
        new_path = (path_override or run.get("path") or "A").upper()
        if new_path not in ("A", "B"):
            raise HTTPException(status_code=400, detail="pathOverride must be A or B")
        run["path"] = new_path
        start = restart_from_node or find_node_id(run, "Path_Router", "Node1_Triage", "Set_Path") or "Node1_Triage"
        reset_from_node(run, start)
        run["status"] = "Running"
        return start

    # approve — skip reject branch; synth continues in background
    _skip_branch("reject", "HITL approved — reject path not taken")
    for n in run.get("nodes") or []:
        if n["id"] in ("Node_HITL_Reject_Notes", "Final_Rejected", "Cancelled_Response") or (
            n.get("branch") == "reject" and n["id"] != decision_node
        ):
            await set_node(
                n["id"],
                status="Skipped",
                output={
                    "skipped": True,
                    "reason": "HITL approved — reject path not taken",
                    "decision": "approve",
                },
            )
    synth = find_node_id(run, "Node5_Synthesize", "Node5_Synth")
    if synth:
        run["status"] = "Running"
        await set_node(synth, status="Running", input={"message": f"HITL notes: {notes}"})
    else:
        final_ok = find_node_id(run, "Final_Approved", "Final_Response")
        if final_ok:
            await set_node(final_ok, status="Succeeded", output={"status": "Succeeded", "decision": "approve"})
        run["status"] = "Succeeded"
    return None


async def continue_after_hitl(
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
    extra_input: dict[str, Any] | None = None,
) -> None:
    """Long-running post-HITL work (approve synth / reroute path)."""
    if decision == "reject":
        return

    if decision == "reroute":
        start = restart_from_node or find_node_id(run, "Path_Router", "Node1_Triage", "Set_Path") or "Node1_Triage"
        await execute_pre_hitl(
            run, user, invoke=invoke, set_node=set_node, request=request, from_node=start
        )
        return

    synth = find_node_id(run, "Node5_Synthesize", "Node5_Synth")
    if not synth:
        return
    extra = f"HITL notes: {notes}"
    if extra_input:
        extra += f"\nHITL input: {extra_input}"
    try:
        node = next(n for n in run["nodes"] if n["id"] == synth)
        await run_agent_node(
            run, user, node, invoke=invoke, set_node=set_node, request=request, extra_message=extra
        )
        final_ok = find_node_id(run, "Final_Approved", "Final_Response")
        if final_ok:
            out = next(n for n in run["nodes"] if n["id"] == synth).get("output")
            await set_node(
                final_ok,
                status="Succeeded",
                output={"status": "Succeeded", "decision": "approve", "output": out},
            )
        run["status"] = "Succeeded"
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
        await set_node(synth, status="Failed", output=detail)
        run["status"] = "Failed"
        run["error"] = detail.get("error") or detail.get("body") or str(detail)
        for nid in ("Node_HITL_Reject_Notes", "Final_Rejected"):
            if find_node_id(run, nid):
                await set_node(
                    nid,
                    status="Skipped",
                    output={"skipped": True, "reason": "HITL was approve; synthesize failed", "decision": "approve"},
                )
    run["paused"] = False


async def execute_hitl_decision(
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
    extra_input: dict[str, Any] | None = None,
) -> None:
    """Synchronous full HITL path (tests / callers that don't background)."""
    cb = await callback_logic_app_hitl(
        run,
        decision=decision,
        notes=notes,
        approver_oid=user.oid,
        path_override=path_override,
        restart_from_node=restart_from_node,
        extra_input=extra_input,
    )
    await apply_hitl_decision(
        run,
        user,
        decision=decision,
        notes=notes,
        set_node=set_node,
        path_override=path_override,
        restart_from_node=restart_from_node,
        extra_input=extra_input,
        callback_result=cb,
    )
    await continue_after_hitl(
        run,
        user,
        decision=decision,
        notes=notes,
        invoke=invoke,
        set_node=set_node,
        request=request,
        path_override=path_override,
        restart_from_node=restart_from_node,
        extra_input=extra_input,
    )


def new_run(
    *,
    flow_id: str,
    user: Any,
    message: str,
    correlation_id: str,
    path_hint: str | None = None,
    hitl_oids: list[str] | None = None,
    hitl_roles: list[str] | None = None,
) -> dict[str, Any]:
    meta = get_flow_meta(flow_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown flow: {flow_id}")
    graph = build_graph(flow_id)
    nodes = runtime_nodes_from_graph(graph)
    for n in nodes:
        n["correlationId"] = correlation_id

    reviewers = resolve_hitl_reviewers(
        requester_oid=user.oid,
        explicit_oids=hitl_oids,
        explicit_roles=hitl_roles or meta.get("hitlReviewerRoles"),
    )
    path = path_hint or meta.get("defaultPath") or "A"
    return {
        "id": "",  # filled by caller
        "flowId": flow_id,
        "flowName": meta.get("name"),
        "userOid": user.oid,
        "userName": user.name,
        "roles": user.roles,
        "message": message,
        "path": path,
        "status": "Running",
        "nodes": nodes,
        "edges": graph["edges"],
        "graphSource": graph.get("source"),
        "features": graph.get("features") or meta.get("features") or [],
        "createdAt": _now(),
        "updatedAt": _now(),
        "paused": False,
        "cancelled": False,
        "correlationId": correlation_id,
        "logicApp": None,
        "hitlReviewerOids": reviewers["hitlReviewerOids"],
        "hitlReviewerRoles": reviewers["hitlReviewerRoles"],
        "hitl": {},
        "controls": {
            "canPause": True,
            "canResume": True,
            "canStop": True,
            "canRetry": True,
            "canRetryFromNode": True,
        },
    }


# re-export helpers used by main
__all__ = [
    "apply_agent_result",
    "apply_hitl_decision",
    "continue_after_hitl",
    "execute_hitl_decision",
    "execute_pre_hitl",
    "list_flows",
    "new_run",
    "user_can_approve_hitl",
    "user_can_control_run",
    "user_can_view_run",
    "reset_from_node",
    "find_node_id",
]
