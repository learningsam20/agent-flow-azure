output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}

output "apim_gateway_url" {
  value = azurerm_api_management.main.gateway_url
}

output "apim_name" {
  value = azurerm_api_management.main.name
}

output "api_center_name" {
  value = azapi_resource.api_center.name
}

output "api_center_id" {
  value = azapi_resource.api_center.id
}

output "foundry_endpoint" {
  value = azurerm_cognitive_account.foundry.endpoint
}

output "foundry_account_name" {
  value = azurerm_cognitive_account.foundry.name
}

output "foundry_project_name" {
  value = azurerm_cognitive_account_project.main.name
}

output "foundry_project_id" {
  value = azurerm_cognitive_account_project.main.id
}

# Real project data-plane endpoint for AIProjectClient
output "foundry_project_endpoint" {
  value = try(
    azurerm_cognitive_account_project.main.endpoints["AI Foundry API"],
    try(
      azurerm_cognitive_account_project.main.endpoints["AIProject"],
      "https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.main.name}"
    )
  )
}

output "foundry_model_deployment" {
  value       = var.foundry_agent_deployment
  description = "Deployment agents use (model-router for decider + failover)"
}

output "foundry_primary_model_deployment" {
  value = azurerm_cognitive_deployment.model_primary.name
}

output "foundry_embedding_deployment" {
  value = azurerm_cognitive_deployment.embedding.name
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.main.endpoint
}

output "storage_account_name" {
  value = azurerm_storage_account.main.name
}

output "function_app_name" {
  value = azurerm_linux_function_app.main.name
}

output "logic_app_name" {
  value = azurerm_logic_app_workflow.main.name
}

output "logic_app_names" {
  value = merge(
    { "multi-agent-flow" = azurerm_logic_app_workflow.main.name },
    { for k, v in azurerm_logic_app_workflow.catalog : k => v.name }
  )
}

output "logic_app_plan" {
  value       = "Consumption"
  description = "Cheapest Logic Apps hosting (pay-per-action; no WS1)"
}

output "function_app_location" {
  value = var.compute_location
}

output "azure_client_id" {
  value       = azurerm_user_assigned_identity.workload.client_id
  description = "Workload UAMI client ID — use with DefaultAzureCredential (no secrets)"
}

output "mcp_host" {
  value       = "https://${azurerm_linux_function_app.main.default_hostname}"
  description = "Custom MCP / audit tools hosted on Azure Functions (no ACA)"
}

output "entra_web_client_id" {
  value = azuread_application.web.client_id
}

output "entra_api_client_id" {
  value = azuread_application.api.client_id
}

output "entra_api_audience" {
  value = "api://agent-flow-azure"
}

output "entra_agent_blueprint_client_id" {
  value = azuread_application.agent_blueprint.client_id
}

output "content_safety_endpoint" {
  value = var.enable_content_safety ? azurerm_cognitive_account.content_safety[0].endpoint : ""
}

output "applicationinsights_connection_string" {
  value     = azurerm_application_insights.main.connection_string
  sensitive = true
}

output "applicationinsights_instrumentation_key" {
  value     = azurerm_application_insights.main.instrumentation_key
  sensitive = true
}

output "keycloak_url" {
  value = var.keycloak_url
}

output "keycloak_realm" {
  value = var.keycloak_realm
}

output "keycloak_api_audience" {
  value = var.keycloak_api_audience
}

output "memory_store_name" {
  value       = "afa-agent-memory"
  description = "Foundry memory store name (created by register_catalog.py against the project)"
}

output "servicenow_mcp_url" {
  value = var.enable_servicenow_mcp ? "https://${azurerm_container_app.servicenow_mcp[0].ingress[0].fqdn}/sse" : ""
  description = "Azure-hosted echelon-ai-labs/servicenow-mcp SSE endpoint (empty if disabled)"
}

output "servicenow_mcp_acr" {
  value = var.enable_servicenow_mcp ? azurerm_container_registry.mcp[0].login_server : ""
}
