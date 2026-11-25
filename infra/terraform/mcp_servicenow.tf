# ---------------------------------------------------------------------------
# Optional: host ServiceNow OSS MCP on Azure Container Apps (cheap Consumption)
# Enable with -var="enable_servicenow_mcp=true" after setting SERVICENOW_* secrets.
# ---------------------------------------------------------------------------

resource "azurerm_container_registry" "mcp" {
  count               = var.enable_servicenow_mcp ? 1 : 0
  name                = replace("${var.project_short}${var.environment}acrmcp${var.name_suffix}", "-", "")
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = true
  tags                = local.tags
}

resource "azurerm_container_app_environment" "mcp" {
  count                      = var.enable_servicenow_mcp ? 1 : 0
  name                       = "${var.project_short}-${var.environment}-cae-${var.location_short}-${var.name_suffix}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

resource "azurerm_container_app" "servicenow_mcp" {
  count                        = var.enable_servicenow_mcp ? 1 : 0
  name                         = "${var.project_short}-${var.environment}-snmcp-${var.location_short}-${var.name_suffix}"
  container_app_environment_id = azurerm_container_app_environment.mcp[0].id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }

  # Secrets are injected at deploy time from Key Vault values (install.sh seeds them).
  # Placeholder values allow first apply before secrets exist; update via deploy script.
  # echelon-ai-labs/servicenow-mcp (SSE on :8080)
  secret {
    name  = "servicenow-instance-url"
    value = var.servicenow_instance_url != "" ? var.servicenow_instance_url : "https://placeholder.service-now.com"
  }
  secret {
    name  = "servicenow-username"
    value = var.servicenow_username != "" ? var.servicenow_username : "placeholder"
  }
  secret {
    name  = "servicenow-password"
    value = var.servicenow_password != "" ? var.servicenow_password : "placeholder"
  }

  # Placeholder public image so first apply succeeds before ACR build.
  # ./scripts/deploy_servicenow_mcp.sh replaces with echelon-ai-labs/servicenow-mcp:latest
  template {
    min_replicas = 0
    max_replicas = 3
    container {
      name   = "servicenow-mcp"
      image  = "mcr.microsoft.com/k8se/quickstart:latest"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name        = "SERVICENOW_INSTANCE_URL"
        secret_name = "servicenow-instance-url"
      }
      env {
        name        = "SERVICENOW_USERNAME"
        secret_name = "servicenow-username"
      }
      env {
        name        = "SERVICENOW_PASSWORD"
        secret_name = "servicenow-password"
      }
      env {
        name  = "SERVICENOW_AUTH_TYPE"
        value = "basic"
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 80
    transport        = "http"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
      ingress[0].target_port,
      registry,
      secret,
    ]
  }
}

resource "azurerm_role_assignment" "acr_pull_workload" {
  count                = var.enable_servicenow_mcp ? 1 : 0
  scope                = azurerm_container_registry.mcp[0].id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

resource "azurerm_role_assignment" "acr_push_workload" {
  count                = var.enable_servicenow_mcp ? 1 : 0
  scope                = azurerm_container_registry.mcp[0].id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}
