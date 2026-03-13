---
description: Collects Azure subscription and service inventories for the Cloud Orchestrator
mode: subagent
model: openrouter/openai/gpt-5.2-codex
temperature: 0.1
prompt: "{file:../../prompts/azure-inventory.md}"
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
    "az *": allow
    "bash *": allow
    "*": allow
  edit: deny
  webfetch: deny
metadata:
  provider: azure
  capabilities:
    - subscription_listing
    - service_counts
    - detailed_inventory
---