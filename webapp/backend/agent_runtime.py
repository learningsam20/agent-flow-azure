"""Agent catalog helpers: model deployment, skills, tools, secrets via MI/Key Vault."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFS = ROOT / "agents" / "definitions"


@lru_cache(maxsize=32)
def load_agent_spec(agent_id: str) -> dict[str, Any]:
    path = DEFS / f"{agent_id}.yaml"
    if not path.exists():
        return {
            "name": agent_id,
            "model": {"deployment": os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "model-router")},
            "skills": [],
            "tools": [],
            "guardrails": {"content_safety": True, "prompt_shields": True},
        }
    raw = path.read_text()
    deployment = os.getenv("FOUNDRY_MODEL_DEPLOYMENT") or os.getenv("FOUNDRY_MODEL_NAME") or "model-router"
    raw = raw.replace("${FOUNDRY_MODEL_NAME}", deployment)
    raw = raw.replace("${MEMORY_STORE_NAME}", os.getenv("MEMORY_STORE_NAME", "afa-agent-memory"))
    return yaml.safe_load(raw) or {}


def model_for_agent(agent_id: str) -> dict[str, str]:
    spec = load_agent_spec(agent_id)
    model = spec.get("model") or {}
    deployment = (
        os.getenv("FOUNDRY_MODEL_DEPLOYMENT")
        or model.get("deployment")
        or os.getenv("FOUNDRY_MODEL_NAME")
        or "model-router"
    )
    return {
        "deployment": deployment,
        "harness": (spec.get("harness") or {}).get("type", "microsoft_copilot_preview"),
        "routerMode": os.getenv("FOUNDRY_ROUTER_MODE", "balanced"),
        "fallbackPool": os.getenv(
            "FOUNDRY_FALLBACK_MODELS",
            "gpt-4.1-mini:2025-04-14,gpt-4.1:2025-04-14,gpt-4o:2024-11-20",
        ),
        "source": f"agents/definitions/{agent_id}.yaml",
    }


def skills_for_agent(agent_id: str) -> list[str]:
    spec = load_agent_spec(agent_id)
    return [s.get("name") if isinstance(s, dict) else str(s) for s in (spec.get("skills") or [])]


def tools_for_agent(agent_id: str) -> list[dict[str, Any]]:
    spec = load_agent_spec(agent_id)
    out = []
    for t in spec.get("tools") or []:
        if isinstance(t, dict):
            out.append({"type": t.get("type"), "name": t.get("name"), "resolve_from": t.get("resolve_from")})
        else:
            out.append({"name": str(t)})
    return out


def mcps_for_agent(agent_id: str) -> list[str]:
    return [t["name"] for t in tools_for_agent(agent_id) if t.get("type") == "mcp" and t.get("name")]


def secret_refs_for_agent(agent_id: str) -> list[dict[str, str]]:
    """Declare which Key Vault secrets an agent may resolve (never returns secret values)."""
    refs = []
    mcps = mcps_for_agent(agent_id)
    if "github" in mcps:
        refs.append(
            {
                "name": "github-mcp-pat",
                "vault": os.getenv("KEY_VAULT_URI") or "",
                "usedBy": "GitHub MCP",
                "auth": "Managed Identity → Key Vault Secrets User",
            }
        )
    if any("cve" in (t.get("name") or "") for t in tools_for_agent(agent_id)):
        refs.append(
            {
                "name": "nvd-api-key",
                "vault": os.getenv("KEY_VAULT_URI") or "",
                "usedBy": "NVD / CVE lookup",
                "auth": "Managed Identity → Key Vault Secrets User",
            }
        )
    return refs


def resolve_secret_present(secret_name: str) -> bool:
    """Check secret exists in Key Vault via MI (does not return value)."""
    vault = os.getenv("KEY_VAULT_URI")
    if not vault:
        return False
    try:
        from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        if os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"):
            cred = DefaultAzureCredential(
                exclude_interactive_browser_credential=True,
                managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None,
            )
        else:
            cred = ChainedTokenCredential(
                AzureCliCredential(),
                DefaultAzureCredential(
                    exclude_interactive_browser_credential=True,
                    exclude_managed_identity_credential=True,
                ),
            )
        client = SecretClient(vault_url=vault, credential=cred)
        client.get_secret(secret_name)
        return True
    except Exception:
        return False


_CS_TOKEN: tuple[str, float] | None = None  # (token, expires_at_epoch)


def _content_safety_token() -> str:
    """Cache AAD token for Content Safety — AzureCliCredential is multi-second cold."""
    global _CS_TOKEN
    import time

    now = time.time()
    if _CS_TOKEN and _CS_TOKEN[1] - 60 > now:
        return _CS_TOKEN[0]

    from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

    if os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"):
        cred = DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None,
        )
    else:
        cred = ChainedTokenCredential(
            AzureCliCredential(),
            DefaultAzureCredential(
                exclude_interactive_browser_credential=True,
                exclude_managed_identity_credential=True,
            ),
        )
    access = cred.get_token("https://cognitiveservices.azure.com/.default")
    _CS_TOKEN = (access.token, float(getattr(access, "expires_on", now + 3000)))
    return access.token


async def evaluate_guardrails(text: str, agent_id: str) -> dict[str, Any]:
    """Run Content Safety when enabled; always return a decision object for the UI."""
    import httpx

    spec = load_agent_spec(agent_id)
    g = spec.get("guardrails") or {}
    decisions: list[dict[str, Any]] = []
    blocked = False
    endpoint = os.getenv("CONTENT_SAFETY_ENDPOINT")

    run_cs = g.get("content_safety") and os.getenv("ENABLE_CONTENT_SAFETY", "true").lower() == "true"
    run_shield = g.get("prompt_shields") and os.getenv("ENABLE_PROMPT_SHIELDS", "true").lower() == "true"

    if (run_cs or run_shield) and endpoint:
        try:
            token = _content_safety_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=15.0) as client:
                tasks = []
                if run_cs:
                    tasks.append(
                        (
                            "content_safety",
                            client.post(
                                f"{endpoint.rstrip('/')}/contentsafety/text:analyze?api-version=2024-09-01",
                                headers=headers,
                                json={
                                    "text": text[:10000],
                                    "categories": ["Hate", "SelfHarm", "Sexual", "Violence"],
                                },
                            ),
                        )
                    )
                if run_shield:
                    tasks.append(
                        (
                            "prompt_shields",
                            client.post(
                                f"{endpoint.rstrip('/')}/contentsafety/text:shieldPrompt?api-version=2024-09-01",
                                headers=headers,
                                json={"userPrompt": text[:10000], "documents": []},
                            ),
                        )
                    )
                import asyncio

                results = await asyncio.gather(*(t[1] for t in tasks), return_exceptions=True)
                for (policy, _), resp in zip(tasks, results):
                    if isinstance(resp, Exception):
                        decisions.append({"policy": policy, "result": "skipped", "details": str(resp)})
                        continue
                    if resp.status_code >= 400:
                        decisions.append(
                            {
                                "policy": policy,
                                "result": "error",
                                "details": {"status": resp.status_code, "body": resp.text[:300]},
                            }
                        )
                        continue
                    data = resp.json()
                    if policy == "content_safety":
                        cats = data.get("categoriesAnalysis") or data.get("blocklistsMatch") or []
                        high = [
                            c
                            for c in cats
                            if isinstance(c, dict) and int(c.get("severity", 0)) >= 4
                        ]
                        if high:
                            blocked = True
                        decisions.append(
                            {
                                "policy": "content_safety",
                                "result": "blocked" if high else "pass",
                                "details": cats,
                            }
                        )
                    else:
                        analysis = data.get("userPromptAnalysis") or {}
                        attack = bool(analysis.get("attackDetected") or analysis.get("isAttackDetected"))
                        if attack:
                            blocked = True
                        decisions.append(
                            {
                                "policy": "prompt_shields",
                                "result": "blocked" if attack else "pass",
                                "details": analysis or data,
                            }
                        )
        except Exception as exc:  # noqa: BLE001
            decisions.append({"policy": "content_safety", "result": "skipped", "details": str(exc)})
    else:
        if run_cs:
            decisions.append(
                {
                    "policy": "content_safety",
                    "result": "configured",
                    "details": "CONTENT_SAFETY_ENDPOINT not set — policy declared on agent YAML",
                }
            )
        if run_shield:
            decisions.append(
                {
                    "policy": "prompt_shields",
                    "result": "configured",
                    "details": "CONTENT_SAFETY_ENDPOINT not set — also enforced via Foundry RAI policy",
                }
            )

    decisions.append(
        {
            "policy": "rbac_acl",
            "result": "enforced",
            "details": {"agent": agent_id, "source": "backend+apim"},
        }
    )

    return {
        "blocked": blocked,
        "decisions": decisions,
        "agentGuardrails": g,
    }
