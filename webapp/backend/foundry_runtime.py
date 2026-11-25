"""Real Microsoft Foundry project client — agents via Responses API (no chat/completions simulation)."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from typing import Any

from agent_runtime import evaluate_guardrails, model_for_agent, skills_for_agent

logger = logging.getLogger("afa.foundry")

# sessionId -> last Foundry response id (playground-style turn chaining)
_SESSION_PREVIOUS_RESPONSE: dict[str, str] = {}

# Warm clients — recreating AIProjectClient + AzureCliCredential every turn is multi-second.
_lock = threading.Lock()
_cached_credential: Any = None
_project_client: Any = None
_openai_clients: dict[str, Any] = {}


def _credential():
    global _cached_credential
    if _cached_credential is not None:
        return _cached_credential
    from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

    if os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"):
        _cached_credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None,
        )
    else:
        _cached_credential = ChainedTokenCredential(
            AzureCliCredential(),
            DefaultAzureCredential(
                exclude_interactive_browser_credential=True,
                exclude_managed_identity_credential=True,
            ),
        )
    return _cached_credential


def project_endpoint() -> str:
    ep = (os.getenv("FOUNDRY_PROJECT_ENDPOINT") or "").rstrip("/")
    if not ep or "/api/projects/" not in ep:
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT must be a Foundry project URL "
            "(…/api/projects/<name>). See setup.md — provision a real project first."
        )
    return ep


def _get_openai_client(agent_id: str):
    """Reuse project + OpenAI clients across turns (matches playground warm session)."""
    global _project_client
    with _lock:
        if _project_client is None:
            from azure.ai.projects import AIProjectClient

            _project_client = AIProjectClient(
                endpoint=project_endpoint(),
                credential=_credential(),
                allow_preview=True,
            )
        client = _openai_clients.get(agent_id)
        if client is None:
            client = _project_client.get_openai_client(agent_name=agent_id)
            _openai_clients[agent_id] = client
        return client


def clear_session_response(session_id: str | None) -> None:
    if session_id:
        _SESSION_PREVIOUS_RESPONSE.pop(session_id, None)


def get_session_previous_response_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    return _SESSION_PREVIOUS_RESPONSE.get(session_id)


def _item_get(item: Any, key: str, default: Any = None) -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _extract_text(response: Any) -> str:
    if response is None:
        return ""
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        item_type = _item_get(item, "type")
        if item_type == "message":
            content = _item_get(item, "content") or []
            for block in content:
                btype = _item_get(block, "type")
                if btype in ("output_text", "text"):
                    val = _item_get(block, "text")
                    if val:
                        parts.append(str(val))
    return "\n".join(parts).strip()


def _trace_and_tools_from_response(response: Any, agent_id: str) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Build UI-facing agent trace steps + tool chips from Responses output items."""
    steps: list[dict[str, Any]] = [
        {
            "step": "foundry_project",
            "status": "ok",
            "agent": agent_id,
            "responseId": getattr(response, "id", None),
            "model": getattr(response, "model", None),
        },
    ]
    mcps: list[str] = []
    tools_used: list[str] = []
    tool_executions: list[dict[str, Any]] = []

    for item in getattr(response, "output", None) or []:
        item_type = str(_item_get(item, "type") or "")
        name = _item_get(item, "name")
        label = _item_get(item, "server_label")
        status = _item_get(item, "status") or "ok"
        call_id = _item_get(item, "call_id") or _item_get(item, "id")

        if item_type == "message":
            steps.append({"step": "message", "status": status or "ok", "type": item_type})
            continue

        if item_type in ("reasoning", "reasoning_text"):
            summary = _item_get(item, "summary") or _item_get(item, "content")
            steps.append(
                {
                    "step": "reasoning",
                    "status": "ok",
                    "type": item_type,
                    "summary": str(summary)[:400] if summary else None,
                }
            )
            continue

        if "mcp" in item_type or item_type.startswith("mcp_"):
            display = label or name or item_type
            mcps.append(str(display))
            tools_used.append(str(display))
            entry = {
                "step": "mcp",
                "name": display,
                "status": status,
                "type": item_type,
                "callId": call_id,
                "arguments": _item_get(item, "arguments"),
                "output": _item_get(item, "output") or _item_get(item, "error"),
            }
            steps.append(entry)
            tool_executions.append(
                {
                    "name": display,
                    "type": item_type,
                    "status": status,
                    "input": _item_get(item, "arguments"),
                    "output": _item_get(item, "output") or _item_get(item, "error"),
                }
            )
            continue

        if "memory" in item_type:
            steps.append(
                {
                    "step": "memory",
                    "status": status or "queried",
                    "type": item_type,
                    "name": name,
                    "query": _item_get(item, "query") or _item_get(item, "arguments"),
                }
            )
            continue

        if item_type in (
            "function_call",
            "tool_call",
            "openapi_call",
            "file_search_call",
            "web_search_call",
            "code_interpreter_call",
            "computer_call",
            "custom_tool_call",
        ) or item_type.endswith("_call"):
            display = name or label or item_type
            tools_used.append(str(display))
            entry = {
                "step": "tool",
                "name": display,
                "status": status,
                "type": item_type,
                "callId": call_id,
                "arguments": _item_get(item, "arguments"),
            }
            steps.append(entry)
            tool_executions.append(
                {
                    "name": display,
                    "type": item_type,
                    "status": status,
                    "input": _item_get(item, "arguments"),
                    "output": _item_get(item, "output") or _item_get(item, "results"),
                }
            )
            continue

        if item_type:
            steps.append({"step": "output", "status": status or "ok", "type": item_type, "name": name})

    if not any(s.get("step") == "memory" for s in steps):
        steps.append({"step": "memory", "status": "available", "note": "memory tool not invoked this turn"})

    return steps, mcps, tool_executions


