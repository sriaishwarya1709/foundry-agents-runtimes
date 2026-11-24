targetScope = 'subscription'

@minLength(1)
@maxLength(40)
param environmentName string
param location string = 'swedencentral'
@description('Developer object ID. Deployment needs authority to create scoped role assignments.')
param principalId string
@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: {
    'azd-env-name': environmentName
  }
}

module resources 'resources.bicep' = {
  name: 'invoice-runtimes'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    principalId: principalId
    principalType: principalType
    deployments: loadYamlContent('../azure.yaml', 'services.ai-project.deployments')
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_AI_ACCOUNT_NAME string = resources.outputs.accountName
output AZURE_AI_PROJECT_NAME string = resources.outputs.projectName
output AZURE_AI_PROJECT_ID string = resources.outputs.projectId
output AZURE_AI_PROJECT_ENDPOINT string = resources.outputs.projectEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = resources.outputs.projectEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = resources.outputs.modelDeploymentName
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.registryName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryEndpoint
output AZURE_AI_PROJECT_ACR_CONNECTION_NAME string = 'invoice-registry'
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = resources.outputs.containerEnvironmentId
output INVOICE_API_ENDPOINT string = resources.outputs.apiEndpoint
output CHECKPOINT_STORAGE_ACCOUNT_NAME string = resources.outputs.storageName
output CHECKPOINT_STORAGE_ENDPOINT string = resources.outputs.storageEndpoint
output AZURE_APPLICATION_INSIGHTS_RESOURCE_ID string = resources.outputs.insightsId
output AZURE_APPLICATION_INSIGHTS_APP_ID string = resources.outputs.insightsAppId
output AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID string = resources.outputs.workspaceId
