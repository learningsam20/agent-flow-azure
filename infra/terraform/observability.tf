# ---------------------------------------------------------------------------
# Application Insights + Log Analytics — UI → backend → agent → tool traces
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.project_short}-${var.environment}-law-${var.location_short}-${var.name_suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "${var.project_short}-${var.environment}-ai-${var.location_short}-${var.name_suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

# Foundry project → App Insights connection enables server-side agent tracing
# ("tracer not attached" in the portal until this exists).
resource "azapi_resource" "foundry_app_insights_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = "app-insights"
  parent_id                 = azurerm_cognitive_account_project.main.id
  schema_validation_enabled = false

  body = {
    properties = {
      category      = "AppInsights"
      authType      = "ApiKey"
      target        = azurerm_application_insights.main.id
      isSharedToAll = true
      credentials = {
        key = azurerm_application_insights.main.connection_string
      }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.main.id
      }
    }
  }
}

# Project system-assigned MI can publish traces to App Insights
resource "azurerm_role_assignment" "foundry_project_appinsights_metrics" {
  scope                = azurerm_application_insights.main.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_cognitive_account_project.main.identity[0].principal_id
}
