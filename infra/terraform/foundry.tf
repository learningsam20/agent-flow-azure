# ---------------------------------------------------------------------------
# Microsoft Foundry: AIServices account + real project + model pool + router
# + Content Safety / RAI. Auth: Managed Identity only (no API keys).
# ---------------------------------------------------------------------------

resource "azurerm_cognitive_account" "foundry" {
  name                       = local.naming.foundry
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  kind                       = "AIServices"
  sku_name                   = "S0"
  custom_subdomain_name      = local.naming.foundry
  project_management_enabled = true
  local_auth_enabled         = false
  tags                       = local.tags

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }
}

resource "azurerm_cognitive_account_project" "main" {
  name                 = var.foundry_project_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  location             = azurerm_resource_group.main.location
  display_name         = "${var.project_short} agents (${var.environment})"
  description          = "Real Foundry project for agent-flow-azure prompt agents, skills, toolbox, MCP"

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }

  tags = local.tags

  # Avoid 409 RequestConflict while model deployments / account updates are still running
  depends_on = [
    azurerm_cognitive_deployment.model_primary,
    azurerm_cognitive_deployment.embedding,
    azurerm_cognitive_account_rai_policy.default,
  ]
}

# Primary cheap chat model (also in router subset)
resource "azurerm_cognitive_deployment" "model_primary" {
  name                 = var.foundry_model_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = var.foundry_model_name
    version = var.foundry_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.foundry_model_capacity
  }
}

# Mid-tier fallback (only when model-router enabled and quota exists)
resource "azurerm_cognitive_deployment" "model_fallback_mid" {
  count                = var.enable_model_router ? 1 : 0
  name                 = var.foundry_fallback_mid_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = var.foundry_fallback_mid_name
    version = var.foundry_fallback_mid_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = 1
  }
}

# Optional third pool model (skip when same as mid or no quota)
resource "azurerm_cognitive_deployment" "model_fallback_strong" {
  count                = var.enable_foundry_fallback_strong && var.foundry_fallback_strong_name != var.foundry_fallback_mid_name ? 1 : 0
  name                 = var.foundry_fallback_strong_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = var.foundry_fallback_strong_name
    version = var.foundry_fallback_strong_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}

# Embeddings for Foundry memory store
resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.foundry_embedding_deployment
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = var.foundry_embedding_deployment
    version = var.foundry_embedding_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}

# Model router = decider + automatic failover (requires quota for ≥2 pool models)
resource "azapi_resource" "model_router" {
  count                     = var.enable_model_router ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  name                      = "model-router"
  parent_id                 = azurerm_cognitive_account.foundry.id
  schema_validation_enabled = false

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = 1
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "model-router"
        version = var.foundry_router_version
      }
      routing = {
        mode = var.foundry_router_mode
        models = concat(
          [
            {
              format  = "OpenAI"
              name    = var.foundry_model_name
              version = var.foundry_model_version
            },
            {
              format  = "OpenAI"
              name    = var.foundry_fallback_mid_name
              version = var.foundry_fallback_mid_version
            }
          ],
          var.enable_foundry_fallback_strong && var.foundry_fallback_strong_name != var.foundry_fallback_mid_name ? [
            {
              format  = "OpenAI"
              name    = var.foundry_fallback_strong_name
              version = var.foundry_fallback_strong_version
            }
          ] : []
        )
      }
    }
  }

  depends_on = [
    azurerm_cognitive_deployment.model_primary,
    azurerm_cognitive_deployment.model_fallback_mid,
    azurerm_cognitive_deployment.model_fallback_strong,
  ]
}

# Content Safety (Foundry Guardrails companion)
resource "azurerm_cognitive_account" "content_safety" {
  count                 = var.enable_content_safety ? 1 : 0
  name                  = "${var.project_short}-${var.environment}-cs-${var.location_short}-${var.name_suffix}"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  kind                  = "ContentSafety"
  sku_name              = "F0"
  custom_subdomain_name = "${var.project_short}-${var.environment}-cs-${var.location_short}-${var.name_suffix}"
  tags                  = local.tags
  local_auth_enabled    = false

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }
}

# Default RAI / content filter policy on the Foundry account (Prompt Shields when enabled)
resource "azurerm_cognitive_account_rai_policy" "default" {
  count                = var.enable_prompt_shields ? 1 : 0
  name                 = "afa-default-rai"
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  base_policy_name     = "Microsoft.DefaultV2"

  content_filter {
    name               = "Hate"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Prompt"
  }
  content_filter {
    name               = "Hate"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Completion"
  }
  content_filter {
    name               = "Sexual"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Prompt"
  }
  content_filter {
    name               = "Sexual"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Completion"
  }
  content_filter {
    name               = "Selfharm"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Prompt"
  }
  content_filter {
    name               = "Selfharm"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Completion"
  }
  content_filter {
    name               = "Violence"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Prompt"
  }
  content_filter {
    name               = "Violence"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Medium"
    source             = "Completion"
  }
  content_filter {
    name               = "Jailbreak"
    filter_enabled     = true
    block_enabled      = true
    severity_threshold = "Low"
    source             = "Prompt"
  }
}

resource "azurerm_role_assignment" "foundry_openai_user_workload" {
  scope                = azurerm_cognitive_account.foundry.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

resource "azurerm_role_assignment" "foundry_openai_user_apim" {
  scope                = azurerm_cognitive_account.foundry.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_api_management.main.identity[0].principal_id
  depends_on           = [azurerm_api_management.main]
}

# Project-scoped Azure AI Developer for workload + APIM (agents / skills / toolbox)
resource "azurerm_role_assignment" "foundry_project_ai_user_workload" {
  scope                = azurerm_cognitive_account_project.main.id
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

resource "azurerm_role_assignment" "foundry_project_ai_user_apim" {
  scope                = azurerm_cognitive_account_project.main.id
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_api_management.main.identity[0].principal_id
  depends_on           = [azurerm_api_management.main]
}

resource "azurerm_role_assignment" "content_safety_user_apim" {
  count                = var.enable_content_safety ? 1 : 0
  scope                = azurerm_cognitive_account.content_safety[0].id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_api_management.main.identity[0].principal_id
  depends_on           = [azurerm_api_management.main]
}

resource "azurerm_role_assignment" "content_safety_user_workload" {
  count                = var.enable_content_safety ? 1 : 0
  scope                = azurerm_cognitive_account.content_safety[0].id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}
