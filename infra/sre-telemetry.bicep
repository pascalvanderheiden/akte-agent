// Optional postprovision deployment, deliberately outside the core template.
// Contract: microsoft/sre-agent, commit 53e7b66, agent-extensions.bicep.
targetScope = 'resourceGroup'

param agentName string
param agentPrincipalId string

@allowed([
  'app-insights'
  'log-analytics'
])
param source string

param appInsightsResourceId string
param appInsightsAppId string
param logAnalyticsResourceId string

var monitoringReader = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
var logAnalyticsReader = '73c42c96-874c-492b-b04d-ab87d138a893'

#disable-next-line BCP081
resource agent 'Microsoft.App/agents@2025-05-01-preview' existing = {
  name: agentName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: last(split(appInsightsResourceId, '/'))
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsResourceId, '/'))
}

resource appInsightsQuery 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (source == 'app-insights') {
  name: guid(appInsights.id, agentPrincipalId, monitoringReader)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReader)
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource workspaceQuery 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (source == 'log-analytics') {
  name: guid(workspace.id, agentPrincipalId, logAnalyticsReader)
  scope: workspace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReader)
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
  }
}

#disable-next-line BCP081
resource appInsightsConnector 'Microsoft.App/agents/connectors@2025-05-01-preview' = if (source == 'app-insights') {
  parent: agent
  name: 'app-insights'
  properties: {
    dataConnectorType: 'AppInsights'
    dataSource: appInsightsResourceId
    extendedProperties: {
      armResourceId: appInsightsResourceId
      resource: { name: appInsights.name }
      appId: appInsightsAppId
    }
    identity: 'system'
  }
  dependsOn: [appInsightsQuery]
}

#disable-next-line BCP081
resource logAnalyticsConnector 'Microsoft.App/agents/connectors@2025-05-01-preview' = if (source == 'log-analytics') {
  parent: agent
  name: 'log-analytics'
  properties: {
    dataConnectorType: 'LogAnalytics'
    dataSource: logAnalyticsResourceId
    extendedProperties: {
      armResourceId: logAnalyticsResourceId
      resource: { name: workspace.name }
    }
    identity: 'system'
  }
  dependsOn: [workspaceQuery]
}
