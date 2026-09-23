// Opt-in, read-only Azure SRE Agent for this azd environment.
//
// Scope of this module (first vertical slice, core only)
// ------------------------------------------------------
// It creates the `Microsoft.App/agents` resource, its user-assigned identity,
// and the least-privilege role assignments needed for the agent to *discover*
// this environment. It deliberately does NOT create:
//   * another Log Analytics workspace or Application Insights component — the
//     environment already has both and SRE reuses them (see `appInsightsName`
//     and `logAnalyticsWorkspaceId`);
//   * telemetry connectors (`AppInsights` / `LogAnalytics`) or the query RBAC
//     for the agent's system-assigned identity — those arrive with the
//     telemetry ticket. The system-assigned identity is provisioned here so
//     that work has an identity to grant;
//   * anything that can change a resource: no Contributor, no remediation, no
//     incident automation, no webhooks, no recurring tasks.
//
// The contract (`Microsoft.App/agents@2025-05-01-preview`, the identity model,
// and the configuration property names) follows the Microsoft SRE Agent
// templates — https://github.com/microsoft/sre-agent, commit 53e7b66.

@description('Name of the SRE Agent resource')
param name string

@description('Region for the SRE Agent. Must be a region where the SRE service is available — hooks/sre-preflight.sh checks this before provisioning.')
param location string

@description('Tags')
param tags object = {}

@description('Name of the user-assigned managed identity the agent uses for resource discovery')
param identityName string

@description('Name of the EXISTING Application Insights component in this resource group. Reused for the agent\'s own logging; no new component is created.')
param appInsightsName string

@description('Resource ID of the EXISTING Log Analytics workspace backing that component. Exported for later telemetry setup; retained as-is.')
param logAnalyticsWorkspaceId string

@description('Object ID of the operator (user or CI service principal) that administers the agent. Empty skips the administrator assignment.')
param operatorPrincipalId string = ''

@description('Principal type of operatorPrincipalId. A CI deployment is a ServicePrincipal, a local `az login` is a User — assuming either one breaks the other.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param operatorPrincipalType string = 'User'

// ─── Built-in Role Definition IDs ───
var reader = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
var monitoringReader = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
var sreAgentAdministrator = 'e79298df-d852-4c6d-84f9-5d13249d1e55'

// ─── Existing monitoring (reused, never recreated) ───
// `AppId` is the Application Insights *application* ID used by the query API.
// It is not the ARM resource ID, the instrumentation key, or the connection
// string — SRE needs the AppId here and the connection string separately.
resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// ─── Discovery identity ───
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

// ─── Read-only discovery RBAC, scoped to this environment's resource group ───
// Names are deterministic guids, so repeat provisioning converges instead of
// creating a second assignment.
resource readerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, identity.id, reader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', reader)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource monitoringReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, identity.id, monitoringReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReader)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ─── SRE Agent ───
#disable-next-line BCP081
resource sreAgent 'Microsoft.App/agents@2025-05-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    // User-assigned for discovery/actions, system-assigned for the connectors
    // added by the telemetry ticket. Both are part of the supported model.
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    knowledgeGraphConfiguration: {
      identity: identity.id
      // Discovery is limited to this environment's resource group — the agent
      // never sees another environment or the rest of the subscription.
      managedResources: [
        resourceGroup().id
      ]
    }
    actionConfiguration: {
      // Literals, not parameters: this deployment is deliberately not
      // configurable into an agent that can act on the environment. `Low`
      // keeps its access read-only and `Review` keeps a human in the loop.
      accessLevel: 'Low'
      identity: identity.id
      mode: 'Review'
    }
    logConfiguration: {
      // The agent's OWN logging. This is not permission to query application
      // telemetry; that is a connector, delivered separately.
      applicationInsightsConfiguration: {
        appId: appInsights.properties.AppId
        connectionString: appInsights.properties.ConnectionString
      }
    }
    defaultModel: {
      provider: 'MicrosoftFoundry'
      name: 'Automatic'
    }
  }
  dependsOn: [
    readerAssignment
    monitoringReaderAssignment
  ]
}

// ─── Operator administration, scoped to the agent itself ───
resource operatorAdminAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(operatorPrincipalId)) {
  name: guid(sreAgent.id, operatorPrincipalId, sreAgentAdministrator)
  scope: sreAgent
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sreAgentAdministrator)
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
  }
}

// ─── Outputs ───
output agentId string = sreAgent.id
output agentName string = sreAgent.name
output agentLocation string = sreAgent.location
output agentPrincipalId string = sreAgent.identity.principalId
output identityId string = identity.id
output identityPrincipalId string = identity.properties.principalId
output identityClientId string = identity.properties.clientId
// Distinct values, both needed by later setup: the query-API application ID
// and the ARM resource ID of the same component.
output appInsightsAppId string = appInsights.properties.AppId
output appInsightsId string = appInsights.id
output logAnalyticsWorkspaceId string = logAnalyticsWorkspaceId
output portalUrl string = 'https://portal.azure.com/#@/resource${sreAgent.id}/overview'
