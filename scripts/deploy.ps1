param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,
    [string]$EnvironmentName = 'invoice-blog',
    [string]$Location = 'swedencentral',
    [switch]$UpdateSkill
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS = 'true'
$env:AZD_ALPHA_ENABLE_AGENTS_LONG_RUNNING = 'true'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE"
    }
}

foreach ($tool in @('az', 'azd', 'docker')) {
    Get-Command $tool -ErrorAction Stop | Out-Null
}
Invoke-Checked docker @('info', '--format', '{{.ServerVersion}}')
Invoke-Checked az @('account', 'show', '--subscription', $SubscriptionId, '--output', 'none')
Invoke-Checked azd @('auth', 'login', '--check-status')

$environments = Invoke-Checked azd @('env', 'list', '--output', 'json') | ConvertFrom-Json
if (@($environments | Where-Object { $_.Name -eq $EnvironmentName }).Count -eq 0) {
    Invoke-Checked azd @(
        'env', 'new', $EnvironmentName, '--subscription', $SubscriptionId,
        '--location', $Location, '--no-prompt'
    )
} else {
    Invoke-Checked azd @('env', 'select', $EnvironmentName)
}
Invoke-Checked azd @('env', 'set', 'AZURE_SUBSCRIPTION_ID', $SubscriptionId)
Invoke-Checked azd @('env', 'set', 'AZURE_LOCATION', $Location)
Invoke-Checked azd @('provision', '--no-prompt')
$values = Invoke-Checked azd @('env', 'get-values', '--output', 'json') | ConvertFrom-Json

Invoke-Checked azd @('deploy', 'invoice-api', '--no-prompt')
$health = Invoke-RestMethod "$($values.INVOICE_API_ENDPOINT)/health"
if ($health.dataset -ne 'synthetic-invoices-v1') {
    throw 'The invoice API is not running the expected demo image'
}
$evidence = Invoke-RestMethod "$($values.INVOICE_API_ENDPOINT)/invoices/INV-104/check"
if ($evidence.status -ne 'AMOUNT_MISMATCH' -or $evidence.variance_minor_units -ne 25000) {
    throw 'The invoice API did not return the expected $250 discrepancy'
}

$skillCommand = 'create'
if ($UpdateSkill) { $skillCommand = 'update' }
$skill = Invoke-Checked azd @(
    'ai', 'skill', $skillCommand, 'invoice-exception-review',
    '--file', (Join-Path (Get-Location) 'skills\invoice-exception-review'),
    '--project-endpoint', $values.AZURE_AI_PROJECT_ENDPOINT, '--output', 'json'
) | ConvertFrom-Json
if (-not $skill.version -or "$($skill.version)" -notmatch '^[1-9][0-9]*$') {
    throw 'Skill publication did not return an immutable positive version'
}
Invoke-Checked azd @('env', 'set', 'INVOICE_SKILL_VERSION', "$($skill.version)")
foreach ($agent in @('invoice-prompt', 'invoice-copilot', 'invoice-hosted')) {
    Invoke-Checked azd @('deploy', $agent, '--no-prompt')
}

function Get-AgentPrincipal {
    param([string]$Name)
    $agent = Invoke-Checked az @(
        'rest', '--method', 'get',
        '--url', "$($values.AZURE_AI_PROJECT_ENDPOINT)/agents/$($Name)?api-version=v1",
        '--resource', 'https://ai.azure.com', '--output', 'json'
    ) | ConvertFrom-Json
    $principal = $agent.instance_identity.principal_id
    if (-not $principal) { throw "Agent $Name has no instance identity" }
    return $principal
}

$copilotPrincipal = Get-AgentPrincipal 'invoice-copilot'
$hostedPrincipal = Get-AgentPrincipal 'invoice-hosted'
$insightsName = ($values.AZURE_APPLICATION_INSIGHTS_RESOURCE_ID -split '/')[-1]
Invoke-Checked az @(
    'deployment', 'group', 'create', '--subscription', $SubscriptionId,
    '--resource-group', $values.AZURE_RESOURCE_GROUP,
    '--name', 'invoice-agent-access', '--template-file', 'infra\agent-access.bicep',
    '--parameters',
    "accountName=$($values.AZURE_AI_ACCOUNT_NAME)",
    "projectName=$($values.AZURE_AI_PROJECT_NAME)",
    "storageName=$($values.CHECKPOINT_STORAGE_ACCOUNT_NAME)",
    "insightsName=$insightsName",
    "copilotPrincipalId=$copilotPrincipal",
    "hostedPrincipalId=$hostedPrincipal",
    '--output', 'none'
)

Write-Host 'Provisioning, API smoke check, skill publication, agent deployment, and RBAC completed.'
Write-Host 'Allow up to five minutes for RBAC propagation before invoking the agents.'
Write-Host 'Load the azd environment into your shell using scripts\load-env.ps1.'
Write-Host "Foundry: https://ai.azure.com/nextgen"
Write-Host "Application Insights resource: $($values.AZURE_APPLICATION_INSIGHTS_RESOURCE_ID)"
