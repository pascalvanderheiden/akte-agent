@description('Name of the Microsoft Foundry resource')
param name string

@description('Location')
param location string

@description('Tags')
param tags object = {}

@description('Name of the Foundry project')
param projectName string = '${name}-proj'

@description('Name of the orchestrator model deployment')
param orchestratorDeploymentName string = 'gpt-6-luna'

@description('Model name for the orchestrator deployment')
param orchestratorModelName string = 'gpt-6-luna'

@description('Model version for the orchestrator deployment')
param orchestratorModelVersion string = '2026-09-22'

@description('Orchestrator deployment SKU capacity (thousands of tokens per minute)')
param orchestratorModelCapacity int = 350

@description('Name of the deep-reasoning model deployment')
param deepReasoningDeploymentName string = 'gpt-6-sol'

@description('Model name for the deep-reasoning deployment')
param deepReasoningModelName string = 'gpt-6-sol'

@description('Model version for the deep-reasoning deployment')
param deepReasoningModelVersion string = '2026-09-22'

@description('Deep-reasoning deployment SKU capacity (thousands of tokens per minute)')
param deepReasoningModelCapacity int = 350

@description('Name of the fast model deployment')
param fastDeploymentName string = 'gpt-6-astra'

@description('Model name for the fast deployment')
param fastModelName string = 'gpt-6-astra'

@description('Model version for the fast deployment')
param fastModelVersion string = '2026-09-03'

@description('Fast deployment SKU capacity (thousands of tokens per minute)')
param fastModelCapacity int = 350

@description('Application Insights resource ID to connect to the project (powers the Foundry Traces tab). Empty = no connection.')
param appInsightsId string = ''

@description('Application Insights connection string used as the connection credential.')
@secure()
param appInsightsConnectionString string = ''

resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: aiFoundry
  name: projectName
  location: location
  // Concurrent project/model writes lock the same account and cause RequestConflict.
  dependsOn: [
    fastDeployment
  ]
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource orchestratorDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiFoundry
  name: orchestratorDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: orchestratorModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: orchestratorModelName
      version: orchestratorModelVersion
    }
  }
}

// Cognitive Services serialises deployment writes at account scope.
resource deepReasoningDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiFoundry
  name: deepReasoningDeploymentName
  dependsOn: [
    orchestratorDeployment
  ]
  sku: {
    name: 'GlobalStandard'
    capacity: deepReasoningModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: deepReasoningModelName
      version: deepReasoningModelVersion
    }
  }
}

resource fastDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiFoundry
  name: fastDeploymentName
  dependsOn: [
    deepReasoningDeployment
  ]
  sku: {
    name: 'GlobalStandard'
    capacity: fastModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: fastModelName
      version: fastModelVersion
    }
  }
}

// Connect Application Insights to the project so the Foundry portal Traces tab
// has a data source AND the platform injects the trace connection string into
// hosted agents (their OpenTelemetry gen_ai spans land here).
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (!empty(appInsightsId)) {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsightsId
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: appInsightsConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
    }
  }
}

output id string = aiFoundry.id
output name string = aiFoundry.name
output endpoint string = aiFoundry.properties.endpoint
output orchestratorModelDeployment string = orchestratorDeployment.name
output fastModelDeployment string = fastDeployment.name
output deepReasoningModelDeployment string = deepReasoningDeployment.name
// Backwards-compatible alias. The orchestrator remains the default chat model.
output modelDeploymentName string = orchestratorDeployment.name
output projectName string = project.name
output projectEndpoint string = project.properties.endpoints['AI Foundry API']
output projectId string = project.id
output principalId string = aiFoundry.identity.principalId
// The project MI is what pulls the hosted-agent container image from ACR
// (per foundry-hosted-agents skill). Account MI ≠ project MI — both are
// system-assigned, both need different roles.
output projectPrincipalId string = project.identity.principalId
