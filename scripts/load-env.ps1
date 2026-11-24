$ErrorActionPreference = 'Stop'
$values = azd env get-values --output json
if ($LASTEXITCODE -ne 0) { throw 'Unable to read the selected azd environment' }
$parsed = $values | ConvertFrom-Json
foreach ($name in @(
    'AZURE_AI_PROJECT_ENDPOINT', 'AZURE_AI_MODEL_DEPLOYMENT_NAME', 'INVOICE_API_ENDPOINT',
    'CHECKPOINT_STORAGE_ENDPOINT', 'AZURE_APPLICATION_INSIGHTS_RESOURCE_ID'
)) {
    $value = $parsed.$name
    if (-not $value) { throw "Missing azd output $name; provision infrastructure first" }
    Set-Item -Path "Env:\$name" -Value $value
}
$env:AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS = 'true'
$env:AZD_ALPHA_ENABLE_AGENTS_LONG_RUNNING = 'true'
