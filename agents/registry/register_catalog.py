#!/usr/bin/env python3
"""Deploy real Foundry catalog: skills, memory, toolbox (MCP+OpenAPI), prompt agents.

Requires FOUNDRY_PROJECT_ENDPOINT = https://<account>.services.ai.azure.com/api/projects/<project>
Auth: DefaultAzureCredential / Azure CLI (no API keys).
Fails non-zero on any hard error — no silent stubs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "agents" / "skills"
DEFS_DIR = ROOT / "agents" / "definitions"
TOOLBOX_NAME = "afa-shared-toolbox"
sys.path.insert(0, str(Path(__file__).resolve().parent))

# MCP tools use Foundry project connections (secrets in ARM, not in agent definitions).
# GitHub = Foundry catalog Official MCP (api.githubcopilot.com) via connection github-mcp.
# ServiceNow = Azure-hosted OSS MCP (Container Apps) via connection servicenow-mcp.

OPENAPI_TOOLS = [
    {
        "name": "fn-afa-validate-request",
        "path": "/api/validate-request",
        "description": "Validate inbound agent/flow requests",
        "roles": ["*"],
    },
    {
        "name": "fn-afa-cve-lookup",
        "path": "/api/cve-lookup",
        "description": "Look up package CVEs via OSV/NVD",
        "roles": ["Agent.Admin", "Agent.SecurityAuditor"],
    },
    {
        "name": "fn-afa-cost-estimator",
        "path": "/api/cost-estimator",
        "description": "Estimate Azure consumption costs",
        "roles": ["Agent.Admin"],
    },
    {
        "name": "fn-afa-repo-metrics",
        "path": "/api/repo-metrics",
        "description": "Repository PR/issue metrics",
        "roles": ["Agent.Admin", "Agent.Developer"],
    },
]


def load_env() -> None:
    # .env wins over .env.generated for operator overrides (e.g. FOUNDRY_MODEL_DEPLOYMENT).
    for name in (".env.generated", ".env"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                val = val[1:-1]
            if name == ".env":
                os.environ[key] = val
            else:
                os.environ.setdefault(key, val)


def get_credential():
    from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

    on_azure = bool(os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"))
    if on_azure:
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


def project_endpoint() -> str:
    ep = (os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").rstrip("/")
    if not ep or "/api/projects/" not in ep:
        raise SystemExit(
            "FOUNDRY_PROJECT_ENDPOINT must be a Foundry project URL "
            "(https://<account>.services.ai.azure.com/api/projects/<project>). "
            "Run provision.sh after enabling project_management_enabled. See setup.md."
        )
    return ep


def agent_model() -> str:
    return (
        os.environ.get("FOUNDRY_MODEL_DEPLOYMENT")
        or os.environ.get("FOUNDRY_AGENT_DEPLOYMENT")
        or "model-router"
    )


def foundry_account_name() -> str:
    account = (os.environ.get("FOUNDRY_ACCOUNT_NAME") or "").strip()
    if account:
        return account
    for key in ("FOUNDRY_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT"):
        ep = (os.environ.get(key) or "").strip()
        if "://" in ep:
            return ep.split("://", 1)[1].split(".", 1)[0]
    raise RuntimeError("FOUNDRY_ACCOUNT_NAME or FOUNDRY_ENDPOINT required for RAI policy id")


def resolve_rai_policy_id() -> str | None:
    """Agents API requires the fully-qualified RAI policy ARM id, not the short name."""
    if os.environ.get("ENABLE_PROMPT_SHIELDS", "true").lower() != "true":
        return None
    explicit = (os.environ.get("FOUNDRY_RAI_POLICY_ID") or "").strip()
    if explicit.startswith("/subscriptions/"):
        return explicit
    name = (os.environ.get("FOUNDRY_RAI_POLICY_NAME") or "afa-default-rai").strip()
    if name.startswith("/subscriptions/"):
        return name
    sub = os.environ["AZURE_SUBSCRIPTION_ID"]
    rg = os.environ["RESOURCE_GROUP_NAME"]
    account = foundry_account_name()
    return (
        f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices"
        f"/accounts/{account}/raiPolicies/{name}"
    )


def functions_base() -> str:
    return (
        os.environ.get("FUNCTION_APP_URL")
        or os.environ.get("MCP_HOST")
        or (f"https://{os.environ['FUNCTION_APP_NAME']}.azurewebsites.net" if os.environ.get("FUNCTION_APP_NAME") else "")
    ).rstrip("/")


def kv_secret(credential, name: str) -> str | None:
    vault = os.environ.get("KEY_VAULT_URI")
    env_fallback = {
        "github-mcp-pat": "GITHUB_MCP_PAT",
        "nvd-api-key": "NVD_API_KEY",
        "servicenow-password": "SERVICENOW_PASSWORD",
        "servicenow-client-secret": "SERVICENOW_CLIENT_SECRET",
    }.get(name)
    if env_fallback and os.environ.get(env_fallback):
        return os.environ[env_fallback]
    if not vault:
        return None
    try:
        from azure.keyvault.secrets import SecretClient

        return SecretClient(vault_url=vault, credential=credential).get_secret(name).value
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: Key Vault secret '{name}' unavailable: {exc}")
        return None


def upload_skills_blob(credential) -> list[dict]:
    """Backup copy of SKILL.md to Storage (Foundry skills are authoritative)."""
    from azure.storage.blob import BlobServiceClient

    account = os.environ.get("STORAGE_ACCOUNT_NAME")
    if not account:
        print("WARN: STORAGE_ACCOUNT_NAME missing — skipping blob backup of skills")
        return []
    url = f"https://{account}.blob.core.windows.net"
    client = BlobServiceClient(account_url=url, credential=credential)
    container = client.get_container_client("skills")
    try:
        container.create_container()
    except Exception:
        pass
    registered = []
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        name = skill_md.parent.name
        container.get_blob_client(f"{name}/SKILL.md").upload_blob(skill_md.read_bytes(), overwrite=True)
        registered.append({"name": name, "blob": f"{url}/skills/{name}/SKILL.md"})
        print(f"Blob backup skill: {name}")
    return registered


def deploy_foundry_skills(project_client) -> dict[str, str]:
    """Create Foundry skill versions from SKILL.md folders. Returns name -> version."""
    versions: dict[str, str] = {}
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        name = skill_dir.name
        files: list[Any] = []
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                files.append((str(path.relative_to(skill_dir)), path.open("rb")))
        try:
            created = project_client.beta.skills.create_from_files(
                name=name,
                content={"files": files, "default": True},
            )
            ver = getattr(created, "version", None) or (created.get("version") if isinstance(created, dict) else None) or "1"
            versions[name] = str(ver)
            print(f"Foundry skill: {name}@{ver}")
        finally:
            for item in files:
                f = item[1] if isinstance(item, tuple) else None
                if f and hasattr(f, "close"):
                    f.close()
    if not versions:
        raise RuntimeError("No skills deployed — agents/skills/*/SKILL.md required")
    return versions


def ensure_memory_store(project_client) -> str:
    name = os.environ.get("MEMORY_STORE_NAME", "afa-agent-memory")
    chat_model = agent_model()
    embedding = os.environ.get("FOUNDRY_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    from azure.ai.projects.models import MemoryStoreDefaultDefinition, MemoryStoreDefaultOptions

    definition = MemoryStoreDefaultDefinition(
        chat_model=chat_model,
        embedding_model=embedding,
        options=MemoryStoreDefaultOptions(
            user_profile_enabled=True,
            chat_summary_enabled=True,
            procedural_memory_enabled=True,
            user_profile_details="agent workflows, preferred tools, security policies, FinOps constraints",
        ),
    )
    try:
        for existing in project_client.beta.memory_stores.list():
            en = getattr(existing, "name", None) or (existing.get("name") if isinstance(existing, dict) else None)
            if en == name:
                print(f"Memory store exists: {name}")
                return name
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: list memory stores: {exc}")

    project_client.beta.memory_stores.create(name=name, definition=definition)
    print(f"Memory store created: {name} (chat={chat_model}, embedding={embedding})")
    return name


def _openapi_spec(tool: dict, base: str) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": tool["name"], "version": "1.0.0", "description": tool["description"]},
        "servers": [{"url": base}],
        "paths": {
            tool["path"]: {
                "post": {
                    "operationId": tool["name"].replace("-", "_"),
                    "summary": tool["description"],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


def build_mcp_tools(credential) -> tuple[list[Any], dict[str, str]]:
    """Build MCPTool list bound to Foundry project connections (no inline secrets)."""
    from azure.ai.projects.models import MCPTool

    from foundry_connections import (
        ensure_github_mcp_connection,
        ensure_learn_mcp_connection,
        ensure_servicenow_mcp_connection,
    )

    tools: list[Any] = []
    connections: dict[str, str] = {}

    pat = kv_secret(credential, "github-mcp-pat") or os.environ.get("GITHUB_MCP_PAT") or ""
    gh_conn = ensure_github_mcp_connection(pat)
    connections["github"] = gh_conn
    tools.append(
        MCPTool(
            server_label="github",
            server_url="https://api.githubcopilot.com/mcp/",
            server_description="GitHub Official MCP (Foundry catalog)",
            project_connection_id=gh_conn,
            require_approval="never",
        )
    )

    learn_conn = ensure_learn_mcp_connection()
    connections["microsoft-learn"] = learn_conn
    tools.append(
        MCPTool(
            server_label="microsoft-learn",
            server_url="https://learn.microsoft.com/api/mcp",
            server_description="Microsoft Learn Docs MCP",
            project_connection_id=learn_conn,
            require_approval="never",
        )
    )

    # Audit store on Functions (optional — broken/anonymous endpoints cause tool_user_error on invoke)
    audit_base = os.environ.get("AUDIT_MCP_URL") or functions_base()
    if audit_base and os.environ.get("ENABLE_AUDIT_MCP", "").lower() in ("1", "true", "yes"):
        audit_url = audit_base if audit_base.endswith("/mcp") else f"{audit_base.rstrip('/')}/api/mcp"
        tools.append(
            MCPTool(
                server_label="mcp-afa-audit-store",
                server_url=audit_url,
                server_description="Audit Store MCP (Functions)",
                require_approval="never",
            )
        )
        print(f"MCP tool ready: mcp-afa-audit-store -> {audit_url}")
    elif audit_base:
        print("Skipping audit MCP (set ENABLE_AUDIT_MCP=true when the Functions MCP endpoint is ready)")

    # ServiceNow OSS MCP hosted on Azure Container Apps (optional until URL + instance creds set)
    sn_mcp = (os.environ.get("SERVICENOW_MCP_URL") or "").strip()
    sn_instance = (os.environ.get("SERVICENOW_INSTANCE_URL") or "").strip()
    if sn_mcp and sn_instance:
        sn_user = os.environ.get("SERVICENOW_USERNAME") or ""
        sn_pass = (
            kv_secret(credential, "servicenow-password")
            or os.environ.get("SERVICENOW_PASSWORD")
            or ""
        )
        sn_conn = ensure_servicenow_mcp_connection(
            mcp_url=sn_mcp,
            username=os.environ.get("SERVICENOW_MCP_BASIC_USER", ""),
            password=os.environ.get("SERVICENOW_MCP_BASIC_PASSWORD", ""),
        )
        connections["servicenow"] = sn_conn
        # Prefer streamable HTTP (/mcp); accept legacy /sse URLs if already set.
        sn_url = sn_mcp.rstrip("/")
        if sn_url.endswith("/sse"):
            sn_url = sn_url[: -len("/sse")] + "/mcp"
        elif not sn_url.endswith("/mcp"):
            sn_url = f"{sn_url}/mcp"
        tools.append(
            MCPTool(
                server_label="servicenow",
                server_url=sn_url,
                server_description="ServiceNow OSS MCP (Azure-hosted)",
                project_connection_id=sn_conn,
                require_approval="never",
            )
        )
        if not sn_user or not sn_pass:
            print("WARN: SERVICENOW_USERNAME/PASSWORD empty — MCP container must already have Key Vault secrets")
    else:
        print(
            "Skipping ServiceNow MCP until SERVICENOW_INSTANCE_URL + SERVICENOW_MCP_URL are set "
            "(host OSS MCP on ACA — see setup.md)"
        )

    return tools, connections


def build_openapi_tools() -> list[Any]:
    from azure.ai.projects.models import OpenApiAnonymousAuthDetails, OpenApiFunctionDefinition, OpenApiTool

    # Anonymous OpenAPI against APIM JWT often fails agent tool handshake (tool_user_error).
    # Opt in once Function/APIM MI auth is wired: ENABLE_OPENAPI_TOOLS=true
    if os.environ.get("ENABLE_OPENAPI_TOOLS", "").lower() not in ("1", "true", "yes"):
        print("Skipping OpenAPI tools (set ENABLE_OPENAPI_TOOLS=true when APIM/Functions auth is ready)")
        return []

    base = functions_base()
    if not base:
        raise RuntimeError("FUNCTION_APP_URL or MCP_HOST required for OpenAPI tools — see setup.md")
    gateway = (os.environ.get("APIM_GATEWAY_URL") or "").rstrip("/")
    tool_base = gateway if gateway else base
    out = []
    for tool in OPENAPI_TOOLS:
        path = tool["path"]
        spec_base = tool_base
        out.append(
            OpenApiTool(
                openapi=OpenApiFunctionDefinition(
                    name=tool["name"],
                    description=tool["description"],
                    spec=_openapi_spec(tool, spec_base),
                    auth=OpenApiAnonymousAuthDetails(),
                )
            )
        )
        print(f"OpenAPI tool ready: {tool['name']} @ {spec_base}{path}")
    return out


def deploy_toolbox(project_client, skill_versions: dict[str, str], mcp_tools: list, openapi_tools: list) -> str:
    from azure.ai.projects.models import (
        MCPToolboxTool,
        OpenApiToolboxTool,
        ToolSearchToolboxTool,
        ToolboxSkillReference,
    )

    tb_tools: list[Any] = [ToolSearchToolboxTool()]
    for t in mcp_tools:
        d = t.as_dict() if hasattr(t, "as_dict") else dict(t)
        tb_tools.append(
            MCPToolboxTool(
                server_label=d["server_label"],
                server_url=d.get("server_url"),
                authorization=d.get("authorization"),
                server_description=d.get("server_description"),
                require_approval=d.get("require_approval") or "never",
            )
        )
    for t in openapi_tools:
        d = t.as_dict() if hasattr(t, "as_dict") else {}
        oa = d.get("openapi") if isinstance(d, dict) else None
        if oa is None and hasattr(t, "openapi"):
            oa = t.openapi
        tb_tools.append(OpenApiToolboxTool(openapi=oa))

    skills = [ToolboxSkillReference(name=n, version=v) for n, v in skill_versions.items()]
    project_client.toolboxes.create_version(
        name=TOOLBOX_NAME,
        tools=tb_tools,
        skills=skills,
        description="Shared toolbox: MCP + OpenAPI Functions + Foundry skills",
        metadata={"project": "agent-flow-azure"},
    )
    print(f"Toolbox version created: {TOOLBOX_NAME} ({len(tb_tools)} tools, {len(skills)} skills)")
    return TOOLBOX_NAME


def deploy_agents(
    project_client,
    *,
    skill_versions: dict[str, str],
    memory_store: str,
    mcp_tools: list,
    openapi_tools: list,
) -> None:
    from azure.ai.projects.models import (
        MemorySearchPreviewTool,
        PromptAgentDefinition,
        RaiConfig,
        SkillReference,
    )

    model = agent_model()
    rai_policy = resolve_rai_policy_id()
    # Copilot harness is preview / subscription-gated; default prompt runtime works everywhere.
    harness = None
    if os.environ.get("FOUNDRY_USE_COPILOT_HARNESS", "").lower() in ("1", "true", "yes"):
        from azure.ai.projects.models import MicrosoftCopilotHarness

        harness = MicrosoftCopilotHarness()

    for yml in sorted(DEFS_DIR.glob("*.yaml")):
        if yml.name.startswith("."):
            continue
        raw = yml.read_text()
        raw = raw.replace("${FOUNDRY_MODEL_NAME}", model)
        raw = raw.replace("${MEMORY_STORE_NAME}", memory_store)
        spec = yaml.safe_load(raw) or {}
        name = spec["name"]
        if spec.get("kind") != "prompt":
            raise RuntimeError(f"{name}: kind must be prompt")

        skill_refs = []
        for s in spec.get("skills") or []:
            sname = s.get("name") if isinstance(s, dict) else str(s)
            if sname in skill_versions:
                skill_refs.append(SkillReference(name=sname, version=skill_versions[sname]))

        tools: list[Any] = []
        tools.extend(mcp_tools)
        tools.extend(openapi_tools)
        tools.append(
            MemorySearchPreviewTool(
                memory_store_name=memory_store,
                scope="user",
            )
        )

        definition = PromptAgentDefinition(
            model=model,
            instructions=spec.get("instructions") or f"You are {name}.",
            harness=harness,
            skills=skill_refs or None,
            tools=tools,
            rai_config=RaiConfig(rai_policy_name=rai_policy) if rai_policy else None,
        )
        created = project_client.agents.create_version(
            agent_name=name,
            definition=definition,
            description=spec.get("description") or spec.get("display_name") or name,
            metadata={
                "source": str(yml.relative_to(ROOT)),
                "harness": "microsoft_copilot_preview" if harness else "default_prompt",
                "toolbox": TOOLBOX_NAME,
            },
        )
        ver = getattr(created, "version", None) or getattr(created, "id", None) or "ok"
        print(f"Foundry agent version: {name} @ {ver} (model={model})")


def write_manifest(
    skills_blob: list[dict],
    skill_versions: dict[str, str],
    connections: dict[str, str],
) -> None:
    gateway = os.environ.get("APIM_GATEWAY_URL", "")
    manifest = {
        "foundry_project_endpoint": os.environ.get("FOUNDRY_PROJECT_ENDPOINT"),
        "model_deployment": agent_model(),
        "toolbox": TOOLBOX_NAME,
        "skills": [
            {**s, "foundry_version": skill_versions.get(s["name"])}
            for s in skills_blob
        ]
        or [{"name": n, "foundry_version": v} for n, v in skill_versions.items()],
        "tools": [
            {
                **t,
                "endpoint": f"{gateway}{t['path']}" if gateway else f"{functions_base()}{t['path']}",
                "auth": "apim_jwt_or_function_host",
            }
            for t in OPENAPI_TOOLS
        ],
        "mcps": [
            {
                "name": "github",
                "displayName": "GitHub Official MCP (Foundry catalog)",
                "url": "https://api.githubcopilot.com/mcp/",
                "project_connection": connections.get("github"),
                "via_foundry_connection": True,
            },
            {
                "name": "microsoft-learn",
                "displayName": "Microsoft Learn Docs MCP",
                "url": "https://learn.microsoft.com/api/mcp",
                "project_connection": connections.get("microsoft-learn"),
                "via_foundry_connection": True,
            },
            {
                "name": "servicenow",
                "displayName": "ServiceNow OSS MCP (Azure-hosted)",
                "url": os.environ.get("SERVICENOW_MCP_URL"),
                "project_connection": connections.get("servicenow"),
                "via_foundry_connection": bool(connections.get("servicenow")),
                "optional": True,
            },
            {
                "name": "mcp-afa-audit-store",
                "displayName": "Audit Store MCP",
                "url": functions_base(),
                "via_foundry_connection": False,
            },
        ],
        "memory_store": os.environ.get("MEMORY_STORE_NAME", "afa-agent-memory"),
        "auth": "workload_identity",
        "connections": connections,
    }
    out = ROOT / "agents" / "registry" / "catalog.generated.json"
    out.write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {out}")


def register_api_center_snapshot(skills: list[dict], connections: dict[str, str]) -> None:
    write_manifest(
        skills,
        {s["name"]: s.get("foundry_version") or "1" for s in skills if "name" in s},
        connections,
    )
    apic = os.environ.get("API_CENTER_NAME")
    if apic:
        print(f"API Center resource present: {apic} (catalog snapshot written)")


def main() -> int:
    load_env()
    print("Auth: Azure CLI / Managed Identity — real Foundry project deploy")
    endpoint = project_endpoint()
    credential = get_credential()

    from azure.ai.projects import AIProjectClient

    with AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project_client:
        skills_blob = upload_skills_blob(credential)
        skill_versions = deploy_foundry_skills(project_client)
        # merge versions into blob manifest entries
        for s in skills_blob:
            s["foundry_version"] = skill_versions.get(s["name"])
        memory_store = ensure_memory_store(project_client)
        mcp_tools, connections = build_mcp_tools(credential)
        openapi_tools = build_openapi_tools()
        deploy_toolbox(project_client, skill_versions, mcp_tools, openapi_tools)
        deploy_agents(
            project_client,
            skill_versions=skill_versions,
            memory_store=memory_store,
            mcp_tools=mcp_tools,
            openapi_tools=openapi_tools,
        )
        write_manifest(skills_blob, skill_versions, connections)
        register_api_center_snapshot(skills_blob, connections)

    print("Catalog registration complete (Foundry project).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
