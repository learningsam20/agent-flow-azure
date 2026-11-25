# ---------------------------------------------------------------------------
# Functions (Y1 Consumption) + Logic Apps Consumption (cheapest — no WS1 plan)
# Custom MCP hosted on Functions. Functions compute uses compute_location
# because Y1 quota is 0 in some regions (e.g. eastus2) while westus2 works.
# ---------------------------------------------------------------------------

resource "azurerm_service_plan" "functions" {
  name                = "${var.project_short}-${var.environment}-asp-func-${var.location_short}-${var.name_suffix}"
  location            = var.compute_location
  resource_group_name = azurerm_resource_group.main.name
  os_type             = "Linux"
  sku_name            = "Y1"
  tags                = local.tags
}

resource "azurerm_linux_function_app" "main" {
  name                 = local.naming.func
  location             = var.compute_location
  resource_group_name  = azurerm_resource_group.main.name
  service_plan_id      = azurerm_service_plan.functions.id
  storage_account_name = azurerm_storage_account.main.name

  # Host still needs a storage key reference for the Functions runtime platform;
  # app code never uses it — data plane uses DefaultAzureCredential / UAMI.
  storage_account_access_key = azurerm_storage_account.main.primary_access_key

  https_only                    = true
  functions_extension_version   = "~4"
  public_network_access_enabled = true
  tags                          = local.tags

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }

  site_config {
    application_stack {
      python_version = "3.11"
    }
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_insights_key               = azurerm_application_insights.main.instrumentation_key
  }

  app_settings = {
    AzureWebJobsFeatureFlags              = "EnableWorkerIndexing"
    FUNCTIONS_WORKER_RUNTIME              = "python"
    AZURE_CLIENT_ID                       = azurerm_user_assigned_identity.workload.client_id
    KEY_VAULT_URI                         = azurerm_key_vault.main.vault_uri
    COSMOS_ENDPOINT                       = azurerm_cosmosdb_account.main.endpoint
    STORAGE_ACCOUNT_NAME                  = azurerm_storage_account.main.name
    APIM_GATEWAY_URL                      = azurerm_api_management.main.gateway_url
    API_CENTER_NAME                       = azapi_resource.api_center.name
    RESOURCE_GROUP_NAME                   = azurerm_resource_group.main.name
    WEBSITE_RUN_FROM_PACKAGE              = "1"
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    APPINSIGHTS_INSTRUMENTATIONKEY        = azurerm_application_insights.main.instrumentation_key
  }
}

resource "azurerm_role_assignment" "func_kv" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_linux_function_app.main.identity[0].principal_id
}

# Logic Apps Consumption — one workflow resource per catalog flow (pay-per-execution).
locals {
  logic_app_flows = {
    "multi-agent-flow"       = local.naming.logic
    "security-incident-flow" = "${var.project_short}-${var.environment}-logic-sec-${var.location_short}-${var.name_suffix}"
    "finops-approval-flow"   = "${var.project_short}-${var.environment}-logic-fin-${var.location_short}-${var.name_suffix}"
  }
}

resource "azurerm_logic_app_workflow" "main" {
  # Keep existing primary name for multi-agent-flow (state-compatible).
  name                = local.naming.logic
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = merge(local.tags, { flow_id = "multi-agent-flow" })

  identity {
    type = "SystemAssigned"
  }

  # Definition is deployed by install.sh from workflows/logic-app-standard/.
  enabled = true
}

resource "azurerm_logic_app_workflow" "catalog" {
  for_each = {
    for flow_id, name in local.logic_app_flows : flow_id => name
    if flow_id != "multi-agent-flow"
  }

  name                = each.value
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = merge(local.tags, { flow_id = each.key })

  identity {
    type = "SystemAssigned"
  }

  enabled = true
}

resource "azurerm_role_assignment" "logic_kv" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_logic_app_workflow.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "logic_catalog_kv" {
  for_each             = azurerm_logic_app_workflow.catalog
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = each.value.identity[0].principal_id
}
