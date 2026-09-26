@description('Name of the Application Insights resource')
param name string

@description('Location')
param location string

@description('Tags')
param tags object = {}

@description('Log Analytics workspace ID')
param logAnalyticsWorkspaceId string

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: name
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspaceId
  }
}

resource hostedAgentCosmosPersistenceAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: '${name}-hosted-agent-cosmos-persistence'
  location: location
  tags: tags
  properties: {
    displayName: 'Hosted-agent Cosmos persistence failures'
    description: 'Repeated hosted-agent user or assistant message persistence failures.'
    // Azure Monitor severity 2 is Warning.
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    scopes: [
      appInsights.id
    ]
    criteria: {
      allOf: [
        {
          // Keep these signatures synchronized with the persistence warnings in src/hosted-agent/main.py.
          query: '''
            traces
            | where message in (
                "Failed to persist user message to Cosmos (non-fatal)",
                "Failed to persist assistant message to Cosmos (non-fatal)"
              )
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          // GreaterThan 2 alerts on the third warning in the window.
          threshold: 2
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
  }
}

output id string = appInsights.id
output name string = appInsights.name
output connectionString string = appInsights.properties.ConnectionString
output instrumentationKey string = appInsights.properties.InstrumentationKey
