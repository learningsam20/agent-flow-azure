#!/usr/bin/env bash
# Bootstrap App.Admin / App.User in Entra (when possible) and Keycloak.
# Falls back to Keycloak-native users from realm import if Entra Graph create fails.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

load_env
check_azure_login

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:5750}"
REALM="${KEYCLOAK_REALM:-agent-flow}"
ADMIN_USER="${KEYCLOAK_ADMIN:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

info "Ensuring Keycloak is reachable at ${KEYCLOAK_URL}"
if ! curl -sf "${KEYCLOAK_URL}/realms/master" >/dev/null 2>&1; then
  info "Starting Keycloak via docker compose"
  docker compose -f "${ROOT_DIR}/infra/keycloak/docker-compose.yml" up -d
  for i in $(seq 1 36); do
    if curl -sf "${KEYCLOAK_URL}/realms/${REALM}" >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
fi
curl -sf "${KEYCLOAK_URL}/realms/${REALM}" >/dev/null || die "Keycloak realm ${REALM} not available"

# Apply long-lived token settings on the *running* realm (import only runs on first boot).
info "Setting Keycloak access token TTL to 8h (and SSO session timeouts)"
python3 - <<PY || warn "Could not update Keycloak token TTL (re-run after Keycloak is up, or set in admin console)"
import json, os, urllib.parse, urllib.request

base = os.environ.get("KEYCLOAK_URL", "${KEYCLOAK_URL}").rstrip("/")
realm = os.environ.get("KEYCLOAK_REALM", "${REALM}")
admin_user = os.environ.get("KEYCLOAK_ADMIN", "${ADMIN_USER}")
admin_pass = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "${ADMIN_PASS}")

