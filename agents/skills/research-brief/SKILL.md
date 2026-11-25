---
name: research-brief
description: Compile parallel agent outputs into an executive research brief with charts and artifact links.
allowed-tools: code-interpreter, audit-store-mcp, blob-artifact-writer
metadata:
  author: agent-flow-azure
  version: "1.0.0"
  source: adapted from Foundry research-brief patterns
  roles: ["Agent.Admin", "Agent.Developer", "Agent.SecurityAuditor", "Agent.Viewer"]
---

# Research Brief / Executive Synthesizer

## Purpose
Merge triage, repo, security, and FinOps branch outputs into a single executive Markdown brief.

## Instructions
1. Collect node outputs from the audit-store MCP (`get_node_state`, `list_runs`) via registry discovery.
2. Use Foundry Code Interpreter for simple charts when numeric data is present.
3. Persist the final brief via blob artifact writer (Managed Identity to Storage).
4. Filter sensitive details based on `X-User-Roles` (Viewers get sanitized summaries).

## Output format
- Executive summary (5 bullets max)
- Findings by stream
- Risks & recommendations
- Artifact URI (if saved)
