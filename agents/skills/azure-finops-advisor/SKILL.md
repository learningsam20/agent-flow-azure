---
name: azure-finops-advisor
description: Analyze Azure SKU costs, tagging gaps, and optimization opportunities for agent-flow-azure workloads.
allowed-tools: cost-estimator, audit-store-mcp
metadata:
  author: agent-flow-azure
  version: "1.0.0"
  source: adapted from MicrosoftDocs/agent-skills azure-cost-optimization
  roles: ["Agent.Admin"]
---

# Azure FinOps Advisor

## Purpose
Estimate monthly run-rate, spot waste (idle Logic Apps, oversized SKUs), and recommend Free/Consumption tiers.

## Instructions
1. Discover `cost-estimator` Function and audit-store MCP from API Center.
2. Prefer Free / Consumption / Developer / F0 SKUs in recommendations.
3. Check mandatory tags: project, environment, managed_by, owner, cost_center.
4. Return a FinOps brief with estimated monthly cost and actionable savings.

## Guardrails
- Restricted to `Agent.Admin` only (gateway ACL + this skill metadata).
- Do not recommend SKUs that require static access keys when MI is available.
