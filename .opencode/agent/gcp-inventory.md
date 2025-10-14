---
description: Gathers GCP organization, folder, and project inventories for the Cloud Orchestrator
mode: subagent
model: openrouter/openai/gpt-5-codex
temperature: 0.1
prompt: "{file:../../prompts/gcp-inventory.md}"
tools:
  bash: true
  read: true
  list: true
  grep: true
  todoread: true
  todowrite: true
  write: false
  edit: false
  patch: false
permission:
  bash:
    "gcloud *": allow
    "bq *": allow
    "bash *": allow
    "*": allow
mcp:
  gcloud: true
  edit: deny
  webfetch: deny
metadata:
  provider: gcp
  capabilities:
    - org_traversal
    - service_counts
    - detailed_inventory
---