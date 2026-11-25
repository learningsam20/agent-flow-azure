# ---------------------------------------------------------------------------
# Data plane + Key Vault — all access via Managed Identity (no account keys)
# ---------------------------------------------------------------------------

data "azurerm_client_config" "current" {}

# Intentionally no Log Analytics / App Insights by default — ingestion is costly for experiments.
# Enable later via a dedicated observability stack if needed.

resource "azurerm_key_vault" "main" {
  name                       = local.naming.kv
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  rbac_authorization_enabled = true
  tags                       = local.tags

  network_acls {
    default_action = "Allow"
    bypass         = "AzureServices"
  }
}

# Deployer can seed secrets during install.sh
resource "azurerm_role_assignment" "kv_admin_deployer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Workload identity reads secrets only (no keys exported to apps)
resource "azurerm_role_assignment" "kv_secrets_user_workload" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

resource "azurerm_storage_account" "main" {
  name                     = substr(local.naming.st, 0, 24)
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  min_tls_version          = "TLS1_2"

  # Prefer Entra ID; shared key retained only for Functions host lifecycle (Azure requirement)
  shared_access_key_enabled       = true
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true
  }
}

resource "azurerm_storage_container" "skills" {
  name                  = "skills"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "artifacts" {
  name                  = "artifacts"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_storage_table" "runs" {
  name               = "runhistory"
  storage_account_id = azurerm_storage_account.main.id
}

# Deployer uploads SKILL.md catalog during install.sh
resource "azurerm_role_assignment" "storage_blob_deployer" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "storage_blob_workload" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

resource "azurerm_role_assignment" "storage_table_workload" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

# Cosmos DB Free Tier (1 per subscription) — AAD auth only for data plane
resource "azurerm_cosmosdb_account" "main" {
  name                = local.naming.cosmos
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  tags                = local.tags
  free_tier_enabled   = true

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }

  # Disable local auth / keys — enforce Entra ID + RBAC (workload identity)
  local_authentication_enabled = false
}

resource "azurerm_cosmosdb_sql_database" "workflow" {
  name                = "workflow"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "checkpoints" {
  name                = "checkpoints"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.workflow.name
  partition_key_paths = ["/runId"]
}

resource "azurerm_cosmosdb_sql_container" "runs" {
  name                = "runs"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.workflow.name
  partition_key_paths = ["/userOid"]
}

resource "azurerm_cosmosdb_sql_container" "user_roles" {
  name                = "userRoles"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.workflow.name
  partition_key_paths = ["/userOid"]
}

resource "azurerm_cosmosdb_sql_container" "learnings" {
  name                = "learnings"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.workflow.name
  partition_key_paths = ["/userOid"]
}

# Cosmos DB Built-in Data Contributor
resource "azurerm_cosmosdb_sql_role_assignment" "workload" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_user_assigned_identity.workload.principal_id
  scope               = azurerm_cosmosdb_account.main.id
}

resource "azurerm_cosmosdb_sql_role_assignment" "deployer" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = azurerm_cosmosdb_account.main.id
}
