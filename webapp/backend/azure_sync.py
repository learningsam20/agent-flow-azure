"""Sync Foundry agents + Logic Apps workflows from Azure into the portal catalog.

Local catalog.json remains the base. Sync merges Azure discoveries into
`.data/azure_catalog.json` and caches workflow definitions under
`.data/synced-workflows/` so the canvas can render without a repo commit.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("afa.sync")

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("AFA_DATA_DIR", str(ROOT / ".data")))
CATALOG_CACHE = DATA_DIR / "azure_catalog.json"
SYNCED_WF_DIR = DATA_DIR / "synced-workflows"
DEFS_DIR = ROOT / "agents" / "definitions"

# Runtime trigger URLs discovered from Azure (overrides env for the process).
_TRIGGER_URLS: dict[str, str] = {}
_CACHED: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _credential():
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


def _arm_token() -> str:
    return _credential().get_token("https://management.azure.com/.default").token


def _arm_get(url: str, token: str) -> dict[str, Any]:
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"ARM GET {resp.status_code}: {resp.text[:500]}")
        return resp.json()


def _arm_post(url: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload or {},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"ARM POST {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "workflow"


def _flow_id_from_logic_app(resource: dict[str, Any]) -> str:
    tags = resource.get("tags") or {}
    if tags.get("flow_id"):
        return str(tags["flow_id"])
    name = str(resource.get("name") or "workflow")
    # Strip common afa naming: afa-dev-logic-sec-eus2-01 → keep full slug for uniqueness
    return _slug(name)


def local_agent_ids() -> list[str]:
    ids = sorted(p.stem for p in DEFS_DIR.glob("*.yaml") if not p.name.startswith("."))
    return ids


def load_sync_cache() -> dict[str, Any]:
    global _CACHED, _TRIGGER_URLS
    if _CACHED is not None:
        return _CACHED
    if CATALOG_CACHE.exists():
        try:
            _CACHED = json.loads(CATALOG_CACHE.read_text())
            triggers = (_CACHED or {}).get("triggerUrls") or {}
            if isinstance(triggers, dict):
                _TRIGGER_URLS.update({str(k): str(v) for k, v in triggers.items() if v})
            return _CACHED or {}
        except Exception as exc:
            logger.warning("load_sync_cache: %s", exc)
    _CACHED = {"agents": [], "flows": [], "triggerUrls": {}, "syncedAt": None}
    return _CACHED


def save_sync_cache(payload: dict[str, Any]) -> None:
    global _CACHED
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CATALOG_CACHE.write_text(json.dumps(payload, indent=2, default=str))
    _CACHED = payload
    triggers = payload.get("triggerUrls") or {}
    if isinstance(triggers, dict):
        _TRIGGER_URLS.clear()
        _TRIGGER_URLS.update({str(k): str(v) for k, v in triggers.items() if v})


def synced_trigger_url(flow_id: str | None) -> str | None:
    if not flow_id:
        return None
    load_sync_cache()
    return _TRIGGER_URLS.get(flow_id)


def synced_flows() -> list[dict[str, Any]]:
    return list(load_sync_cache().get("flows") or [])


def synced_agents() -> list[dict[str, Any]]:
    return list(load_sync_cache().get("agents") or [])


def list_known_agents() -> list[str]:
    """Stable agent id list for ACL UI + chat picker (local defs ∪ Azure sync)."""
    names = set(local_agent_ids())
    for a in synced_agents():
        if a.get("id"):
            names.add(str(a["id"]))
    # Prefer local yaml order first, then extras
    local = local_agent_ids()
    extras = sorted(names - set(local))
    return local + extras


def sync_foundry_agents() -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    agents: list[dict[str, Any]] = []
    try:
        from azure.ai.projects import AIProjectClient

        ep = (os.getenv("FOUNDRY_PROJECT_ENDPOINT") or "").rstrip("/")
        if not ep or "/api/projects/" not in ep:
            errors.append("FOUNDRY_PROJECT_ENDPOINT not set")
            return agents, errors
        client = AIProjectClient(endpoint=ep, credential=_credential(), allow_preview=True)
        for item in client.agents.list(limit=100, order="asc"):
            name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else None)
            if not name:
                continue
            state = getattr(item, "state", None) or (item.get("state") if isinstance(item, dict) else None)
            agents.append(
                {
                    "id": str(name),
                    "name": str(name),
                    "source": "azure-foundry",
                    "state": str(state) if state is not None else None,
                    "kind": "prompt",
                }
            )
    except Exception as exc:
        logger.exception("sync_foundry_agents")
        errors.append(f"Foundry agents: {exc}")
    # Always include local definitions even if Azure list fails
    seen = {a["id"] for a in agents}
    for aid in local_agent_ids():
        if aid not in seen:
            agents.append({"id": aid, "name": aid, "source": "local", "kind": "prompt"})
    agents.sort(key=lambda a: a["id"])
    return agents, errors


def sync_logic_apps() -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    errors: list[str] = []
    flows: list[dict[str, Any]] = []
    triggers: dict[str, str] = {}
    sub = os.getenv("AZURE_SUBSCRIPTION_ID") or ""
    rg = os.getenv("RESOURCE_GROUP_NAME") or ""
    if not sub or not rg:
        errors.append("AZURE_SUBSCRIPTION_ID / RESOURCE_GROUP_NAME required to sync Logic Apps")
        return flows, triggers, errors

    try:
        token = _arm_token()
    except Exception as exc:
        errors.append(f"Azure auth for Logic Apps: {exc}")
        return flows, triggers, errors

    list_url = (
        f"https://management.azure.com/subscriptions/{urllib.parse.quote(sub)}"
        f"/resourceGroups/{urllib.parse.quote(rg)}"
        f"/providers/Microsoft.Logic/workflows?api-version=2019-05-01"
    )
    try:
        page = _arm_get(list_url, token)
    except Exception as exc:
        errors.append(f"List Logic Apps: {exc}")
        return flows, triggers, errors

    resources = list(page.get("value") or [])
    next_link = page.get("nextLink")
    while next_link:
        try:
            page = _arm_get(next_link, token)
            resources.extend(page.get("value") or [])
            next_link = page.get("nextLink")
        except Exception as exc:
            errors.append(f"Logic Apps paging: {exc}")
            break

    SYNCED_WF_DIR.mkdir(parents=True, exist_ok=True)

    # Map Azure resource name → catalog flow id so we enrich demos instead of duplicating.
    catalog_by_la: dict[str, str] = {}
    try:
        catalog_path = ROOT / "workflows" / "catalog.json"
        if catalog_path.exists():
            for f in (json.loads(catalog_path.read_text()).get("flows") or []):
                la = f.get("logicAppName")
                fid = f.get("id")
                if la and fid:
                    catalog_by_la[str(la)] = str(fid)
    except Exception:
        pass

    for res in resources:
        name = str(res.get("name") or "")
        if not name:
            continue
        tags = res.get("tags") or {}
        flow_id = (
            str(tags.get("flow_id") or "").strip()
            or catalog_by_la.get(name)
            or _flow_id_from_logic_app(res)
        )
        props = res.get("properties") or {}
        state = props.get("state")
        definition = props.get("definition")
        # Full GET if list payload omitted definition
        if not definition:
            try:
                detail = _arm_get(
                    f"https://management.azure.com{res['id']}?api-version=2019-05-01",
                    token,
                )
                definition = (detail.get("properties") or {}).get("definition")
            except Exception as exc:
                errors.append(f"{name}: fetch definition failed ({exc})")
                definition = None

        workflow_path = None
        if definition:
            dest = SYNCED_WF_DIR / flow_id
            dest.mkdir(parents=True, exist_ok=True)
            # Match local workflow.json shape used by workflow_graph.load_workflow_definition
            (dest / "workflow.json").write_text(
                json.dumps({"definition": definition}, indent=2, default=str)
            )
            try:
                workflow_path = str((dest / "workflow.json").relative_to(ROOT))
            except ValueError:
                workflow_path = str(dest / "workflow.json")

        # Callback URL for HTTP request / manual trigger
        trigger_url = None
        for trigger_name in ("manual", "When_an_HTTP_request_is_received", "request"):
            try:
                cb = _arm_post(
                    f"https://management.azure.com{res['id']}/triggers/{trigger_name}"
                    f"/listCallbackUrl?api-version=2019-05-01",
                    token,
                )
                trigger_url = cb.get("value") or cb.get("url")
                if trigger_url:
                    break
            except Exception:
                continue
        if trigger_url:
            triggers[flow_id] = str(trigger_url)

        display = name
        desc = (
            f"Synced from Azure Logic App `{name}`"
            + (f" (state={state})" if state else "")
            + "."
        )
        flows.append(
            {
                "id": flow_id,
                "name": tags.get("displayName") or display,
                "description": desc,
                "demoPrompt": "",
                "features": ["synced", "azure"],
                "defaultPath": "A",
                "hitlReviewerRoles": ["App.Admin", "Agent.Admin"],
                "workflowPath": workflow_path,
                "logicAppName": name,
                "logicAppId": res.get("id"),
                "source": "azure",
                "state": state,
            }
        )

    flows.sort(key=lambda f: f["id"])
    return flows, triggers, errors


def sync_from_azure() -> dict[str, Any]:
    """Pull agents + Logic Apps from Azure and persist a mergeable catalog cache."""
    agents, agent_errs = sync_foundry_agents()
    flows, triggers, flow_errs = sync_logic_apps()
    def _short(err: str) -> str:
        # Keep portal toast readable — first line / 240 chars.
        line = (err or "").strip().splitlines()[0] if err else ""
        return line[:240] + ("…" if len(line) > 240 else "")

    payload = {
        "syncedAt": _now(),
        "agents": agents,
        "flows": flows,
        "triggerUrls": triggers,
        "errors": [_short(e) for e in (agent_errs + flow_errs)],
        "resourceGroup": os.getenv("RESOURCE_GROUP_NAME"),
        "subscriptionId": os.getenv("AZURE_SUBSCRIPTION_ID"),
        "foundryProject": os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    }
    save_sync_cache(payload)
    logger.info(
        "Azure sync complete: %s agents, %s flows, %s errors",
        len(agents),
        len(flows),
        len(payload["errors"]),
    )
    return payload
