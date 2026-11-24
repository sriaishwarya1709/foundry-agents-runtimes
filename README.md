# Foundry invoice runtime comparison

One invoice-reconciliation use case, **three agents**, and complete Bicep infrastructure.
Default region: **Sweden Central (`swedencentral`)**.

| Agent | Ownership | Demo |
| --- | --- | --- |
| `invoice-prompt` | Foundry runs the default prompt-agent loop | Check `INV-104`: $1,250 invoice vs $1,000 PO; $250 over |
| `invoice-copilot` | Prompt agent with `microsoft_copilot_preview` harness | Review invoice PDFs with a versioned skill; return CSV and Markdown files |
| `invoice-hosted` | Your Python code owns execution; Foundry hosts it | Stored background batch; durable per-invoice checkpoints; atomic report creation |

The Copilot harness is a runtime choice for a **prompt agent**, not a third agent type.
All financial comparisons use shared business code and integer minor units, never model arithmetic.
There are no approval/payment endpoints or permissions.

## Infrastructure

`infra/main.bicep` creates a resource group and deploys `infra/resources.bicep`:

- Foundry AIServices account with Entra authentication and a system-assigned project identity.
- Foundry project and a `gpt-5-mini` model deployment.
- Workspace-backed Application Insights, Log Analytics, and the project's AppInsights connection.
- Private Blob container for job manifests, checkpoints, and reports; shared-key auth disabled.
- Container Registry and its Foundry project connection.
- Container Apps environment and a read-only synthetic reconciliation API.
- Scoped developer, API identity, and registry RBAC.

`infra/agent-access.bicep` grants permissions to the **actual agent instance identities**
after agents exist: Copilot can upload Foundry Files; hosted can use the project, write
job blobs, and export telemetry. The project identity is not a substitute for the Copilot identity.

Model declarations in `azure.yaml` are read by Bicep at compile time. Change the model
name/version/SKU/capacity there, then reprovision; don't create a second model deployment manually.
The default model capacity is 500 units to accommodate the multi-file Copilot review.
This is pay-per-token GlobalStandard capacity, not a provisioned-throughput commitment.
Reduce it for lighter workloads, but multi-tool reviews can exceed a small allocation.

**Demo security and cost:** this is a public-network sample, not production infrastructure.
The anonymous API exposes only fixed synthetic records and no write operations.
Foundry, models, storage, Registry, Container Apps, and telemetry can incur charges.
Do not replace the fixtures with real invoices without adding authenticated API access,
appropriate networking, governance, and data-retention policies.
Shell is enabled for PDF processing; it is broader than file read/write permissions.
No web or subagent built-ins are enabled, but shell can still access allowed networks.
App Insights local ingestion auth remains enabled for Foundry's AppInsights connection;
the custom Python exporters use Entra credentials. Connection strings are resolved at
runtime or inside ARM and never printed or committed to environment configuration.
Prompt/file contents are not deliberately included in custom telemetry.
The Foundry service's own trace-content policies still apply.

## Prerequisites

- Python **3.13**, Azure CLI, and Azure Developer CLI **1.37+**.
  On Windows ARM64, use x64 Python if a native dependency has no ARM64 wheel.
  The API image is built remotely using ACR Tasks; local Docker is not required.
  Bicep grants the developer scoped ACR push and Tasks Contributor roles for that build.
  The Copilot review procedure lives under `procedures/` to avoid CLI auto-discovery
  attaching it to the ordinary prompt agent. It is published separately and version-pinned.
  Skill versions must be literal strings in `azure.yaml`; the current CLI does not
  expand environment variables there. After publishing an updated skill, change its
  pinned version before redeploying the Copilot agent.
- Install extensions: `azd extension install microsoft.foundry` and
  `azd extension install azure.ai.evaluations`.
- Sign in yourself: `az login` and `azd auth login`.
- Subscription access to create resources and scoped role assignments (for example,
  Contributor plus Role Based Access Control Administrator).
