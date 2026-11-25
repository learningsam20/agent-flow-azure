variable "subscription_id" {
  type        = string
  description = "Azure subscription ID"
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant ID"
}

variable "environment" {
  type        = string
  description = "Environment name (dev|test|prod)"
  default     = "dev"
}

variable "location" {
  type        = string
  description = "Azure region for most resources"
  default     = "eastus2"
}

variable "location_short" {
  type        = string
  description = "Short region code for naming"
  default     = "eus2"
}

variable "compute_location" {
  type        = string
  description = "Region for Functions Y1 (may differ when regional App Service quota is 0)"
  default     = "westus2"
}

variable "project_short" {
  type        = string
  description = "Short project prefix"
  default     = "afa"
}

variable "name_suffix" {
  type        = string
  description = "Numeric suffix for uniqueness"
  default     = "01"
}

variable "foundry_project_name" {
  type        = string
  description = "Foundry project name under the AIServices account"
  default     = "afa-agents"
}

variable "foundry_model_name" {
  type        = string
  description = "Primary chat model (also first entry in model-router subset)"
  default     = "gpt-4.1-mini"
}

variable "foundry_model_version" {
  type    = string
  default = "2025-04-14"
}

variable "foundry_model_capacity" {
  type    = number
  default = 10
}

# Prefer models with available regional quota (gpt-4.1 / gpt-4o often 0 on new subs).
variable "foundry_fallback_mid_name" {
  type    = string
  default = "gpt-4.1-nano"
}

variable "foundry_fallback_mid_version" {
  type    = string
  default = "2025-04-14"
}

variable "foundry_fallback_strong_name" {
  type    = string
  default = "gpt-4.1-nano"
}

variable "foundry_fallback_strong_version" {
  type    = string
  default = "2025-04-14"
}

variable "enable_foundry_fallback_strong" {
  type        = bool
  description = "Deploy a third pool model for model-router (needs quota). Default false when mid==strong."
  default     = false
}

variable "foundry_router_version" {
  type        = string
  description = "model-router deployment version (decider + failover)"
  default     = "2025-11-18"
}

variable "foundry_router_mode" {
  type        = string
  description = "model-router mode: balanced | cost | quality"
  default     = "balanced"
}

variable "foundry_agent_deployment" {
  type        = string
  description = "Deployment name agents bind to (model-router when enable_model_router, else primary)"
  default     = "gpt-4.1-mini"
}

variable "enable_model_router" {
  type        = bool
  description = "Deploy model-router + fallback models (needs quota for ≥2 chat models)"
  default     = false
}

variable "foundry_embedding_deployment" {
  type    = string
  default = "text-embedding-3-small"
}

variable "foundry_embedding_version" {
  type    = string
  default = "1"
}

variable "enable_content_safety" {
  type    = bool
  default = true
}

variable "enable_prompt_shields" {
  type    = bool
  default = true
}

variable "enable_servicenow_mcp" {
  type        = bool
  description = "Provision ACR + Container Apps host for ServiceNow OSS MCP"
  default     = false
}

variable "servicenow_instance_url" {
  type      = string
  default   = ""
  sensitive = true
}

variable "servicenow_username" {
  type      = string
  default   = ""
  sensitive = true
}

variable "servicenow_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "web_redirect_uri" {
  type    = string
  default = "http://localhost:5748/"
}

variable "keycloak_url" {
  type        = string
  description = "Public Keycloak base URL used by APIM validate-jwt openid-config"
  default     = "http://localhost:5750"
}

variable "keycloak_realm" {
  type    = string
  default = "agent-flow"
}

variable "keycloak_api_audience" {
  type    = string
  default = "afa-api"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags merged with mandatory tags"
  default     = {}
}
