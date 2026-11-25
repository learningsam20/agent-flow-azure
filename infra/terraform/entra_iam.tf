# ---------------------------------------------------------------------------
# Entra ID App Registrations, App Roles, Agent Identity Blueprint (no secrets)
# Auth: authorization code + PKCE for web; managed identity / FIC for agents
# ---------------------------------------------------------------------------

resource "azuread_application" "api" {
  display_name     = "${var.project_short}-${var.environment}-api"
  sign_in_audience = "AzureADMyOrg"
  identifier_uris  = ["api://agent-flow-azure"]

  api {
    mapped_claims_enabled          = true
    requested_access_token_version = 2

    oauth2_permission_scope {
      admin_consent_description  = "Access agent-flow-azure APIs as the signed-in user"
      admin_consent_display_name = "Access agent-flow-azure"
      enabled                    = true
      id                         = "11111111-1111-1111-1111-111111111111"
      type                       = "User"
      user_consent_description   = "Access agent-flow-azure on your behalf"
      user_consent_display_name  = "Access agent-flow-azure"
      value                      = "access_as_user"
    }
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Platform administrator (required app role)"
    display_name         = "App Admin"
    enabled              = true
    id                   = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    value                = "App.Admin"
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Standard application user (required app role)"
    display_name         = "App User"
    enabled              = true
    id                   = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    value                = "App.User"
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Full agent ACL — all agents, skills, MCPs, HITL"
    display_name         = "Agent Admin"
    enabled              = true
    id                   = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    value                = "Agent.Admin"
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Developer — triage, repo-ops, synthesizer"
    display_name         = "Agent Developer"
    enabled              = true
    id                   = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    value                = "Agent.Developer"
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Security auditor — triage, sec-audit, synthesizer, HITL"
    display_name         = "Agent Security Auditor"
    enabled              = true
    id                   = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    value                = "Agent.SecurityAuditor"
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Viewer — triage + synthesizer read-only"
    display_name         = "Agent Viewer"
    enabled              = true
    id                   = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    value                = "Agent.Viewer"
  }

  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "api" {
  client_id = azuread_application.api.client_id
}

resource "azuread_application" "web" {
  display_name     = "${var.project_short}-${var.environment}-web"
  sign_in_audience = "AzureADMyOrg"

  single_page_application {
    # SPA redirect URIs must include a trailing slash when there is no path segment
    redirect_uris = [
      "${trimsuffix(var.web_redirect_uri, "/")}/",
    ]
  }

  required_resource_access {
    resource_app_id = azuread_application.api.client_id

    resource_access {
      id   = "11111111-1111-1111-1111-111111111111"
      type = "Scope"
    }
  }

  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000"

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "web" {
  client_id = azuread_application.web.client_id
}

# Federated credential placeholder for GitHub Actions / CI workload identity (no client secret)
resource "azuread_application_federated_identity_credential" "ci" {
  application_id = azuread_application.api.id
  display_name   = "github-actions-oidc"
  description    = "Workload identity federation for CI (no client secrets)"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = "repo:REPLACE_ORG/agent-flow-azure:ref:refs/heads/main"
}

# Agent Identity Blueprint app (credential = federated / managed identity only)
resource "azuread_application" "agent_blueprint" {
  display_name     = "${var.project_short}-${var.environment}-agent-blueprint"
  sign_in_audience = "AzureADMyOrg"

  # Agent Identity Blueprint marker — Graph APIs may require additional properties
  # applied post-provision by install/register_catalog.py when tenant supports Agent ID.
  tags = ["agent-flow-azure", "agent-identity-blueprint"]
}

resource "azuread_service_principal" "agent_blueprint" {
  client_id = azuread_application.agent_blueprint.client_id
}

# Agent Blueprint credentials: configure Federated Identity Credential against the
# workload UAMI via Microsoft Graph / install.sh (tenant Agent ID preview APIs).
# Do NOT create client secrets — Workload Identity Federation only.
