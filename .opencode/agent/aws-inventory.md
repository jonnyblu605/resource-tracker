---
description: Retrieves AWS organizational and service inventories for the Cloud Orchestrator
mode: subagent
model: openrouter/openai/gpt-5-codex
temperature: 0.1
prompt: "{file:../../prompts/aws-inventory.md}"
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
    "aws *": allow
    "bash *": allow
    "*": allow
  edit: deny
  webfetch: deny
metadata:
  provider: aws
  capabilities:
    - org_account_listing
    - service_counts
    - detailed_inventory
---