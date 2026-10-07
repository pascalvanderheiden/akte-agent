@description('Name of the Container App')
param name string

@description('Location')
param location string

@description('Tags')
param tags object = {}

@description('Container Apps Environment ID')
param containerAppsEnvId string

@description('Container Registry name')
param containerRegistryName string

@description('App Insights connection string')
param appInsightsConnectionString string

@description('App Insights resource ID (for resource-scoped KQL queries from the Traces panel)')
param appInsightsResourceId string = ''

@description('Cosmos DB endpoint')
param cosmosDbEndpoint string

@description('Key Vault URI')
param keyVaultUri string

@description('Microsoft Foundry endpoint')
param foundryEndpoint string

@description('Foundry model deployment name')
param foundryModelDeployment string

@description('Orchestrator model deployment name')
param orchestratorModelDeployment string

@description('Deep-reasoning model deployment name')
param deepReasoningModelDeployment string

@description('Fast model deployment name')
param fastModelDeployment string

@description('Bing Search endpoint')
param bingSearchEndpoint string

@description('Foundry project name')
param foundryProjectName string

@description('Foundry project endpoint (e.g. https://host/api/projects/proj)')
param foundryProjectEndpoint string = ''

@description('Hosted agent name deployed in Foundry')
param foundryAgentName string = 'kratos-agent'

@description('Azure Blob Storage endpoint for skills')
param blobStorageEndpoint string

@description('Static Web App URL for CORS (e.g. https://xxx.azurestaticapps.net)')
param staticWebAppUrl string = ''

@description('True once azd has deployed an application image to this Container App (SERVICE_AGENT_SERVICE_RESOURCE_EXISTS)')
param exists bool = false

@description('Readiness probe failure window in seconds; every accepted value exceeds the warm-pool warm-up timeout (at most 90 seconds).')
@minValue(91)
@maxValue(2400)
param readinessProbeFailureWindowSeconds int = 100

var readinessProbeFailureThreshold = 10
var readinessProbePeriodSeconds = int((readinessProbeFailureWindowSeconds + readinessProbeFailureThreshold - 1) / readinessProbeFailureThreshold)

@description('Readiness probe request timeout in seconds.')
@minValue(1)
@maxValue(240)
param readinessProbeTimeoutSeconds int = 5

@description('Liveness probe period in seconds.')
@minValue(1)
@maxValue(240)
param livenessProbePeriodSeconds int = 30

@description('Liveness probe failures allowed before the container is restarted.')
@minValue(1)
@maxValue(10)
param livenessProbeFailureThreshold int = 3

@description('Liveness probe request timeout in seconds.')
@minValue(1)
@maxValue(240)
param livenessProbeTimeoutSeconds int = 5

@description('Delay before liveness checks begin, independent of warm-pool readiness.')
@minValue(1)
@maxValue(60)
param livenessProbeInitialDelaySeconds int = 30

// Ingress port and bootstrap listener port are the same value by construction.
var ingressTargetPort = 8000

// ─── Container image ───
// Never hardcode an image here. container-app-image.bicep keeps the first
// provision bootable on a stand-in image that listens on this module's own
// ingress port, and leaves an already-deployed application image untouched
// on every later provision; its header explains why a fixed placeholder
// breaks both cases. obo-mcp-server.bicep uses the identical mechanism.
module containerImage './container-app-image.bicep' = {
  name: '${name}-image'
  params: {
    containerAppName: name
    exists: exists
    targetPort: ingressTargetPort
  }
}

// ─── ACR pull identity ───
// A User-Assigned Managed Identity is created for ACR access so that the
// AcrPull role assignment exists BEFORE the Container App tries to validate
// the registry config.  The System-Assigned identity is still used for all
// other service-to-service RBAC (Cosmos, Key Vault, etc.).

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrName = replace(containerRegistryName, '-', '')

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource acrPullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${name}-acr-pull'
  location: location
  tags: tags
}

resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, acrPullIdentity.id, acrPullRoleId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: acrPullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ─── Container App ───
resource agentService 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'agent-service' })
  dependsOn: [acrPullRole]
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${acrPullIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvId
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: '${acrName}.azurecr.io'
          identity: acrPullIdentity.id
        }
      ]
      ingress: {
        external: true
        targetPort: ingressTargetPort
        transport: 'http'
        corsPolicy: {
          allowedOrigins: empty(staticWebAppUrl) ? ['*'] : [staticWebAppUrl]
          allowedMethods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
          allowedHeaders: ['*']
          maxAge: 3600
        }
      }
    }
    template: {
      containers: [
        {
          name: 'agent-service'
          image: containerImage.outputs.image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: containerImage.outputs.isBootstrap ? [] : [
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: ingressTargetPort
                scheme: 'HTTP'
              }
              periodSeconds: readinessProbePeriodSeconds
              failureThreshold: readinessProbeFailureThreshold
              timeoutSeconds: readinessProbeTimeoutSeconds
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: ingressTargetPort
                scheme: 'HTTP'
              }
              initialDelaySeconds: livenessProbeInitialDelaySeconds
              periodSeconds: livenessProbePeriodSeconds
              failureThreshold: livenessProbeFailureThreshold
              timeoutSeconds: livenessProbeTimeoutSeconds
            }
          ]
          env: concat(
            [
              { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
              { name: 'APPLICATION_INSIGHTS_RESOURCE_ID', value: appInsightsResourceId }
              { name: 'COSMOS_DB_ENDPOINT', value: cosmosDbEndpoint }
              { name: 'KEY_VAULT_URI', value: keyVaultUri }
              { name: 'FOUNDRY_ENDPOINT', value: foundryEndpoint }
              { name: 'MODEL_DEPLOYMENT_ORCHESTRATOR', value: orchestratorModelDeployment }
              { name: 'MODEL_DEPLOYMENT_DEEP_REASONING', value: deepReasoningModelDeployment }
              { name: 'MODEL_DEPLOYMENT_FAST', value: fastModelDeployment }
              { name: 'FOUNDRY_MODEL_DEPLOYMENT', value: foundryModelDeployment }
              { name: 'FOUNDRY_PROJECT_NAME', value: foundryProjectName }
              { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
              { name: 'FOUNDRY_AGENT_NAME', value: foundryAgentName }
              { name: 'BING_SEARCH_ENDPOINT', value: bingSearchEndpoint }
              { name: 'BLOB_STORAGE_ENDPOINT', value: blobStorageEndpoint }
              { name: 'OTEL_SERVICE_NAME', value: 'kratos-agent-service' }
              { name: 'ENVIRONMENT', value: 'production' }
              { name: 'ALLOWED_ORIGINS', value: empty(staticWebAppUrl) ? '*' : staticWebAppUrl }
            ],
            // Only non-empty while the bootstrap image is in use; it takes its
            // listening port from these. Nothing here overrides the container
            // command, which `azd deploy` would carry over onto the real image.
            containerImage.outputs.bootstrapEnv
          )
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

output id string = agentService.id
output name string = agentService.name
output url string = 'https://${agentService.properties.configuration.ingress.fqdn}'
output principalId string = agentService.identity.principalId
output readinessProbePeriodSeconds int = readinessProbePeriodSeconds
output readinessProbeFailureThreshold int = readinessProbeFailureThreshold
output readinessProbeTimeoutSeconds int = readinessProbeTimeoutSeconds
output livenessProbePeriodSeconds int = livenessProbePeriodSeconds
output livenessProbeFailureThreshold int = livenessProbeFailureThreshold
output livenessProbeTimeoutSeconds int = livenessProbeTimeoutSeconds
output livenessProbeInitialDelaySeconds int = livenessProbeInitialDelaySeconds
output ingressTargetPort int = ingressTargetPort
