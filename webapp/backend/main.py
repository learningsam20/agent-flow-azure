"""Production FastAPI facade: Keycloak auth, APIM→Foundry/Functions/Logic Apps, Cosmos, App Insights.

Identity-only: user JWT forwarded to APIM; Managed Identity for Azure data plane.
No local agent simulation and no app-minted tokens.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from auth import (
    ACL_MATRIX,
    AGENT_ROLES,
    UserContext,
    assert_agent_allowed,
    get_current_user,
    require_admin,
    save_agent_roles,
)
from agent_runtime import (
    mcps_for_agent,
    model_for_agent,
    resolve_secret_present,
    secret_refs_for_agent,
    skills_for_agent,
    tools_for_agent,
)
from flow_control import callback_logic_app_hitl, resolve_hitl_reviewers
from flow_engine import (
    apply_hitl_decision,
    continue_after_hitl,
    execute_pre_hitl,
    find_node_id,
    list_flows,
    new_run,
    reset_from_node,
    user_can_approve_hitl,
    user_can_control_run,
    user_can_view_run,
)
from foundry_runtime import invoke_foundry_agent
from workflow_graph import build_graph

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.generated", override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("afa")

app = FastAPI(title="agent-flow-azure", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_RUNS: dict[str, dict[str, Any]] = {}
_CHATS: dict[str, list[dict[str, Any]]] = {}
_HISTORY_DIR = Path(os.getenv("AFA_DATA_DIR", str(ROOT / ".data")))
_HISTORY_FILE = _HISTORY_DIR / "session_history.json"
_HISTORY_HYDRATED = False  # local disk ready (fast path for list/get)
_HISTORY_COSMOS_DONE = False
_HISTORY_COSMOS_LOCK = threading.Lock()
_HISTORY_COSMOS_THREAD: threading.Thread | None = None
_LEARNINGS: list[dict[str, Any]] = []
_FLOW_GRAPH = build_graph("multi-agent-flow")


class ChatHistoryTurn(BaseModel):
    role: str  # user | assistant
    content: str


class ChatRequest(BaseModel):
    message: str
    stream: bool = False
    sessionId: str | None = None
    """Client conversation id — server maps it to Foundry previous_response_id."""
    previousResponseId: str | None = None
    """Optional explicit chain id; usually derived from sessionId server-side."""
    history: list[ChatHistoryTurn] = Field(default_factory=list)
    """Fallback only when no previous_response_id exists yet for this session."""


class FlowRunRequest(BaseModel):
    message: str
    flow_id: str = "multi-agent-flow"
    path_hint: str | None = Field(default=None)
    restart_from_node: str | None = None
    prior_run_id: str | None = None
    hitl_reviewer_oids: list[str] = Field(default_factory=list)
    hitl_reviewer_roles: list[str] = Field(default_factory=list)


class HitlRequest(BaseModel):
    """Human resume payload — continues Logic App webhook + in-app graph."""

    decision: str = Field(description="approve | reject | reroute")
    notes: str = ""
    path_override: str | None = Field(default=None, description="A or B when decision=reroute")
    restart_from_node: str | None = Field(
        default=None, description="On reroute/retry, resume execution from this node"
    )
    input: dict[str, Any] = Field(default_factory=dict, description="Extra structured HITL input")


class FlowControlRequest(BaseModel):
    node_id: str | None = None
    reason: str = ""


class RoleUpdateRequest(BaseModel):
    agentRoles: list[str]
    email: str = ""
    name: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_credential():
    from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

    if os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"):
        return DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None,
        )
    return ChainedTokenCredential(
        AzureCliCredential(),
        DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
            exclude_managed_identity_credential=True,
        ),
    )


_COSMOS_CACHE: tuple[Any, Any, Any] | None = None
_COSMOS_DISABLED = False


def _cosmos():
    """Returns (runs, checkpoints, learnings) containers or (None, None, None)."""
    global _COSMOS_CACHE, _COSMOS_DISABLED
    if _COSMOS_DISABLED:
        return None, None, None
    endpoint = os.getenv("COSMOS_ENDPOINT")
    if not endpoint:
        return None, None, None
    if os.getenv("USE_COSMOS", "1") != "1":
        return None, None, None
    if _COSMOS_CACHE is not None:
        return _COSMOS_CACHE
    try:
        from azure.cosmos import CosmosClient

        client = CosmosClient(endpoint, credential=_local_credential())
        db = client.get_database_client("workflow")
        _COSMOS_CACHE = (
            db.get_container_client("runs"),
            db.get_container_client("checkpoints"),
            db.get_container_client("learnings"),
        )
        return _COSMOS_CACHE
    except Exception as exc:
        logger.warning("Cosmos unavailable: %s", exc)
        _COSMOS_DISABLED = True
        return None, None, None


_LOCAL_PERSIST_TIMER: threading.Timer | None = None
_LOCAL_PERSIST_LOCK = threading.Lock()


def _persist_local_history_now() -> None:
    """Disk fallback so sessions survive backend restart when Cosmos is unavailable."""
    try:
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "runs": list(_RUNS.values())[:300],
            "chats": {oid: cs[:100] for oid, cs in _CHATS.items()},
        }
        tmp = _HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, default=str))
        tmp.replace(_HISTORY_FILE)
    except Exception as exc:
        logger.warning("persist_local_history: %s", exc)


def _persist_local_history() -> None:
    """Debounce disk writes — set_node used to rewrite on every node tick."""
    global _LOCAL_PERSIST_TIMER
    with _LOCAL_PERSIST_LOCK:
        if _LOCAL_PERSIST_TIMER is not None:
            _LOCAL_PERSIST_TIMER.cancel()
        _LOCAL_PERSIST_TIMER = threading.Timer(0.4, _persist_local_history_now)
        _LOCAL_PERSIST_TIMER.daemon = True
        _LOCAL_PERSIST_TIMER.start()


def _hydrate_local_history() -> tuple[int, int]:
    if not _HISTORY_FILE.exists():
        return 0, 0
    try:
        payload = json.loads(_HISTORY_FILE.read_text())
    except Exception as exc:
        logger.warning("hydrate_local_history: %s", exc)
        return 0, 0
    loaded_runs = 0
    loaded_chats = 0
    for item in payload.get("runs") or []:
        rid = item.get("id")
        if rid and rid not in _RUNS:
            _RUNS[rid] = item
            loaded_runs += 1
    for oid, chats in (payload.get("chats") or {}).items():
        bucket = _CHATS.setdefault(oid, [])
        for item in chats or []:
            if not any(c.get("id") == item.get("id") for c in bucket):
                bucket.append(item)
                loaded_chats += 1
    return loaded_runs, loaded_chats


def persist_run(run: dict[str, Any]) -> None:
    oid = run.get("userOid") or "unknown"
    run = {**run, "type": run.get("type") or "flow", "docType": "flow", "userOid": oid}
    _RUNS[run["id"]] = run
    _persist_local_history()
    runs, _, _ = _cosmos()
    if runs is None:
        return
    try:
        # Partition key is /userOid — must always be present.
        runs.upsert_item({**run, "id": run["id"], "userOid": oid})
    except Exception as exc:
        logger.warning("persist_run cosmos: %s", exc)


def persist_chat(chat: dict[str, Any]) -> None:
    oid = chat.get("userOid") or "unknown"
    _CHATS.setdefault(oid, []).insert(0, chat)
    # Cap in-memory list
    _CHATS[oid] = _CHATS[oid][:200]
    _persist_local_history()
    runs, _, _ = _cosmos()
    if runs is None:
        return
    try:
        runs.upsert_item(
            {
                **chat,
                "id": chat.get("id") or str(uuid.uuid4()),
                "userOid": oid,
                "type": "chat",
                "docType": "chat",
            }
        )
    except Exception as exc:
        logger.warning("persist_chat cosmos: %s", exc)


def _ingest_history_items(items: list[dict[str, Any]]) -> tuple[int, int]:
    loaded_runs = 0
    loaded_chats = 0
    for item in items:
        doc_type = item.get("docType") or item.get("type") or (
            "chat" if item.get("agentId") and not item.get("flowId") else "flow"
        )
        if doc_type == "chat":
            oid = item.get("userOid") or "unknown"
            bucket = _CHATS.setdefault(oid, [])
            if not any(c.get("id") == item.get("id") for c in bucket):
                bucket.append(item)
                loaded_chats += 1
        else:
            rid = item.get("id")
            if not rid:
                continue
            existing = _RUNS.get(rid)
            # Prefer newer / richer documents (Cosmos may refresh a local stub).
            if existing is None or (item.get("updatedAt") or "") >= (existing.get("updatedAt") or ""):
                if existing is None:
                    loaded_runs += 1
                _RUNS[rid] = item
    for oid, bucket in _CHATS.items():
        bucket.sort(key=lambda c: c.get("createdAt") or "", reverse=True)
        _CHATS[oid] = bucket[:200]
    return loaded_runs, loaded_chats


def _cosmos_hydrate_background() -> None:
    global _HISTORY_COSMOS_DONE
    with _HISTORY_COSMOS_LOCK:
        if _HISTORY_COSMOS_DONE:
            return
        runs_c, _, _ = _cosmos()
        if runs_c is None:
            _HISTORY_COSMOS_DONE = True
            return
        try:
            items: list[dict[str, Any]] = []
            for item in runs_c.query_items(
                query="SELECT * FROM c",
                enable_cross_partition_query=True,
                max_item_count=50,
            ):
                items.append(item)
                if len(items) >= 200:
                    break
            items.sort(key=lambda c: c.get("createdAt") or "", reverse=True)
            lr, lc = _ingest_history_items(items[:150])
            logger.info("Cosmos history merge: +%s runs, +%s chats", lr, lc)
        except Exception as exc:
            logger.warning("cosmos hydrate bg: %s", exc)
        _HISTORY_COSMOS_DONE = True


def hydrate_history_from_cosmos(*, force: bool = False) -> None:
    """Fast local hydrate, then merge Cosmos in the background (non-blocking)."""
    global _HISTORY_HYDRATED, _HISTORY_COSMOS_DONE, _HISTORY_COSMOS_THREAD
    if force:
        _HISTORY_HYDRATED = False
        _HISTORY_COSMOS_DONE = False
    if not _HISTORY_HYDRATED:
        lr, lc = _hydrate_local_history()
        _HISTORY_HYDRATED = True
        logger.info("Local history ready: %s runs, %s chats", lr, lc)
    if _HISTORY_COSMOS_DONE:
        return
    if _HISTORY_COSMOS_THREAD and _HISTORY_COSMOS_THREAD.is_alive():
        return
    _HISTORY_COSMOS_THREAD = threading.Thread(
        target=_cosmos_hydrate_background,
        name="afa-cosmos-hydrate",
        daemon=True,
    )
    _HISTORY_COSMOS_THREAD.start()


def persist_checkpoint(run_id: str, node_id: str, status: str, data: Any) -> None:
    _, checkpoints, _ = _cosmos()
    if checkpoints is None:
        return
    try:
        checkpoints.upsert_item(
            {
                "id": f"{run_id}:{node_id}",
                "runId": run_id,
                "nodeId": node_id,
                "status": status,
                "data": data,
                "updatedAt": _now(),
            }
        )
    except Exception as exc:
        logger.warning("persist_checkpoint: %s", exc)


def persist_learning(item: dict[str, Any]) -> None:
    _LEARNINGS.insert(0, item)
    _, _, learnings = _cosmos()
    if learnings is None:
        return
    try:
        learnings.upsert_item(item)
    except Exception as exc:
        logger.warning("persist_learning: %s", exc)


def _normalize_agent_response(
    agent_id: str,
    body: dict[str, Any],
    correlation_id: str,
    *,
    usage: dict | None = None,
    guardrails: dict | None = None,
    model: dict | None = None,
) -> dict[str, Any]:
    """Normalize Foundry/APIM payload into a stable chat shape with trace chips."""
    reply = (
        body.get("reply")
        or body.get("output_markdown")
        or body.get("content")
        or body.get("message")
        or body.get("text")
        or ""
    )
    if isinstance(reply, dict):
        # Prefer nested text; never stringify a full chat.completion object as the reply
        reply = (
            reply.get("text")
            or reply.get("content")
            or reply.get("reply")
            or ((reply.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
    if not isinstance(reply, str):
        reply = str(reply) if reply is not None else ""
    reply = reply.strip()
    if not reply:
        # Last resort: short note — do not dump raw provider payloads into the UI
        out = body.get("output")
        if isinstance(out, str) and out.strip() and not out.strip().startswith("{"):
            reply = out.strip()
        else:
            reply = "No visible model reply was returned."
    model = model or model_for_agent(agent_id)
    skills = body.get("skills") or skills_for_agent(agent_id)
    mcps = body.get("mcps") or body.get("mcps_used") or mcps_for_agent(agent_id)
    tools = body.get("tools") or body.get("tools_used") or tools_for_agent(agent_id)
    secrets = [
        {**s, "present": resolve_secret_present(s["name"])}
        for s in secret_refs_for_agent(agent_id)
    ]
    trace = body.get("trace") or [
        {"step": "backend_acl", "status": "ok", "correlationId": correlation_id},
        {"step": "foundry_project", "agent": agent_id, "model": model.get("deployment"), "status": "ok"},
    ]
    return {
        "agent": agent_id,
        "harness": body.get("harness") or model.get("harness") or "default_prompt",
        "reply": reply if isinstance(reply, str) else str(reply),
        "output_markdown": body.get("output_markdown") or (reply if isinstance(reply, str) else str(reply)),
        "recommended_path": body.get("recommended_path"),
        "trace": trace,
        "skills": skills,
        "mcps": mcps,
        "tools": tools,
        "toolExecutions": body.get("toolExecutions") or [],
        "secrets": secrets,
        "model": model,
        "usage": usage,
        "guardrails": guardrails,
        "correlationId": correlation_id,
        "createdAt": _now(),
        "memory": body.get("memory") or body.get("memories") or [],
        "responseId": body.get("responseId"),
        "previousResponseId": body.get("previousResponseId"),
        "sessionId": body.get("sessionId"),
    }


async def invoke_agent_upstream(
    user: UserContext,
    agent_id: str,
    message: str,
    meta: dict | None = None,
    request: Request | None = None,
) -> dict:
    """Invoke a real Foundry Prompt Agent (tools/MCP/skills run inside Foundry).

    Prefer APIM gateway when KEYCLOAK_PUBLIC_URL is reachable; otherwise call the
    Foundry project Responses API with Azure CLI / MI credentials.
    """
    assert_agent_allowed(user, agent_id)
    gateway = (os.getenv("APIM_GATEWAY_URL") or "").rstrip("/")
    correlation_id = (
        (request.headers.get("X-Correlation-Id") if request else None)
        or (meta or {}).get("correlationId")
        or str(uuid.uuid4())
    )
    # Prefer gateway unless explicitly forced to project SDK (local Keycloak JWKS unreachable).
    use_gateway = bool(gateway) and os.getenv("FOUNDRY_DIRECT_MI", "0") != "1"

    if use_gateway:
        payload = {
            "message": message,
            "userOid": user.oid,
            **{
                k: v
                for k, v in (meta or {}).items()
                if not k.startswith("_") and k != "history" and v is not None
            },
        }
        if (meta or {}).get("history"):
            payload["history"] = meta["history"]
        headers = {
            "Authorization": f"Bearer {user.raw_token}",
            "X-User-Id": user.oid,
            "X-User-Roles": ",".join(user.roles),
            "X-Correlation-Id": correlation_id,
            "Content-Type": "application/json",
        }
        if request and request.headers.get("traceparent"):
            headers["traceparent"] = request.headers["traceparent"]
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{gateway}/api/agents/{agent_id}/chat", json=payload, headers=headers
            )
            if resp.status_code == 403:
                raise HTTPException(status_code=403, detail=resp.json() if resp.content else {"error": "Forbidden"})
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "Upstream APIM → Foundry agent call failed",
                        "status": resp.status_code,
                        "body": resp.text[:2000],
                        "correlationId": correlation_id,
                        "agent": agent_id,
                        "hint": "Set KEYCLOAK_PUBLIC_URL for APIM JWKS, or FOUNDRY_DIRECT_MI=1 for local project SDK path. See setup.md.",
                    },
                )
            try:
                body = resp.json()
            except Exception:
                body = {"reply": resp.text}
            result = _normalize_agent_response(
                agent_id, body if isinstance(body, dict) else {"reply": str(body)}, correlation_id
            )
    else:
        if not os.getenv("FOUNDRY_PROJECT_ENDPOINT"):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "FOUNDRY_PROJECT_ENDPOINT required (…/api/projects/<name>). See setup.md.",
                },
            )
        try:
            body = await invoke_foundry_agent(
                agent_id,
                message,
                correlation_id=correlation_id,
                user_oid=user.oid,
                user_email=user.email or "",
                roles=list(user.roles),
                history=(meta or {}).get("history") or [],
                session_id=(meta or {}).get("sessionId"),
                previous_response_id=(meta or {}).get("previousResponseId"),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "Foundry project agent invoke failed", "detail": str(exc), "correlationId": correlation_id},
            ) from exc
        if body.get("blocked"):
            raise HTTPException(status_code=403, detail=body)
        result = _normalize_agent_response(
            agent_id,
            body,
            correlation_id,
            usage=body.get("usage"),
            guardrails=body.get("guardrails"),
            model=body.get("model"),
        )

    for mem in result.get("memory") or []:
        if isinstance(mem, dict):
            persist_learning(
                {
                    "id": mem.get("id") or str(uuid.uuid4()),
                    "userOid": user.oid,
                    "agent": agent_id,
                    "functionality": mem.get("functionality")
                    or mem.get("category")
                    or mem.get("type")
                    or "memory",
                    "learning": mem.get("learning") or mem.get("content") or mem.get("text") or str(mem),
                    "date": mem.get("updatedAt") or mem.get("createdAt") or _now(),
                    "correlationId": correlation_id,
                }
            )
    return result


def _logic_app_trigger_url(flow_id: str | None = None) -> str:
    """Resolve per-flow SAS URL; falls back to legacy LOGIC_APP_TRIGGER_URL."""
    import json

    if flow_id:
        try:
            from azure_sync import synced_trigger_url

            synced = synced_trigger_url(flow_id)
            if synced:
                return synced.strip()
        except Exception:
            pass
    raw_map = (os.getenv("LOGIC_APP_TRIGGER_URLS") or "").strip()
    if raw_map:
        try:
            mapping = json.loads(raw_map)
            if flow_id and isinstance(mapping, dict) and mapping.get(flow_id):
                return str(mapping[flow_id]).strip()
        except Exception:
            logger.warning("LOGIC_APP_TRIGGER_URLS is not valid JSON")
    if flow_id:
        env_key = "LOGIC_APP_TRIGGER_URL_" + flow_id.replace("-", "_").upper()
        specific = (os.getenv(env_key) or "").strip()
        if specific:
            return specific
    return (os.getenv("LOGIC_APP_TRIGGER_URL") or "").strip()


async def trigger_logic_app(user: UserContext, run: dict[str, Any]) -> dict[str, Any] | None:
    """Fire Logic Apps Consumption HTTP Request trigger (SAS URL in LOGIC_APP_TRIGGER_URL).

    The SAS query string authenticates the trigger. Authorization carries the caller's
    Keycloak token so downstream APIM agent actions can forward it.
    """
    flow_id = run.get("flowId")
    trigger_url = _logic_app_trigger_url(flow_id)
    if not trigger_url:
        if os.getenv("LOGIC_APP_NAME") or os.getenv("LOGIC_APP_TRIGGER_URLS"):
            logger.warning(
                "No Logic App trigger URL for flow=%s — Azure run history will stay empty for this flow. "
                "Re-run scripts/install.sh.",
                flow_id,
            )
        return None
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": user.oid,
        "X-User-Roles": ",".join(user.roles),
        "X-Correlation-Id": run.get("correlationId", str(uuid.uuid4())),
    }
    # Forward the user JWT for APIM — do not use Azure management tokens on the SAS URL.
    if user.raw_token:
        headers["Authorization"] = f"Bearer {user.raw_token}"
    notify = os.getenv("HITL_NOTIFY_URL") or (
        f"{os.getenv('PUBLIC_API_URL', 'http://127.0.0.1:5749').rstrip('/')}/api/flows/{run['id']}/hitl-ready"
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            trigger_url,
            json={
                "runId": run["id"],
                "userOid": user.oid,
                "roles": ",".join(user.roles),
                "message": run["message"],
                "correlationId": run.get("correlationId"),
                "path_hint": run.get("path"),
                "hitlNotifyUrl": notify,
                "hitlReviewerOids": run.get("hitlReviewerOids") or [],
                "hitlReviewerRoles": run.get("hitlReviewerRoles") or [],
            },
            headers=headers,
        )
        if resp.status_code >= 400:
            logger.warning("Logic App trigger failed: %s %s", resp.status_code, resp.text[:500])
            return {"status": resp.status_code, "error": resp.text[:500]}
        logger.info("Logic App triggered for run %s → HTTP %s", run.get("id"), resp.status_code)
        try:
            body = resp.json()
            if isinstance(body, dict):
                return {"status": resp.status_code, **body}
            return {"status": resp.status_code, "body": body}
        except Exception:
            return {"status": resp.status_code}


def _require_run(run_id: str) -> dict[str, Any]:
    hydrate_history_from_cosmos()
    run = _RUNS.get(run_id)
    if not run:
        # One more local pass in case hydrate raced with the first request.
        _hydrate_local_history()
        run = _RUNS.get(run_id)
    if not run:
        runs_c, _, _ = _cosmos()
        if runs_c is not None:
            try:
                items = list(
                    runs_c.query_items(
                        query="SELECT * FROM c WHERE c.id = @id",
                        parameters=[{"name": "@id", "value": run_id}],
                        enable_cross_partition_query=True,
                    )
                )
                if items:
                    run = items[0]
                    _RUNS[run_id] = run
            except Exception as exc:
                logger.warning("cosmos get run %s: %s", run_id, exc)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _make_set_node(run: dict[str, Any]):
    run_id = run["id"]

    async def set_node(node_id: str, **kwargs):
        for n in run["nodes"]:
            if n["id"] == node_id:
                status = kwargs.get("status")
                # In-progress: start the clock, clear any premature end (HITL wait used to set 0s).
                if status in ("Running", "Waiting_HITL", "Paused"):
                    if not n.get("startedAt"):
                        n["startedAt"] = _now()
                    n.pop("endedAt", None)
                    n.pop("durationMs", None)
                elif status in ("Succeeded", "Failed", "Skipped", "Cancelled"):
                    if not n.get("startedAt"):
                        n["startedAt"] = _now()
                    n["endedAt"] = _now()
                    try:
                        s = datetime.fromisoformat(str(n["startedAt"]).replace("Z", "+00:00"))
                        e = datetime.fromisoformat(str(n["endedAt"]).replace("Z", "+00:00"))
                        n["durationMs"] = max(0, int((e - s).total_seconds() * 1000))
                    except Exception:
                        n["durationMs"] = n.get("durationMs") or 0
                # Attach policy marker when guardrails blocked this node
                gr = kwargs.get("guardrails") or n.get("guardrails") or {}
                if status == "Failed" and isinstance(gr, dict) and gr.get("blocked"):
                    kwargs.setdefault(
                        "policy",
                        {
                            "gateway": "blocked",
                            "reason": "Guardrail blocked",
                            "correlationId": run.get("correlationId"),
                        },
                    )
                n.update(kwargs)
                # Keep in-progress timing clean even if kwargs carried stale endedAt.
                if status in ("Running", "Waiting_HITL", "Paused"):
                    n.pop("endedAt", None)
                    n.pop("durationMs", None)
                persist_checkpoint(run_id, node_id, n.get("status", "Running"), n)
                break
        run["updatedAt"] = _now()
        persist_run(run)

    return set_node


@app.on_event("startup")
def configure_telemetry() -> None:
    hydrate_history_from_cosmos()
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        logger.info("App Insights not configured — skipping OTel setup")
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        configure_azure_monitor(connection_string=conn)
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        logger.info("Azure Monitor OpenTelemetry configured")
    except Exception as exc:
        logger.warning("Failed to configure App Insights: %s", exc)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "auth": "keycloak",
        "keycloak": os.getenv("KEYCLOAK_URL", "http://localhost:5750"),
        "apim": os.getenv("APIM_GATEWAY_URL") or None,
        "cosmos": bool(os.getenv("COSMOS_ENDPOINT")),
        "appInsights": bool(os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")),
    }


@app.get("/api/config/public")
def public_config():
    """Non-secret config for the SPA (Keycloak + App Insights)."""
    agents = sorted({n["agent"] for n in _FLOW_GRAPH["nodes"] if n.get("agent")})
    return {
        "keycloakUrl": os.getenv("KEYCLOAK_URL", "http://localhost:5750"),
        "keycloakRealm": os.getenv("KEYCLOAK_REALM", "agent-flow"),
        "keycloakClientId": os.getenv("KEYCLOAK_CLIENT_ID", "afa-web"),
        "keycloakAudience": os.getenv("KEYCLOAK_API_AUDIENCE", "afa-api"),
        "appInsightsConnectionString": os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or None,
        "agents": agents,
        "defaultModel": os.getenv("FOUNDRY_MODEL_DEPLOYMENT") or os.getenv("FOUNDRY_MODEL_NAME"),
        "workflowSource": _FLOW_GRAPH.get("source"),
    }


@app.get("/api/flows")
def catalog_flows(user: UserContext = Depends(get_current_user)):
    """List demonstrable Logic App flows (catalog + Azure sync cache)."""
    from azure_sync import load_sync_cache

    cache = load_sync_cache()
    return {
        "flows": list_flows(),
        "syncedAt": cache.get("syncedAt"),
    }


@app.get("/api/agents")
def list_agents_api(user: UserContext = Depends(get_current_user)):
    """Agents available for chat (local definitions ∪ Azure Foundry sync)."""
    from azure_sync import list_known_agents, load_sync_cache, synced_agents

    cache = load_sync_cache()
    known = list_known_agents()
    details = {a["id"]: a for a in synced_agents() if a.get("id")}
    agents = [
        details.get(aid)
        or {"id": aid, "name": aid, "source": "local", "kind": "prompt"}
        for aid in known
    ]
    allowed = user.allowed_agents()
    return {
        "agents": agents,
        "allowed": {a["id"]: a["id"] in allowed for a in agents},
        "syncedAt": cache.get("syncedAt"),
    }


@app.post("/api/sync/azure")
def sync_azure_catalog(user: UserContext = Depends(get_current_user)):
    """Pull Foundry agents + Logic Apps from the configured Azure subscription/RG."""
    from azure_sync import sync_from_azure

    try:
        result = sync_from_azure()
    except Exception as exc:
        logger.exception("sync_azure_catalog")
        raise HTTPException(status_code=502, detail=f"Azure sync failed: {exc}") from exc
    return {
        "ok": not bool(result.get("errors")),
        "syncedAt": result.get("syncedAt"),
        "agents": result.get("agents") or [],
        "flows": list_flows(),
        "agentCount": len(result.get("agents") or []),
        "flowCount": len(result.get("flows") or []),
        "errors": result.get("errors") or [],
        "resourceGroup": result.get("resourceGroup"),
    }


@app.get("/api/flows/{flow_id}/graph")
def flow_graph(flow_id: str, user: UserContext = Depends(get_current_user)):
    """Visual DAG derived from Logic Apps workflow.json for a catalog flow."""
    try:
        return build_graph(flow_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/flows/multi-agent-flow/graph")
def flow_graph_legacy(user: UserContext = Depends(get_current_user)):
    return build_graph("multi-agent-flow")


@app.get("/api/me")
def me(user: UserContext = Depends(get_current_user)):
    from azure_sync import list_known_agents, load_sync_cache

    cache = load_sync_cache()
    return {
        "oid": user.oid,
        "name": user.name,
        "email": user.email,
        "roles": user.roles,
        "appRoles": user.app_roles,
        "agentRoles": user.agent_roles,
        "matrix": user.permission_matrix(),
        "isAdmin": user.is_admin(),
        "flows": list_flows(),
        "agents": list_known_agents(),
        "syncedAt": cache.get("syncedAt"),
    }


@app.get("/api/acl")
def acl_matrix(_: UserContext = Depends(get_current_user)):
    return {"acl_matrix": ACL_MATRIX, "agentRoles": sorted(AGENT_ROLES)}


def _aggregate_chat_sessions(chats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One sidebar entry per conversation session (not per turn)."""
    by_session: dict[str, dict[str, Any]] = {}
    for c in chats:
        sid = c.get("sessionId") or c.get("id")
        if not sid:
            continue
        turn = {
            "id": c.get("id"),
            "role": "pair",
            "message": c.get("message") or "",
            "reply": (c.get("result") or {}).get("reply")
            or (c.get("result") or {}).get("output_markdown")
            or "",
            "result": c.get("result"),
            "createdAt": c.get("createdAt"),
            "agentId": c.get("agentId"),
        }
        existing = by_session.get(sid)
        if not existing:
            by_session[sid] = {
                "id": sid,
                "sessionId": sid,
                "agentId": c.get("agentId"),
                "userOid": c.get("userOid"),
                "userName": c.get("userName"),
                "title": (c.get("message") or "Chat")[:80],
                "preview": (c.get("message") or "")[:120],
                "createdAt": c.get("createdAt"),
                "updatedAt": c.get("completedAt") or c.get("createdAt"),
                "messageCount": 1,
                "turns": [turn],
                # Compat fields for older UI that expected a single chat item
                "message": c.get("message"),
                "result": c.get("result"),
                "skills": c.get("skills"),
                "mcps": c.get("mcps"),
                "tools": c.get("tools"),
            }
        else:
            existing["turns"].append(turn)
            existing["messageCount"] = len(existing["turns"])
            existing["updatedAt"] = c.get("completedAt") or c.get("createdAt") or existing["updatedAt"]
            existing["preview"] = (c.get("message") or existing["preview"])[:120]
            existing["agentId"] = c.get("agentId") or existing["agentId"]
            existing["message"] = c.get("message")
            existing["result"] = c.get("result")
            existing["skills"] = c.get("skills")
            existing["mcps"] = c.get("mcps")
            existing["tools"] = c.get("tools")
    sessions = list(by_session.values())
    # turns were appended newest-first if chats list is newest-first — normalize oldest→newest
    for s in sessions:
        s["turns"] = sorted(s["turns"], key=lambda t: t.get("createdAt") or "")
        if s["turns"]:
            s["title"] = (s["turns"][0].get("message") or s["title"])[:80]
            s["createdAt"] = s["turns"][0].get("createdAt") or s.get("createdAt")
    sessions.sort(key=lambda s: s.get("updatedAt") or s.get("createdAt") or "", reverse=True)
    return sessions


