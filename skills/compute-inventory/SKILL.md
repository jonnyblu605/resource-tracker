---
name: compute-inventory
description: Build highly repeatable VM and compute instance inventories across AWS, Azure, and GCP using fixed clarification questions, strict compute-only scope controls, normalized output fields, and deterministic stop conditions. Use when users request VM counts, instance listings, machine inventory, or compute footprint summaries.
---
# Compute Inventory Workflow

Use this skill to keep cloud inventory responses tightly scoped and repeatable.

## 1. Ask Only Required Questions
Collect missing inputs with this fixed order:
1. `scope`: account/subscription/project/org
2. `regions`: explicit list or `all enabled`
3. `granularity`: `count`, `summary`, or `detailed` (default `count`)
4. `filters`: `tags/labels` and `state` filter values

If user already supplied a field, do not ask it again.

## 2. Enforce Compute-Only Scope
Build an instruction payload that never expands past compute instances:

```json
{
  "intent_profile": "compute_inventory_v1",
  "scope": "account|subscription|project|organization",
  "services": ["compute"],
  "regions": ["all-enabled-or-explicit-list"],
  "filters": {
    "tags_or_labels": [],
    "states": ["running", "stopped", "terminated"]
  },
  "granularity": "count|summary|detailed",
  "format": "human|json",
  "stop_after": "only compute instances; no other resource families"
}
```

Apply these guardrails every time:
- Never include network, storage, database, IAM, container, or observability resources unless user explicitly asks.
- Stop immediately once compute objective is satisfied.
- Default output mode to `human`; switch to `json` only when asked.

## 3. Normalize Provider Fields
Use these cross-cloud compute fields in results:
- `provider`
- `id`
- `name`
- `scope_id`: account/subscription/project identifier
- `location`: region or zone
- `state`
- `instanceType`
- `tags_or_labels`

If a provider field is unavailable, set it to `null` and add a warning.

## 4. Keep Large Results Deterministic
For `detailed` inventory:
- Return at most 200 instances per provider.
- Add a warning stating truncation occurred and how to refine (`regions`, `tags/labels`, `states`).

For `count` inventory:
- Use count-only queries and avoid retrieving full instance records.

## 5. Output Contract
When `format=json`, return only normalized JSON payload.
When `format=human`, return:
1. One summary paragraph.
2. One bullet list per provider with counts and warnings.
3. One fenced JSON block with normalized payload.

## 6. Failure Contract
If blocked, return:
- attempted scope,
- exact blocker (auth, permission, missing CLI/API),
- one concrete next action command.

Never fabricate counts.
