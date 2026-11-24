targetScope = 'resourceGroup'

param accountName string
param projectName string
param storageName string
param insightsName string
param copilotPrincipalId string
param hostedPrincipalId string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource checkpoints 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobs
  name: 'invoice-jobs'
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: insightsName
}

var foundryRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource agentProjectRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in [copilotPrincipalId, hostedPrincipalId]: {
  scope: project
  name: guid(project.id, principal, foundryRole)
  properties: {
    principalId: principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryRole
  }
}]
resource hostedBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: checkpoints
  name: guid(checkpoints.id, hostedPrincipalId, 'blob-contributor')
  properties: {
    principalId: hostedPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource hostedInsightsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, hostedPrincipalId, 'reader')
  properties: {
    principalId: hostedPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  }
}
resource hostedMetricsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: insights
  name: guid(insights.id, hostedPrincipalId, 'metrics')
  properties: {
    principalId: hostedPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}