- Check model quota and current [region/feature availability](https://learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions).
  Sweden Central is documented for the Copilot harness; model capacity and hosted
  availability still depend on the subscription and service rollout.

Both Copilot harness and resilient hosted execution use preview capabilities.
Never silently change region or substitute a different agent runtime if deployment is rejected.

## Install and validate locally

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
az bicep build --file infra\main.bicep
az bicep build --file infra\agent-access.bicep
```

`requirements.txt` locks the runtime dependencies for Container Apps and Foundry's
remote Python build. After changing dependencies, regenerate it with your package manager.

Run the read-only API locally:

```powershell
python -m uvicorn invoice_demo.api:app --port 8080
```

In another terminal:

```powershell
Invoke-RestMethod http://localhost:8080/invoices/INV-104/check
Invoke-RestMethod http://localhost:8080/invoices/INV-107/check
```

Expected cases:

| ID | Result |
| --- | --- |
| `INV-104` | `AMOUNT_MISMATCH`, signed variance **25000** USD minor units |
| `INV-105` | `MISSING_PO`, no variance |
| `INV-106` | `CURRENCY_MISMATCH` (USD/EUR), no subtraction |
| `INV-107` | Synthetic persistent PO timeout: `VERIFICATION_UNAVAILABLE` |
| `INV-108` | `MATCH`, variance 0; no approval performed |
| Unknown valid ID | `MISSING_INVOICE` |

## Deploy all three agents

This step creates billable Azure resources. Review the templates first.

```powershell
.\scripts\deploy.ps1 -SubscriptionId "<your-subscription-guid>"
.\scripts\load-env.ps1
```

The script provisions Bicep, deploys the API, verifies the exact $250 tool result,
publishes the skill and pins its returned immutable version, deploys the three agents,
then applies the second Bicep template for instance-identity RBAC. Allow up to five
minutes for role propagation before invoking the agents.

For subsequent skill changes, use `-UpdateSkill` to publish a new immutable version:

```powershell
.\scripts\deploy.ps1 -SubscriptionId "<your-subscription-guid>" -UpdateSkill
```
If the returned version differs from the literal pin, the script stops with instructions.
Update `invoice-copilot.skills[0].version` in `azure.yaml`, then run
`azd deploy invoice-copilot --no-prompt` to attach that version.

Some enterprise policies force checkpoint storage public network access off even when
the template requests it enabled. This public-network sample cannot reach that storage
from the hosted agent without an approved private-network connection. Do not exempt or
disable governance policies to make the demo work. Agent activation alone does not prove
the hosted batch can read and write checkpoints.

The deployed API must be reachable by Foundry. The initial infrastructure image is
only a Container Apps bootstrap image; it is **not** the reconciliation API until
`azd deploy invoice-api` succeeds. The deployment script verifies the dataset fingerprint.

## Invoke and capture blog examples

### 1. Default prompt agent

In the Foundry playground, select `invoice-prompt` and paste:

```text
Check invoice INV-104 against its linked purchase order. Use check_invoice as the
source of truth. Explain the status, invoice and PO totals, currency, and signed
variance in dollars. State whether human review is required. Do not approve or pay it.
```

Expected result: `AMOUNT_MISMATCH`, invoice $1,250 vs PO $1,000, **$250 over**.
Try these additional prompts to capture different exception paths:

| Sample prompt | Expected evidence |
| --- | --- |
| Check INV-105 and explain whether its purchase order was found. | `MISSING_PO` |
| Check INV-106. Explain the currency discrepancy without subtracting unlike currencies. | `CURRENCY_MISMATCH` |
| Check INV-107. Distinguish unavailable verification from a missing purchase order. | `VERIFICATION_UNAVAILABLE` |
| Check INV-108. Explain whether the totals match without approving payment. | `MATCH` |

The CLI equivalent for the primary example is:

```powershell
python -m invoice_demo.demo invoke invoice-prompt --invoices INV-104
```

Open the agent in [New Foundry](https://ai.azure.com/nextgen), inspect its response trace,
and capture the `check_invoice` tool call, structured result, and $250 explanation.

### 2. Prompt agent with Copilot harness

The six synthetic input PDFs are included in [samples/invoices](./samples/invoices),
so you can download them from GitHub and attach them in the Foundry playground:

- [INV-104.pdf](./samples/invoices/INV-104.pdf): amount mismatch, $250 over the PO.
- [INV-105.pdf](./samples/invoices/INV-105.pdf): missing purchase order.
- [INV-106.pdf](./samples/invoices/INV-106.pdf): USD/EUR currency mismatch.
- [INV-107.pdf](./samples/invoices/INV-107.pdf): intentional verification timeout.
- [INV-108.pdf](./samples/invoices/INV-108.pdf): matching totals.
- [unreadable-id.pdf](./samples/invoices/unreadable-id.pdf): human review required.

To invoke the agent with these checked-in PDFs:

```powershell
python -m invoice_demo.demo invoke invoice-copilot --pdf-dir samples\invoices
```

To regenerate the same fixture content locally, run `python -m invoice_demo.demo pdfs`.
That command writes to `demo-output/pdfs`; use `--pdf-dir demo-output\pdfs` to review
those regenerated copies. Generated responses and reports remain excluded from Git.
The client attaches PDF content directly, creates an explicit conversation, and uses the
**agent-scoped** Responses client. A project-level Responses call or `previous_response_id`
is not supported for this harness.

In the Foundry playground, select `invoice-copilot`, start a new conversation, attach
all six PDFs from [samples/invoices](./samples/invoices), and paste:

```text
Use the invoice-exception-review skill to review every attached invoice PDF.
Account for all six source filenames, including the file with an unreadable ID.
Verify each readable invoice ID with check_invoice; do not calculate financial
comparisons from document text. Keep missing POs, currency mismatches, unavailable
verification, and unreadable IDs separate. Create invoice-exceptions.csv and
invoice-exceptions.md for human review, and return both as downloadable Foundry
Files. Include matching invoices in the report too. Do not approve or pay invoices.
```

Expected result: six report rows covering `INV-104.pdf` through `INV-108.pdf` and
`unreadable-id.pdf`, plus downloadable CSV and Markdown file references.

Capture the harness selection, attached `invoice-exception-review` skill, file operations,
tool calls, and downloaded exception reports. Confirm all **six filenames** appear.
The timeout must be separate from missing PO evidence. Capture the returned Foundry
file references; a sandbox path alone does not prove a durable report was uploaded.

If file upload fails with 403, check the Copilot **instance identity** role assignment,
wait for propagation, and retry in a new conversation.

### 3. Hosted batch and recovery

The hosted handler accepts a **JSON job**, not a natural-language prompt. Select
`invoice-hosted` and send the following JSON as the message text, or use the CLI below:

```json
{
  "job_id": "blog-nightly-01",
  "invoice_ids": ["INV-104", "INV-105", "INV-106", "INV-108"]
}
```

Expected result: durable per-invoice checkpoints and one committed reviewer report.
Resubmit the exact same JSON to demonstrate report reuse. Use a new job ID for a
different ordered invoice list. The hosted demo requires working checkpoint-storage
connectivity; organizational network policy can block it even when the agent is active.

```powershell
python -m invoice_demo.demo invoke invoice-hosted --job-id blog-nightly-01
python -m invoice_demo.demo response "<returned-response-id>"
python -m invoice_demo.demo report blog-nightly-01
```

The invocation uses `background=true` and `store=true`; polling reports the real status.
Invoke again with the same job ID and invoice list. The committed report is reused.
Changing a job's invoice list is rejected. Default successful batches omit `INV-107`:
that fixture intentionally always times out. Add it to demonstrate bounded retries;
the failing invoice and final report remain uncommitted, while prior checkpoints survive.

For the intentional timeout/retry scenario, use a separate job:

```json
{
  "job_id": "blog-timeout-01",
  "invoice_ids": ["INV-104", "INV-107"]
}
```

Expected result: `INV-104` is checkpointed, but `INV-107` exhausts bounded retries.
The response fails explicitly and no final report is committed. This is not the
successful-batch example.

For a quick **application-checkpoint** restart demonstration without Azure/model calls:

```powershell
python -m invoice_demo.demo local-batch --job-id recovery-demo --stop-after 1
# Expected nonzero exit after persisting INV-104.
python -m invoice_demo.demo local-batch --job-id recovery-demo
python -m invoice_demo.demo local-batch --job-id recovery-demo
```

The second process skips the saved check; the third reuses the report. This demonstrates
domain recovery, not Foundry lease recovery. Use a fresh job ID if this demo already completed.

For **platform handler reentry**, run the hosted agent using `azd ai agent run invoice-hosted
--no-client` with the provisioned environment, send a stored background request, and interrupt
that specific process while work is in progress. Restart against the same SDK state store.
Use Linux/WSL2 or a container for local lease-lock crash exercises, as recommended by
the [official resilient-agent walkthrough](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-resilient-agent).
Never kill unrelated processes. Capture `context.is_recovery`, saved domain checkpoints,
and the final report. The local SQLite demo does not claim to verify platform recovery.

No nightly schedule is deployed: a scheduler invokes the hosted agent with a stable
job ID and immutable invoice list. Foundry hosts the handler; application code owns
retries, checkpoints, the manifest invariant, and duplicate-safe report writes.
Concurrent attempts can repeat a read or model draft, but Blob's conditional create
commits only one report. This is not an exactly-once guarantee for arbitrary external actions.

## Application Insights screenshots

Open the `appi-*` resource in the Azure portal. In **Logs**, run the queries in
`docs/screenshots.kql` individually at Application Insights component scope.

1. `check_invoice` spans: amount mismatch, missing PO, currency mismatch, and unavailable verification.
2. Hosted transaction: `invoice.batch`, `batch.invoice`, `batch.model_summary`, and `batch.report`.
3. Recovery: `checkpoint.reused=true` and the stable `job.id`.
4. Duplicate safety: exactly one `report.created=true` span per completed job.
5. Latency: model summary vs deterministic financial tool execution.

Correlate API and hosted spans by `operation_Id`. Open a transaction to inspect child
spans and events. Prompt-agent tool traces in Foundry complement the API spans; do not
assume every runtime exports identical span names or that provisioning proves ingestion.
Telemetry may take several minutes to appear. Missing data requires checking identity
RBAC, exporter logs, component/workspace query access, and actual invocation completion.
Custom exporters resolve App Insights metadata using the scoped Reader role and
ingest using Monitoring Metrics Publisher. The hosted exporter is initialized on the
first invocation, after instance RBAC exists, and reuses AgentServer's trace provider.

## Cleanup

```powershell
azd down
```

Confirm the selected environment before accepting deletion. This deletes the demo resource
group, including reports and telemetry. Save screenshots/reports before cleanup.

## References

- [Prompt agents](https://learn.microsoft.com/azure/foundry/agents/concepts/prompt-agents)
- [Copilot harness tools, skills, files, and identity](https://learn.microsoft.com/azure/foundry/agents/how-to/use-tools-skills-copilot-harness)
- [Hosted resilience responsibilities](https://learn.microsoft.com/azure/foundry/agents/concepts/long-running-agent-resilience)
- [AgentServer Responses SDK samples](https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/agentserver/azure-ai-agentserver-responses/samples)

This repository uses the current AgentServer Responses SDK handler/checkpoint pattern.
Cloud deployment, live model output, telemetry ingestion, and Foundry process-loss recovery
must be verified in your subscription; local tests cannot establish those outcomes.
