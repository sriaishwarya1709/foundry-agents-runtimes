param location string
param environmentName string
param principalId string
param principalType string
param deployments array

var token = uniqueString(subscription().id, resourceGroup().id)
var tags = {
  'azd-env-name': environmentName
}
var foundryUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var reader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
var blobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var metricsPublisher = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
var logReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'logs-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    // Foundry's AppInsights connection uses the component's ingestion key.
    DisableLocalAuth: false
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'foundry-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: 'foundry-${token}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

@batchSize(1)
resource models 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [for deployment in deployments: {
  parent: account
  name: deployment.name
  sku: deployment.sku
  properties: {
    model: deployment.model
  }
}]

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: 'invoice-review'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Invoice runtime comparison'
    description: 'Synthetic invoices: default prompt, Copilot harness, and resilient hosted batch.'
  }
  dependsOn: [models]
}

resource insightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'invoice-insights'
  properties: {
    category: 'AppInsights'
    target: insights.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: insights.properties.ConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: insights.id
    }
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource registryConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'invoice-registry'
  properties: {
    category: 'ContainerRegistry'
    target: registry.properties.loginServer
    authType: 'ManagedIdentity'
    isSharedToAll: true
    credentials: {
      clientId: project.identity.principalId
      resourceId: registry.id
    }
    metadata: { ResourceId: registry.id }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${token}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource checkpoints 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'invoice-jobs'
  properties: { publicAccess: 'None' }
}

resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-invoice-api-${token}'
  location: location
  tags: tags
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-invoice-api-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'invoice-api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      registries: [{
        server: registry.properties.loginServer
        identity: apiIdentity.id
      }]
    }
    template: {
      containers: [{
        name: 'invoice-api'
        // azd deploy invoice-api replaces this bootstrap image with Dockerfile.api.
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
          { name: 'AZURE_APPLICATION_INSIGHTS_RESOURCE_ID', value: insights.id }
        ]
      }]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource developerProjectRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: project
  name: guid(project.id, principalId, foundryUser)
  properties: { principalId: principalId, principalType: principalType, roleDefinitionId: foundryUser }
}
resource developerBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: checkpoints
  name: guid(checkpoints.id, principalId, blobContributor)
  properties: { principalId: principalId, principalType: principalType, roleDefinitionId: blobContributor }
}
resource developerLogsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: workspace
  name: guid(workspace.id, principalId, logReader)
  properties: { principalId: principalId, principalType: principalType, roleDefinitionId: logReader }
}
resource developerInsightsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, principalId, logReader)
  properties: { principalId: principalId, principalType: principalType, roleDefinitionId: logReader }
}
resource developerRegistryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, principalId, 'acr-push')
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec')
  }
}
resource developerRegistryBuildRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, principalId, 'acr-tasks-contributor')
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'fb382eab-e894-4461-af04-94435c366c3f')
  }
}
resource projectRegistryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, project.id, acrPull)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPull
  }
}
resource apiRegistryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, apiIdentity.id, acrPull)
  properties: {
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPull
  }
}
resource apiInsightsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, apiIdentity.id, reader)
  properties: {
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: reader
  }
}
resource apiMetricsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, apiIdentity.id, metricsPublisher)
  properties: {
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: metricsPublisher
  }
}

output accountName string = account.name
output projectName string = project.name
output projectId string = project.id
output projectEndpoint string = project.properties.endpoints['AI Foundry API']
output modelDeploymentName string = deployments[0].name
output registryName string = registry.name
output registryEndpoint string = registry.properties.loginServer
output containerEnvironmentId string = containerEnvironment.id
output apiEndpoint string = 'https://${api.properties.configuration.ingress.fqdn}'
output storageName string = storage.name
output storageEndpoint string = storage.properties.primaryEndpoints.blob
output insightsId string = insights.id
output insightsAppId string = insights.properties.AppId
output workspaceId string = workspace.id