@app.post("/api/agents/{agent_id}/chat")
async def chat_agent(
    agent_id: str,
    body: ChatRequest,
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    started = _now()
    session_id = (body.sessionId or "").strip() or str(uuid.uuid4())
    # Prefer server-side chain for this session; client previousResponseId is optional override.
    from foundry_runtime import get_session_previous_response_id

    prev = (body.previousResponseId or "").strip() or get_session_previous_response_id(session_id)
    history = [{"role": t.role, "content": t.content} for t in (body.history or []) if t.content]
    result = await invoke_agent_upstream(
        user,
        agent_id,
        body.message,
        meta={
            "sessionId": session_id,
            "previousResponseId": prev,
            # History only used if no previous_response_id yet (first turn / cold start)
            "history": history if not prev else [],
        },
        request=request,
    )
    chat = {
        "id": str(uuid.uuid4()),
        "type": "chat",
        "sessionId": session_id,
        "agentId": agent_id,
        "userOid": user.oid,
        "userName": user.name,
        "message": body.message,
        "input": {"message": body.message},
        "result": result,
        "createdAt": started,
        "completedAt": _now(),
        "correlationId": result.get("correlationId"),
        "responseId": result.get("responseId"),
        "skills": result.get("skills"),
        "mcps": result.get("mcps"),
        "tools": result.get("tools"),
    }
    persist_chat(chat)
    return {
        **result,
        "input": body.message,
        "createdAt": started,
        "sessionId": session_id,
        "chatId": chat["id"],
        "responseId": result.get("responseId"),
        "previousResponseId": prev,
    }


@app.post("/api/flows/{flow_id}/run")
@app.post("/api/flows/multi-agent-flow/run")
async def run_flow(
    request: Request,
    user: UserContext = Depends(get_current_user),
    body: FlowRunRequest | None = None,
    flow_id: str | None = None,
):
    if body is None:
        raise HTTPException(status_code=400, detail="Body required")
    fid = flow_id or body.flow_id or "multi-agent-flow"
    # path from legacy URL
    if request.url.path.rstrip("/").endswith("/multi-agent-flow/run"):
        fid = body.flow_id or "multi-agent-flow"

    run_id = str(uuid.uuid4())
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    path_hint = body.path_hint
    if not path_hint and fid == "multi-agent-flow":
        path_hint = "B" if any(k in body.message.lower() for k in ("cost", "finops", "spend")) else "A"

    run = new_run(
        flow_id=fid,
        user=user,
        message=body.message,
        correlation_id=correlation_id,
        path_hint=path_hint,
        hitl_oids=body.hitl_reviewer_oids,
        hitl_roles=body.hitl_reviewer_roles,
    )
    run["id"] = run_id

    from_node = None
    if body.restart_from_node and body.prior_run_id:
        prior = _RUNS.get(body.prior_run_id)
        if prior and prior.get("flowId") == fid:
            for n in run["nodes"]:
                src = next((x for x in prior["nodes"] if x["id"] == n["id"]), None)
                if src and n["id"] != body.restart_from_node:
                    for k in (
                        "status",
                        "input",
                        "output",
                        "skills",
                        "mcps",
                        "tools",
                        "toolExecutions",
                        "policy",
                        "startedAt",
                        "endedAt",
                        "durationMs",
                        "usage",
                        "guardrails",
                        "model",
                    ):
                        if k in src:
                            n[k] = src[k]
            from_node = body.restart_from_node
            reset_from_node(run, from_node)

    persist_run(run)
    la = await trigger_logic_app(user, run)
    if la:
        run["logicApp"] = la
        persist_run(run)

    # Return immediately so the UI can show the graph + poll live node status.
    import asyncio

    async def _bg_execute() -> None:
        try:
            set_node = _make_set_node(run)
            await execute_pre_hitl(
                run,
                user,
                invoke=invoke_agent_upstream,
                set_node=set_node,
                request=None,
                from_node=from_node,
            )
        except Exception as exc:
            logger.exception("background flow execute failed: %s", exc)
            run["status"] = "Failed"
            run["error"] = str(exc)
        finally:
            persist_run(run)

    asyncio.create_task(_bg_execute())
    return run


@app.get("/api/flows/runs")
def list_runs(all_users: bool = False, user: UserContext = Depends(get_current_user)):
    # Local disk first (ms); Cosmos merges in a background thread.
    hydrate_history_from_cosmos()
    items = [r for r in _RUNS.values() if (r.get("docType") or r.get("type") or "flow") != "chat"]
    if all_users and user.is_admin():
        pass
    else:
        items = [r for r in items if user_can_view_run(user, r)]
    items.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
    if all_users and user.is_admin():
        chats = [c for cs in _CHATS.values() for c in cs]
    else:
        chats = list(_CHATS.get(user.oid, []))
        # Username/email aliases: older runs may be keyed by a previous claim fallback.
        if not chats and user.email:
            chats = list(_CHATS.get(user.email, []))
        if not chats and user.name:
            chats = list(_CHATS.get(user.name, []))
    sessions = _aggregate_chat_sessions(chats)
    return {
        "runs": items[:100],
        "chats": sessions,
        "chatSessions": sessions,
        "cosmosPending": not _HISTORY_COSMOS_DONE,
    }


@app.get("/api/flows/runs/{run_id}")
def get_run(run_id: str, user: UserContext = Depends(get_current_user)):
    run = _require_run(run_id)
    if not user_can_view_run(user, run):
        raise HTTPException(status_code=403, detail="Forbidden — not owner or HITL reviewer")
    run["viewerCanApproveHitl"] = user_can_approve_hitl(user, run)
    run["viewerCanControl"] = user_can_control_run(user, run)
    return run


@app.post("/api/flows/{run_id}/hitl-ready")
async def hitl_ready(run_id: str, request: Request):
    """Logic Apps HttpWebhook subscribe callback — stores callbackUrl for in-app resume."""
    run = _RUNS.get(run_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if run:
        run["hitl"] = {
            **(run.get("hitl") or {}),
            "callbackUrl": body.get("callbackUrl"),
            "readyAt": _now(),
            "notify": body,
        }
        if body.get("hitlReviewerOids"):
            run["hitlReviewerOids"] = body["hitlReviewerOids"]
        if body.get("hitlReviewerRoles"):
            run["hitlReviewerRoles"] = body["hitlReviewerRoles"]
        persist_run(run)
    return {"ok": True, "runId": run_id}


@app.post("/api/flows/{run_id}/hitl-unsubscribe")
async def hitl_unsubscribe(run_id: str):
    run = _RUNS.get(run_id)
    if run and run.get("hitl"):
        run["hitl"]["unsubscribedAt"] = _now()
        persist_run(run)
    return {"ok": True}


@app.post("/api/flows/{run_id}/approve")
async def approve_hitl(
    run_id: str,
    body: HitlRequest,
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    import asyncio

    run = _require_run(run_id)
    if run["status"] != "Waiting_HITL":
        raise HTTPException(status_code=409, detail=f"Run status is {run['status']}")
    decision = (body.decision or "").strip().lower()
    if decision not in ("approve", "reject", "reroute"):
        raise HTTPException(status_code=400, detail="decision must be approve|reject|reroute")

    set_node = _make_set_node(run)
    # Logic App webhook callback (best-effort; does not block UI state)
    cb = await callback_logic_app_hitl(
        run,
        decision=decision,
        notes=body.notes,
        approver_oid=user.oid,
        path_override=body.path_override,
        restart_from_node=body.restart_from_node,
        extra_input=body.input,
    )
    # Clear Waiting_HITL immediately so the canvas updates before synthesize/reroute finishes.
    await apply_hitl_decision(
        run,
        user,
        decision=decision,
        notes=body.notes,
        set_node=set_node,
        path_override=body.path_override,
        restart_from_node=body.restart_from_node,
        extra_input=body.input,
        callback_result=cb,
    )
    persist_run(run)

    if decision in ("approve", "reroute"):
        async def _bg_post_hitl() -> None:
            try:
                await continue_after_hitl(
                    run,
                    user,
                    decision=decision,
                    notes=body.notes,
                    invoke=invoke_agent_upstream,
                    set_node=set_node,
                    request=None,
                    path_override=body.path_override,
                    restart_from_node=body.restart_from_node,
                    extra_input=body.input,
                )
            except Exception as exc:
                logger.exception("post-HITL continuation failed: %s", exc)
                run["status"] = "Failed"
                run["error"] = str(exc)
            finally:
                persist_run(run)

        asyncio.create_task(_bg_post_hitl())

    return run


@app.post("/api/flows/{run_id}/cancel")
@app.post("/api/flows/{run_id}/stop")
def cancel_flow(
    run_id: str,
    user: UserContext = Depends(get_current_user),
    body: FlowControlRequest | None = None,
):
    run = _require_run(run_id)
    if not user_can_control_run(user, run):
        raise HTTPException(status_code=403, detail="Only owner or App.Admin can stop")
    run["status"] = "Cancelled"
    run["cancelled"] = True
    run["paused"] = False
    reason = (body.reason if body else "") or "stopped from app"
    target = body.node_id if body else None
    for n in run["nodes"]:
        if target and n["id"] != target and n["status"] not in ("Pending", "Running", "Waiting_HITL"):
            continue
        if n["status"] in ("Pending", "Running", "Waiting_HITL", "Paused") or (target and n["id"] == target):
            n["status"] = "Cancelled"
            n["endedAt"] = _now()
            n["output"] = {**(n.get("output") or {}), "stopReason": reason}
    persist_run(run)
    return run


@app.post("/api/flows/{run_id}/pause")
def pause_flow(
    run_id: str,
    user: UserContext = Depends(get_current_user),
    body: FlowControlRequest | None = None,
):
    run = _require_run(run_id)
    if not user_can_control_run(user, run):
        raise HTTPException(status_code=403, detail="Only owner or App.Admin can pause")
    run["paused"] = True
    if run["status"] == "Running":
        run["status"] = "Paused"
    if body and body.node_id:
        for n in run["nodes"]:
            if n["id"] == body.node_id and n["status"] == "Running":
                n["status"] = "Paused"
                n["output"] = {**(n.get("output") or {}), "pausedReason": body.reason}
    persist_run(run)
    return run


@app.post("/api/flows/{run_id}/resume")
async def resume_flow(
    run_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
    body: FlowControlRequest | None = None,
):
    run = _require_run(run_id)
    if not user_can_control_run(user, run):
        raise HTTPException(status_code=403, detail="Only owner or App.Admin can resume")
    if run["status"] == "Waiting_HITL":
        raise HTTPException(status_code=409, detail="Use HITL approve/reject/reroute to continue")
    run["paused"] = False
    run["cancelled"] = False
    from_node = body.node_id if body else None
    if run["status"] in ("Paused", "Cancelled") or from_node:
        run["status"] = "Running"
        set_node = _make_set_node(run)
        await execute_pre_hitl(
            run,
            user,
            invoke=invoke_agent_upstream,
            set_node=set_node,
            request=request,
            from_node=from_node,
        )
    persist_run(run)
    return run


@app.post("/api/flows/{run_id}/retry")
async def retry_flow(
    run_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
    body: FlowControlRequest | None = None,
):
    """Retry entire workflow or from a specific node."""
    prior = _require_run(run_id)
    if not user_can_control_run(user, prior):
        raise HTTPException(status_code=403, detail="Only owner or App.Admin can retry")
    node_id = (body.node_id if body else None) or "Init_State"
    req = FlowRunRequest(
        message=prior["message"],
        flow_id=prior.get("flowId") or "multi-agent-flow",
        restart_from_node=node_id,
        prior_run_id=run_id,
        path_hint=prior.get("path"),
        hitl_reviewer_oids=prior.get("hitlReviewerOids") or [],
        hitl_reviewer_roles=prior.get("hitlReviewerRoles") or [],
    )
    return await run_flow(request=request, user=user, body=req, flow_id=req.flow_id)


@app.post("/api/flows/{run_id}/restart")
async def restart_from_node(
    run_id: str,
    request: Request,
    node_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    return await retry_flow(
        run_id, request, user, FlowControlRequest(node_id=node_id, reason="restart from node")
    )


@app.get("/api/learning")
def list_learning(
    agentId: str | None = None,
    user: UserContext = Depends(get_current_user),
):
    items = [x for x in _LEARNINGS if x.get("userOid") == user.oid or user.is_admin()]
    if agentId:
        items = [x for x in items if x.get("agent") == agentId]
    # Also pull from Cosmos
    _, _, learnings = _cosmos()
    if learnings is not None:
        try:
            query = "SELECT * FROM c WHERE c.userOid = @oid"
            params = [{"name": "@oid", "value": user.oid}]
            if user.is_admin() and not agentId:
                query = "SELECT * FROM c"
                params = []
            for doc in learnings.query_items(query=query, parameters=params, enable_cross_partition_query=True):
                if agentId and doc.get("agent") != agentId:
                    continue
                if not any(i.get("id") == doc.get("id") for i in items):
                    items.append(doc)
        except Exception as exc:
            logger.warning("learning query: %s", exc)
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return {"learnings": items}


@app.get("/api/admin/users")
def admin_list_users(user: UserContext = Depends(require_admin)):
    """List known users from Cosmos role store (+ current user)."""
    users: dict[str, dict[str, Any]] = {
        user.oid: {
            "oid": user.oid,
            "email": user.email,
            "name": user.name,
            "appRoles": user.app_roles,
            "agentRoles": user.agent_roles,
        }
    }
    endpoint = os.getenv("COSMOS_ENDPOINT")
    if endpoint:
        try:
            from azure.cosmos import CosmosClient

            client = CosmosClient(endpoint, credential=_local_credential())
            container = client.get_database_client("workflow").get_container_client("userRoles")
            for doc in container.query_items(query="SELECT * FROM c", enable_cross_partition_query=True):
                oid = doc.get("userOid") or doc.get("id")
                users[oid] = {
                    "oid": oid,
                    "email": doc.get("email", ""),
                    "name": doc.get("name", ""),
                    "appRoles": doc.get("appRoles") or ["App.User"],
                    "agentRoles": doc.get("agentRoles") or [],
                }
        except Exception as exc:
            logger.warning("admin_list_users: %s", exc)
    return {"users": list(users.values()), "assignableAgentRoles": sorted(AGENT_ROLES)}


@app.put("/api/admin/users/{oid}/roles")
def admin_set_roles(oid: str, body: RoleUpdateRequest, user: UserContext = Depends(require_admin)):
    invalid = [r for r in body.agentRoles if r not in AGENT_ROLES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid agent roles: {invalid}")
    doc = save_agent_roles(oid, body.agentRoles, email=body.email, name=body.name)
    return doc


@app.get("/api/registry/skills")
def registry_skills(user: UserContext = Depends(get_current_user)):
    catalog = ROOT / "agents" / "registry" / "catalog.generated.json"
    if catalog.exists():
        import json

        data = json.loads(catalog.read_text())
    else:
        data = {
            "skills": [{"name": p.parent.name} for p in (ROOT / "agents" / "skills").glob("*/SKILL.md")],
            "mcps": [],
            "tools": [],
        }
    matrix = user.permission_matrix()
    if "*" not in matrix["skills"]:
        data["skills"] = [s for s in data.get("skills", []) if s.get("name") in matrix["skills"] or not matrix["skills"]]
    return data
