"""Keycloak OIDC JWT validation + in-app Agent ACL (Cosmos).

No app-minted tokens. Users authenticate via Keycloak; App.Admin / App.User
are required. Agent.* ACL is stored in Cosmos and managed by App.Admin.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ACL_MATRIX: dict[str, dict[str, Any]] = {
    "Agent.Admin": {
        "agents": [
            "triage-router-agent",
            "repo-ops-agent",
            "sec-audit-agent",
            "finops-analyst-agent",
            "exec-synthesizer-agent",
        ],
        "skills": ["*"],
        "mcps": ["github", "microsoft-learn", "mcp-afa-audit-store"],
        "hitl": True,
    },
    "Agent.Developer": {
        "agents": ["triage-router-agent", "repo-ops-agent", "exec-synthesizer-agent"],
        "skills": ["intent-classifier", "github-issue-creator", "research-brief"],
        "mcps": ["github", "mcp-afa-audit-store"],
        "hitl": False,
    },
    "Agent.SecurityAuditor": {
        "agents": ["triage-router-agent", "sec-audit-agent", "exec-synthesizer-agent"],
        "skills": ["intent-classifier", "owasp-security-audit", "research-brief"],
        "mcps": ["microsoft-learn", "mcp-afa-audit-store"],
        "hitl": True,
    },
    "Agent.Viewer": {
        "agents": ["triage-router-agent", "exec-synthesizer-agent"],
        "skills": ["research-brief", "intent-classifier"],
        "mcps": [],
        "hitl": False,
    },
}

APP_ROLES = {"App.Admin", "App.User"}
AGENT_ROLES = set(ACL_MATRIX.keys())

bearer = HTTPBearer(auto_error=False)

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


def keycloak_url() -> str:
    return os.getenv("KEYCLOAK_URL", "http://localhost:5750").rstrip("/")


def keycloak_realm() -> str:
    return os.getenv("KEYCLOAK_REALM", "agent-flow")


def keycloak_issuer() -> str:
    explicit = os.getenv("KEYCLOAK_ISSUER")
    if explicit:
        return explicit.rstrip("/")
    return f"{keycloak_url()}/realms/{keycloak_realm()}"


def keycloak_audience() -> str:
    return os.getenv("KEYCLOAK_API_AUDIENCE", "afa-api")


@dataclass
class UserContext:
    oid: str
    name: str
    roles: list[str]
    email: str = ""
    app_roles: list[str] = field(default_factory=list)
    agent_roles: list[str] = field(default_factory=list)
    raw_token: str = ""

    def is_admin(self) -> bool:
        return "App.Admin" in self.app_roles

    def allowed_agents(self) -> set[str]:
        agents: set[str] = set()
        for r in self.agent_roles:
            agents.update(ACL_MATRIX.get(r, {}).get("agents", []))
        # App.Admin / Agent.Admin can use every known agent (incl. Azure-synced).
        if self.is_admin() or "Agent.Admin" in self.agent_roles:
            try:
                from azure_sync import list_known_agents

                agents.update(list_known_agents())
            except Exception:
                agents.update(ACL_MATRIX["Agent.Admin"]["agents"])
        elif self.is_admin() and not agents:
            agents.update(ACL_MATRIX["Agent.Admin"]["agents"])
        return agents

    def can_hitl(self) -> bool:
        if self.is_admin():
            return True
        return any(ACL_MATRIX.get(r, {}).get("hitl") for r in self.agent_roles)

    def hitl_capable_roles(self) -> list[str]:
        roles = [r for r in self.agent_roles if ACL_MATRIX.get(r, {}).get("hitl")]
        if self.is_admin() and "App.Admin" not in roles:
            roles = ["App.Admin", *roles]
        return roles

    def permission_matrix(self) -> dict[str, Any]:
        agents = self.allowed_agents()
        try:
            from azure_sync import list_known_agents

            all_agents = list_known_agents()
        except Exception:
            all_agents = [
                "triage-router-agent",
                "repo-ops-agent",
                "sec-audit-agent",
                "finops-analyst-agent",
                "exec-synthesizer-agent",
            ]
        skills: set[str] = set()
        mcps: set[str] = set()
        for r in self.agent_roles or (["Agent.Admin"] if self.is_admin() else []):
            skills.update(ACL_MATRIX.get(r, {}).get("skills", []))
            mcps.update(ACL_MATRIX.get(r, {}).get("mcps", []))
        return {
            "agents": {a: a in agents for a in all_agents},
            "skills": sorted(skills),
            "mcps": sorted(mcps),
            "hitl": self.can_hitl(),
            "hitlRoles": self.hitl_capable_roles(),
            "appRoles": self.app_roles,
            "agentRoles": self.agent_roles,
            "controls": {
                "pause": True,
                "resume": True,
                "stop": True,
                "retry": True,
                "retryFromNode": True,
                "hitlReroute": self.can_hitl(),
            },
        }


def _fetch_jwks() -> dict[str, Any]:
    now = time.time()
    if _JWKS_CACHE["keys"] and now - _JWKS_CACHE["fetched_at"] < 3600:
        return _JWKS_CACHE["keys"]
    url = f"{keycloak_issuer()}/protocol/openid-connect/certs"
    resp = httpx.get(url, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    _JWKS_CACHE["keys"] = data
    _JWKS_CACHE["fetched_at"] = now
    return data


def _roles_from_claims(payload: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    realm = payload.get("realm_access") or {}
    roles.extend(realm.get("roles") or [])
    resource = payload.get("resource_access") or {}
    for client in (keycloak_audience(), "afa-web", "account"):
        roles.extend((resource.get(client) or {}).get("roles") or [])
    claim_roles = payload.get("roles") or payload.get("role") or []
    if isinstance(claim_roles, str):
        claim_roles = [claim_roles]
    roles.extend(claim_roles)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for r in roles:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def load_agent_roles_from_store(oid: str) -> list[str] | None:
    """Return Cosmos-stored Agent.* roles, or None if store unavailable."""
    endpoint = os.getenv("COSMOS_ENDPOINT")
    if not endpoint:
        return None
    try:
        from azure.cosmos import CosmosClient
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
        client = CosmosClient(endpoint, credential=cred)
        container = client.get_database_client("workflow").get_container_client("userRoles")
        doc = container.read_item(item=oid, partition_key=oid)
        roles = [r for r in (doc.get("agentRoles") or []) if r in AGENT_ROLES]
        return roles
    except Exception:
        return None


def save_agent_roles(oid: str, agent_roles: list[str], email: str = "", name: str = "") -> dict[str, Any]:
    endpoint = os.getenv("COSMOS_ENDPOINT")
    if not endpoint:
        raise HTTPException(status_code=503, detail="COSMOS_ENDPOINT required for role management")
    cleaned = [r for r in agent_roles if r in AGENT_ROLES]
    from azure.cosmos import CosmosClient
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
    client = CosmosClient(endpoint, credential=cred)
    db = client.get_database_client("workflow")
    try:
        container = db.get_container_client("userRoles")
    except Exception:
        raise HTTPException(status_code=503, detail="Cosmos userRoles container missing")
    doc = {
        "id": oid,
        "userOid": oid,
        "email": email,
        "name": name,
        "agentRoles": cleaned,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    container.upsert_item(doc)
    return doc


def decode_token(token: str) -> UserContext:
    try:
        header = jwt.get_unverified_header(token)
        jwks = _fetch_jwks()
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
        if not key:
            # refresh once
            _JWKS_CACHE["keys"] = None
            jwks = _fetch_jwks()
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
        if not key:
            raise HTTPException(status_code=401, detail="Unknown token signing key")
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
        # Accept localhost / 127.0.0.1 issuer variants for local Keycloak
        base = keycloak_issuer()
        allowed_issuers = {base}
        if "localhost" in base:
            allowed_issuers.add(base.replace("localhost", "127.0.0.1"))
        if "127.0.0.1" in base:
            allowed_issuers.add(base.replace("127.0.0.1", "localhost"))
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=keycloak_audience(),
            issuer=list(allowed_issuers),
            options={"require": ["exp", "iss"]},
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"Invalid Keycloak token: {exc}") from exc

    all_roles = _roles_from_claims(payload)
    app_roles = [r for r in all_roles if r in APP_ROLES]
    if not app_roles:
        raise HTTPException(
            status_code=403,
            detail="Missing required app role App.Admin or App.User",
        )

    # Prefer stable identity claims. Never prefer `sid` — it changes every login and
    # would orphan Cosmos/local history under a new userOid after re-login.
    # Lightweight tokens may omit `sub`; use username/email before session id.
    oid = str(
        payload.get("sub")
        or payload.get("oid")
        or payload.get("preferred_username")
        or payload.get("email")
        or payload.get("sid")
        or ""
    )
    email = payload.get("email") or payload.get("preferred_username") or ""
    name = payload.get("name") or email or oid or "keycloak-user"
    if not oid:
        raise HTTPException(status_code=401, detail="Token missing subject (sub/oid/sid)")

    stored = load_agent_roles_from_store(oid)
    token_agent = [r for r in all_roles if r in AGENT_ROLES]
    agent_roles = stored if stored is not None else token_agent

    return UserContext(
        oid=oid,
        name=name,
        email=email,
        roles=list(dict.fromkeys(app_roles + agent_roles)),
        app_roles=app_roles,
        agent_roles=agent_roles,
        raw_token=token,
    )


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> UserContext:
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    return decode_token(creds.credentials)


async def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="App.Admin required")
    return user


def assert_agent_allowed(user: UserContext, agent_id: str) -> None:
    if agent_id not in user.allowed_agents():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Policy Violation: Role lacks permission",
                "agent": agent_id,
                "roles": user.roles,
                "correlationId": str(uuid.uuid4()),
            },
        )
