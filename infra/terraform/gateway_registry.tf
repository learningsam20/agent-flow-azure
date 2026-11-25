# ---------------------------------------------------------------------------
# Azure API Management (Consumption) as AI Gateway + Azure API Center (Free)
# JWT validation + RBAC ACL — Managed Identity to backends (no subscription keys required for MI path)
# ---------------------------------------------------------------------------

resource "azurerm_api_management" "main" {
  name                = local.naming.apim
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  publisher_name      = "agent-flow-azure"
  publisher_email     = "platform@example.com"
  sku_name            = "Consumption_0"
  tags                = local.tags

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }
}

resource "azurerm_api_management_named_value" "acl_matrix" {
  name                = "acl-matrix"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "acl-matrix"
  value               = local.acl_matrix
}

resource "azurerm_api_management_named_value" "tenant_id" {
  name                = "entra-tenant-id"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "entra-tenant-id"
  value               = var.tenant_id
}

resource "azurerm_api_management_named_value" "api_audience" {
  name                = "entra-api-audience"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "entra-api-audience"
  value               = "api://agent-flow-azure"
}

resource "azurerm_api_management_named_value" "keycloak_openid_config" {
  name                = "keycloak-openid-config"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "keycloak-openid-config"
  # APIM must reach this URL from Azure. localhost Keycloak fails policy validation —
  # fall back to Entra OIDC metadata so apply succeeds; set KEYCLOAK_PUBLIC_URL for real APIM JWT.
  value = (
    startswith(var.keycloak_url, "http://localhost") || startswith(var.keycloak_url, "http://127.0.0.1")
    ) ? "https://login.microsoftonline.com/${var.tenant_id}/v2.0/.well-known/openid-configuration" : (
    "${trimsuffix(var.keycloak_url, "/")}/realms/${var.keycloak_realm}/.well-known/openid-configuration"
  )
}

resource "azurerm_api_management_named_value" "keycloak_api_audience" {
  name                = "keycloak-api-audience"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "keycloak-api-audience"
  value               = var.keycloak_api_audience
}

resource "azurerm_api_management_named_value" "foundry_model_deployment" {
  name                = "foundry-model-deployment"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "foundry-model-deployment"
  value               = var.foundry_agent_deployment
}

resource "azurerm_api_management_named_value" "foundry_project_endpoint" {
  name                = "foundry-project-endpoint"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "foundry-project-endpoint"
  value = try(
    azurerm_cognitive_account_project.main.endpoints["AI Foundry API"],
    try(
      azurerm_cognitive_account_project.main.endpoints["AIProject"],
      "https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.main.name}"
    )
  )
}

resource "azurerm_api_management_named_value" "foundry_project_name" {
  name                = "foundry-project-name"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  display_name        = "foundry-project-name"
  value               = azurerm_cognitive_account_project.main.name
}

resource "azurerm_api_management_api" "agents" {
  name                  = "agent-flow-api"
  resource_group_name   = azurerm_resource_group.main.name
  api_management_name   = azurerm_api_management.main.name
  revision              = "1"
  display_name          = "Agent Flow Azure API"
  path                  = "api"
  protocols             = ["https"]
  subscription_required = false
  # Operations added explicitly — OpenAPI import validation is brittle on Consumption APIM
}

resource "azurerm_api_management_api_operation" "chat_agent" {
  operation_id        = "chatAgent"
  api_name            = azurerm_api_management_api.agents.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Chat Agent"
  method              = "POST"
  url_template        = "/agents/{agentId}/chat"

  template_parameter {
    name     = "agentId"
    type     = "string"
    required = true
  }

  response {
    status_code = 200
  }
}

resource "azurerm_api_management_api_operation" "run_flow" {
  operation_id        = "runFlow"
  api_name            = azurerm_api_management_api.agents.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Run Flow"
  method              = "POST"
  url_template        = "/flows/{flowId}/run"

  template_parameter {
    name     = "flowId"
    type     = "string"
    required = true
  }

  response {
    status_code = 202
  }
}

resource "azurerm_api_management_api_operation" "approve_hitl" {
  operation_id        = "approveHitl"
  api_name            = azurerm_api_management_api.agents.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Approve HITL"
  method              = "POST"
  url_template        = "/flows/{runId}/approve"

  template_parameter {
    name     = "runId"
    type     = "string"
    required = true
  }

  response {
    status_code = 200
  }
}

resource "azurerm_api_management_api_operation" "cancel_flow" {
  operation_id        = "cancelFlow"
  api_name            = azurerm_api_management_api.agents.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Cancel Flow"
  method              = "POST"
  url_template        = "/flows/{runId}/cancel"

  template_parameter {
    name     = "runId"
    type     = "string"
    required = true
  }

  response {
    status_code = 200
  }
}

resource "azurerm_api_management_api_operation" "list_skills" {
  operation_id        = "listSkills"
  api_name            = azurerm_api_management_api.agents.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "List Skills"
  method              = "GET"
  url_template        = "/registry/skills"

  response {
    status_code = 200
  }
}

resource "azurerm_api_management_api_policy" "agent_interceptor" {
  api_name            = azurerm_api_management_api.agents.name
  api_management_name = azurerm_api_management.main.name
  resource_group_name = azurerm_resource_group.main.name
  xml_content         = file("${path.module}/../apim/policies/agent-interceptor.xml")
}

resource "azurerm_api_management_backend" "foundry" {
  name                = "foundry-backend"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  protocol            = "http"
  # Project data-plane host (Responses / agents) — not the raw cognitiveservices account URL
  url = trimsuffix(
    try(
      azurerm_cognitive_account_project.main.endpoints["AI Foundry API"],
      try(
        azurerm_cognitive_account_project.main.endpoints["AIProject"],
        "https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.main.name}"
      )
    ),
    "/"
  )
}

resource "azurerm_api_management_backend" "functions" {
  name                = "functions-backend"
  resource_group_name = azurerm_resource_group.main.name
  api_management_name = azurerm_api_management.main.name
  protocol            = "http"
  url                 = "https://${azurerm_linux_function_app.main.default_hostname}"
}

# Azure API Center — Free tier (via AzAPI; not yet a first-class azurerm resource)
resource "azapi_resource" "api_center" {
  type                      = "Microsoft.ApiCenter/services@2024-03-01"
  name                      = local.naming.apic
  parent_id                 = azurerm_resource_group.main.id
  location                  = azurerm_resource_group.main.location
  tags                      = local.tags
  schema_validation_enabled = false

  body = {
    sku = {
      name = "Free"
    }
    identity = {
      type = "SystemAssigned,UserAssigned"
      userAssignedIdentities = {
        (azurerm_user_assigned_identity.workload.id) = {}
      }
    }
    properties = {}
  }

  response_export_values = ["id", "name", "identity"]
}

resource "azurerm_role_assignment" "apic_contributor_workload" {
  scope                = azapi_resource.api_center.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}
