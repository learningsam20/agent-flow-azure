locals {
  naming = {
    rg      = "rg-agent-flow-azure-${var.environment}"
    foundry = "${var.project_short}-${var.environment}-foundry-${var.location_short}-${var.name_suffix}"
    apim    = "${var.project_short}-${var.environment}-apim-${var.location_short}-${var.name_suffix}"
    apic    = "${var.project_short}-${var.environment}-apic-${var.location_short}-${var.name_suffix}"
    kv      = "${var.project_short}-${var.environment}-kv-${var.location_short}-${var.name_suffix}"
    st      = replace("${var.project_short}${var.environment}st${var.location_short}${var.name_suffix}", "-", "")
    cosmos  = "${var.project_short}-${var.environment}-cosmos-${var.location_short}-${var.name_suffix}"
    func    = "${var.project_short}-${var.environment}-func-${var.location_short}-${var.name_suffix}"
    logic   = "${var.project_short}-${var.environment}-logic-${var.location_short}-${var.name_suffix}"
    uami = "${var.project_short}-${var.environment}-uami-${var.location_short}-${var.name_suffix}"
  }

  mandatory_tags = {
    project     = "agent-flow-azure"
    environment = var.environment
    managed_by  = "terraform"
    owner       = "platform-engineering"
    cost_center = "ai-experiment"
  }

  tags = merge(local.mandatory_tags, var.tags)

  # Role → agents ACL matrix (synced to APIM named value)
  acl_matrix = jsonencode({
    "Agent.Admin" = {
      agents = ["triage-router-agent", "repo-ops-agent", "sec-audit-agent", "finops-analyst-agent", "exec-synthesizer-agent"]
      skills = ["*"]
      mcps   = ["github", "microsoft-learn", "mcp-afa-audit-store"]
      hitl   = true
    }
    "Agent.Developer" = {
      agents = ["triage-router-agent", "repo-ops-agent", "exec-synthesizer-agent"]
      skills = ["intent-classifier", "github-issue-creator", "mcp-builder", "research-brief"]
      mcps   = ["github", "mcp-afa-audit-store"]
      hitl   = false
    }
    "Agent.SecurityAuditor" = {
      agents = ["triage-router-agent", "sec-audit-agent", "exec-synthesizer-agent"]
      skills = ["intent-classifier", "owasp-security-audit", "research-brief"]
      mcps   = ["microsoft-learn", "mcp-afa-audit-store"]
      hitl   = true
    }
    "Agent.Viewer" = {
      agents = ["triage-router-agent", "exec-synthesizer-agent"]
      skills = ["research-brief"]
      mcps   = []
      hitl   = false
    }
  })
}

resource "azurerm_resource_group" "main" {
  name     = local.naming.rg
  location = var.location
  tags     = local.tags
}

resource "random_string" "unique" {
  length  = 4
  special = false
  upper   = false
}

# Shared user-assigned managed identity for Functions, Logic Apps, ACA, APIM backends
resource "azurerm_user_assigned_identity" "workload" {
  name                = local.naming.uami
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}
