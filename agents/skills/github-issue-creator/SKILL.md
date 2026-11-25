---
name: github-issue-creator
description: Audit repositories and draft GitHub issues or PR review comments via the governed GitHub MCP.
allowed-tools: github-mcp, repo-metrics
metadata:
  author: agent-flow-azure
  version: "1.0.0"
  source: adapted from microsoft/skills github-issue-creator
  roles: ["Agent.Admin", "Agent.Developer"]
---

# GitHub Issue Creator

## Purpose
Help developers audit repos, draft issues, and summarize PR diffs using the GitHub Official MCP Server routed through the Foundry AI Gateway.

## Instructions
1. Discover the GitHub MCP endpoint from Azure API Center / Foundry Toolbox (never hardcode `api.githubcopilot.com`).
2. Invoke MCP tools only through the gateway so JWT/RBAC and rate limits apply.
3. Prefer read-only analysis unless the user explicitly asks to create an issue and holds `Agent.Developer` or `Agent.Admin`.
4. Call `repo-metrics` Function for quantitative signals when useful.
5. Return Markdown with findings, suggested issue title/body, and citations.

## Guardrails
- Do not embed PATs or secrets in outputs.
- Block write operations for Viewer/Auditor roles (gateway enforces; reinforce in reasoning).