def _build_input(message: str, history: list[dict[str, Any]] | None, prev: str | None) -> Any:
    if prev:
        return message
    if history:
        turns = []
        for h in history:
            role = str(h.get("role") or "").strip().lower()
            content = str(h.get("content") or "").strip()
            if content and role in ("user", "assistant"):
                turns.append({"role": role, "content": content})
        turns.append({"role": "user", "content": message})
        return turns
    return message


def _create_response(openai_client: Any, create_kwargs: dict[str, Any]) -> Any:
    return openai_client.responses.create(**create_kwargs)


async def invoke_foundry_agent(
    agent_id: str,
    message: str,
    *,
    correlation_id: str,
    user_oid: str,
    user_email: str,
    roles: list[str],
    history: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    previous_response_id: str | None = None,
) -> dict[str, Any]:
    """Invoke a Foundry Prompt Agent via Responses API with playground-style turn chaining.

    Prefer ``previous_response_id`` (derived from ``session_id`` server-side). History is only
    a fallback when no prior response id exists yet.
    """
    endpoint = project_endpoint()
    model_meta = model_for_agent(agent_id)
    skills = skills_for_agent(agent_id)
    t0 = time.perf_counter()

    guardrails = await evaluate_guardrails(message, agent_id)
    if guardrails.get("blocked"):
        return {
            "error": "Guardrail blocked request",
            "guardrails": guardrails,
            "correlationId": correlation_id,
            "blocked": True,
        }

    reply = ""
    usage: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    mcps: list[str] = []
    tools_used: list[str] = []
    tool_executions: list[dict[str, Any]] = []
    raw_model = model_meta.get("deployment") or os.getenv("FOUNDRY_MODEL_DEPLOYMENT") or "model-router"
    response_id: str | None = None

    prev = previous_response_id or get_session_previous_response_id(session_id)
    model_input = _build_input(message, history, prev)

    openai_client = _get_openai_client(agent_id)
    create_kwargs: dict[str, Any] = {
        "input": model_input,
        "store": True,
        "extra_body": {
            "agent_reference": {"name": agent_id, "type": "agent_reference"},
            "metadata": {
                "correlationId": correlation_id,
                "userOid": user_oid,
                "userEmail": user_email,
                "roles": ",".join(roles),
                "sessionId": session_id or "",
            },
        },
    }
    if prev:
        create_kwargs["previous_response_id"] = prev

    t_client = time.perf_counter()
    try:
        # Sync SDK — run off the event loop so chat/stop stay responsive.
        response = await asyncio.to_thread(_create_response, openai_client, create_kwargs)
    except Exception as exc:
        # Stale previous_response_id — retry as a fresh turn with history fallback
        if prev and ("previous_response" in str(exc).lower() or "not found" in str(exc).lower()):
            logger.warning(
                "previous_response_id %s invalid for session %s — retrying without chain: %s",
                prev,
                session_id,
                exc,
            )
            clear_session_response(session_id)
            create_kwargs.pop("previous_response_id", None)
            create_kwargs["input"] = _build_input(message, history, None)
            response = await asyncio.to_thread(_create_response, openai_client, create_kwargs)
        else:
            raise
    t_resp = time.perf_counter()

    reply = _extract_text(response)
    response_id = getattr(response, "id", None)
    trace, mcps, tool_executions = _trace_and_tools_from_response(response, agent_id)
    tools_used = [t for t in (_item_get(x, "name") for x in tool_executions) if t] or [
        s["name"] for s in trace if s.get("step") in ("tool", "mcp") and s.get("name")
    ]
    u = getattr(response, "usage", None)
    if u:
        usage = {
            "promptTokens": getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", None),
            "completionTokens": getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", None),
            "totalTokens": getattr(u, "total_tokens", None),
            "model": getattr(response, "model", None) or raw_model,
        }
    else:
        usage = {"model": getattr(response, "model", None) or raw_model}

    if response_id and session_id:
        _SESSION_PREVIOUS_RESPONSE[session_id] = response_id

    if not reply:
        reply = (
            "Foundry agent returned no visible text. "
            "Check the agent version, model-router subset, and tool/MCP approvals in the Foundry portal."
        )
        logger.warning("Empty Foundry agent reply agent=%s correlation=%s", agent_id, correlation_id)

    # Foundry agents already apply project RAI. Output re-scan is optional (slow: extra AAD + CS calls).
    scan_output = os.getenv("ENABLE_OUTPUT_GUARDRAILS", "false").lower() == "true"
    if scan_output:
        out_guard = await evaluate_guardrails(reply, agent_id)
    else:
        out_guard = {"blocked": False, "decisions": [{"policy": "output_scan", "result": "skipped"}]}
    combined_guard = {
        "blocked": bool(out_guard.get("blocked")),
        "input": guardrails,
        "output": out_guard,
        "decisions": (guardrails.get("decisions") or [])
        + [{"policy": "output_scan", "result": d.get("result"), "details": d} for d in (out_guard.get("decisions") or [])],
    }

    memory_note = [
        {
            "id": str(uuid.uuid4()),
            "type": "procedural",
            "functionality": agent_id,
            "learning": f"Foundry agent {agent_id} handled: {message[:180]}",
            "store": os.getenv("MEMORY_STORE_NAME", "afa-agent-memory"),
        }
    ]

    logger.info(
        "foundry_invoke agent=%s session=%s prev=%s ms_total=%.0f ms_create=%.0f chained=%s",
        agent_id,
        session_id,
        bool(prev),
        (time.perf_counter() - t0) * 1000,
        (t_resp - t_client) * 1000,
        bool(prev),
    )

    return {
        "reply": reply,
        "output_markdown": reply,
        "harness": model_meta.get("harness") or "default_prompt",
        "skills": skills,
        "mcps": mcps,
        "tools": tools_used,
        "toolExecutions": tool_executions,
        "trace": [
            {"step": "backend_acl", "status": "ok"},
            {"step": "session", "status": "ok", "sessionId": session_id, "previousResponseId": prev},
            {"step": "guardrails_input", "status": "pass", "decisions": guardrails.get("decisions")},
            *trace,
            {"step": "guardrails_output", "status": "pass" if not combined_guard["blocked"] else "blocked"},
        ],
        "memory": memory_note,
        "usage": usage,
        "guardrails": combined_guard,
        "model": model_meta,
        "correlationId": correlation_id,
        "foundryProject": endpoint,
        "responseId": response_id,
        "previousResponseId": prev,
        "sessionId": session_id,
    }
