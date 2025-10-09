# Cloud Orchestrator Agent

This document explains how to use the Cloud Orchestrator primary agent together with the AWS, Azure, and GCP inventory subagents.

## Prerequisites

1. Install and authenticate all cloud CLIs:
   - AWS CLI v2 with `aws configure` or `aws sso login`
   - Azure CLI with `az login`
   - Google Cloud SDK with `gcloud auth login`
2. Ensure `.opencode/agent/*.md` files and `prompts/*.md` prompts are present in the project.
3. Point opencode to the project root containing `opencode.json`.

## Agent Overview

| Agent | Mode | Description |
| --- | --- | --- |
| Cloud Orchestrator (`cloud-orchestrator`) | Primary | Interprets user prompts and delegates to subagents |
| AWS Inventory (`aws-inventory`) | Subagent | Runs AWS CLI read-only inventory commands |
| Azure Inventory (`azure-inventory`) | Subagent | Runs Azure CLI read-only inventory commands |
| GCP Inventory (`gcp-inventory`) | Subagent | Runs gcloud/bq read-only inventory commands |

## Typical Workflows

### Count resources in a single provider
```
cloud-orchestrator> I need the number of GCP projects in my organization
```
1. Orchestrator validates intent, asks for scope clarifications (e.g., specific folders or stop conditions), then calls `@gcp-inventory`.
2. Subagent runs `gcloud projects list` scoped to the organization with the provided stop criteria, returns JSON payload or status update.
3. Orchestrator summarizes counts, reports back, and suggests refinements if the subagent surfaced limitations.

### Multi-cloud inventory summary
```
cloud-orchestrator> Summarize my AWS and Azure compute footprints
```
1. Orchestrator confirms desired services, regions, and stop conditions for both clouds before delegating.
2. Delegated subagents return scoped counts and details or status reports when constraints prevent completion.
3. Orchestrator consolidates results into a single narrative and includes a **Next steps** section when actions are required.

### Request machine-readable output
```
cloud-orchestrator> Provide AWS VPC inventory in us-east-1 as json
```
1. Orchestrator detects JSON format request, confirms any necessary filters, and propagates to subagent.
2. AWS subagent emits JSON envelope only or a status object if unable to complete.
3. Orchestrator forwards JSON block (or status explanation) and may suggest follow-up commands when partial results occur.

## Error Handling

- **Clarification required**: Orchestrator pauses work, relays the subagent status object, and asks pointed questions so the user can refine scope.
- **Missing CLI**: Subagents detect absent binaries and respond with installation commands; orchestrator surfaces these messages plus suggested remediation steps.
- **Authentication failure**: Subagents include the relevant login command (e.g., `aws sso login`) so the user can reauthenticate.
- **Permissions**: When access is denied, responses indicate the account/project and suggest the necessary role.
- **Large datasets**: Responses mention truncation, recommend narrowing by region or service, and wait for user confirmation before continuing.

## Extending Capabilities

- Add new services by editing the relevant subagent prompt in `prompts/`.
- Update command allowlists inside `.opencode/agent/*.md` to whitelist additional CLIs.
- Modify models or temperature settings in `opencode.json` for different cost/performance profiles.

## Testing

Automated tests can live under `tests/cloud-orchestrator/`:
- Record CLI outputs and run orchestrator prompts against fixtures.
- Validate normalized JSON structure and human-readable summaries.
- Simulate failures (missing auth, disabled API) to confirm graceful messaging.