def req(method, url, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if data is None else json.dumps(data).encode()
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None

form = urllib.parse.urlencode({
    "client_id": "admin-cli",
    "username": admin_user,
    "password": admin_pass,
    "grant_type": "password",
}).encode()
tr = urllib.request.Request(
    f"{base}/realms/master/protocol/openid-connect/token",
    data=form,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    method="POST",
)
with urllib.request.urlopen(tr, timeout=20) as resp:
    access = json.loads(resp.read())["access_token"]

realm_doc = req("GET", f"{base}/admin/realms/{realm}", token=access)
realm_doc.update({
    "accessTokenLifespan": 28800,
    "accessTokenLifespanForImplicitFlow": 28800,
    "ssoSessionIdleTimeout": 86400,
    "ssoSessionMaxLifespan": 604800,
    "clientSessionIdleTimeout": 86400,
    "clientSessionMaxLifespan": 604800,
})
req("PUT", f"{base}/admin/realms/{realm}", data=realm_doc, token=access)
clients = req("GET", f"{base}/admin/realms/{realm}/clients?clientId=afa-web", token=access) or []
if clients:
    cid = clients[0]["id"]
    client = req("GET", f"{base}/admin/realms/{realm}/clients/{cid}", token=access)
    attrs = dict(client.get("attributes") or {})
    attrs["access.token.lifespan"] = "28800"
    client["attributes"] = attrs
    req("PUT", f"{base}/admin/realms/{realm}/clients/{cid}", data=client, token=access)
print("Keycloak token TTL set to 28800s")
PY

ENTRA_OK=0
info "Attempting Entra user bootstrap via Microsoft Graph"
python3 - <<'PY' && ENTRA_OK=1 || true
import json, os, subprocess, sys, uuid

tenant = os.environ.get("AZURE_TENANT_ID", "")
api_app = os.environ.get("ENTRA_API_CLIENT_ID", "")
users = [
    {"upn_prefix": "afa-admin", "display": "AFA Admin", "roles": ["App.Admin", "App.User", "Agent.Admin"]},
    {"upn_prefix": "afa-developer", "display": "AFA Developer", "roles": ["App.User", "Agent.Developer"]},
    {"upn_prefix": "afa-auditor", "display": "AFA Auditor", "roles": ["App.User", "Agent.SecurityAuditor"]},
]

# Resolve verified domain
r = subprocess.run(
    ["az", "rest", "--method", "get",
     "--url", "https://graph.microsoft.com/v1.0/domains?$select=id,isDefault,isVerified"],
    capture_output=True, text=True,
)
if r.returncode != 0:
    print("Graph domains failed:", r.stderr, file=sys.stderr)
    raise SystemExit(1)
domains = json.loads(r.stdout).get("value", [])
domain = next((d["id"] for d in domains if d.get("isDefault") and d.get("isVerified")), None)
domain = domain or next((d["id"] for d in domains if d.get("isVerified")), None)
if not domain:
    print("No verified domain", file=sys.stderr)
    raise SystemExit(1)

# Resolve app role IDs from service principal
sp = subprocess.run(
    ["az", "ad", "sp", "show", "--id", api_app, "-o", "json"],
    capture_output=True, text=True,
)
if sp.returncode != 0:
    print("SP show failed — set ENTRA_API_CLIENT_ID from .env.generated", file=sys.stderr)
    raise SystemExit(1)
spj = json.loads(sp.stdout)
role_map = {ar["value"]: ar["id"] for ar in spj.get("appRoles", []) if ar.get("value")}
sp_oid = spj["id"]

created = []
for u in users:
    upn = f"{u['upn_prefix']}@{domain}"
    password = f"Afa!{uuid.uuid4().hex[:10]}"
    body = {
        "accountEnabled": True,
        "displayName": u["display"],
        "mailNickname": u["upn_prefix"],
        "userPrincipalName": upn,
        "passwordProfile": {"forceChangePasswordNextSignIn": False, "password": password},
    }
    # create or get
    existing = subprocess.run(
        ["az", "ad", "user", "show", "--id", upn, "-o", "json"],
        capture_output=True, text=True,
    )
    if existing.returncode == 0:
        user = json.loads(existing.stdout)
        print(f"Entra user exists: {upn}")
    else:
        cr = subprocess.run(
            ["az", "ad", "user", "create",
             "--display-name", u["display"],
             "--user-principal-name", upn,
             "--password", password,
             "--force-change-password-next-sign-in", "false",
             "-o", "json"],
            capture_output=True, text=True,
        )
        if cr.returncode != 0:
            print("Create failed:", cr.stderr, file=sys.stderr)
            raise SystemExit(1)
        user = json.loads(cr.stdout)
        print(f"Created Entra user: {upn} password={password}")
    created.append({"upn": upn, "id": user["id"], "roles": u["roles"]})

    for role in u["roles"]:
        rid = role_map.get(role)
        if not rid:
            print(f"  skip missing app role {role}")
            continue
        assign = {
            "principalId": user["id"],
            "resourceId": sp_oid,
            "appRoleId": rid,
        }
        path = f"/tmp/afa-role-{user['id']}-{rid}.json"
        open(path, "w").write(json.dumps(assign))
        ar = subprocess.run(
            ["az", "rest", "--method", "post",
             "--url", f"https://graph.microsoft.com/v1.0/users/{user['id']}/appRoleAssignments",
             "--body", f"@{path}"],
            capture_output=True, text=True,
        )
        if ar.returncode != 0 and "Permission being assigned already exists" not in (ar.stderr + ar.stdout):
            print(f"  role {role}: {ar.stderr or ar.stdout}")
        else:
            print(f"  assigned {role}")

print(json.dumps({"ok": True, "users": [{"upn": c["upn"], "roles": c["roles"]} for c in created]}))
PY

if [[ "${ENTRA_OK}" == "1" ]]; then
  info "Entra bootstrap succeeded — configure Keycloak Entra IdP for SSO (admin console → Identity providers)"
else
  warn "Entra bootstrap skipped/failed — using Keycloak-native users from realm import"
fi

info "Keycloak realm users (from import / console):"
info "  admin@agent-flow.local / ChangeMeAdmin1!  → App.Admin + Agent.Admin"
info "  developer@agent-flow.local / ChangeMeUser1! → App.User + Agent.Developer"
info "  auditor@agent-flow.local / ChangeMeUser2! → App.User + Agent.SecurityAuditor"
info "Keycloak admin console: ${KEYCLOAK_URL} (admin / ${ADMIN_PASS})"
info "Bootstrap complete. Admins can add more users in Entra/Keycloak and assign Agent.* in the app."
