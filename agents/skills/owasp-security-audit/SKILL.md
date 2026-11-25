---
name: owasp-security-audit
description: Scan configs and code for secrets, IAM flaws, and CVEs using OWASP-oriented checks and CVE lookup tools.
allowed-tools: cve-lookup, microsoft-learn-mcp
metadata:
  author: agent-flow-azure
  version: "1.0.0"
  source: adapted from anthropics/skills security-review patterns
  roles: ["Agent.Admin", "Agent.SecurityAuditor"]
---

# OWASP Security Audit

## Purpose
Perform a structured security review covering secrets exposure, IAM misconfigurations, and known CVEs.

## Instructions
1. Inventory inputs (IaC snippets, dependency manifests, config).
2. Flag hardcoded secrets, overly permissive RBAC, and public endpoints.
3. Resolve `cve-lookup` and Microsoft Learn MCP via registry discovery.
4. Call `cve-lookup` for package versions; ground remediation advice with Learn MCP.
5. Produce a severity-ranked report (Critical/High/Medium/Low) with remediation steps.

## Guardrails
- Never request or store real credentials.
- Prefer Managed Identity / workload identity recommendations over keys.
