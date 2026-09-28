/*
  Resolves the container image a Container App renders to, for both of this
  template's `host: containerapp` services (agent-service, obo-mcp-server).

  A first provision happens before azd builds the application image, so it
  needs a stand-in that listens on the ingress port. Later provisions must
  retain the deployed application image rather than replacing it with that
  stand-in.
*/

@description('Name of the Container App whose currently-deployed image must be preserved')
param containerAppName string

@description('True once azd has deployed an application image to this Container App (SERVICE_<NAME>_RESOURCE_EXISTS)')
param exists bool

@description('Ingress target port; the bootstrap image is told to listen on exactly this port')
param targetPort int

@description('Image used only until the first real application image is deployed')
param bootstrapImage string = 'mcr.microsoft.com/dotnet/samples:aspnetapp-10.0'

resource deployedApp 'Microsoft.App/containerApps@2024-03-01' existing = if (exists) {
  name: containerAppName
}

var deployedImage = exists ? deployedApp!.properties.template.containers[0].image : ''
var bootstrapImageWithoutDigest = split(bootstrapImage, '@')[0]
var bootstrapTagSeparator = lastIndexOf(bootstrapImageWithoutDigest, ':')
var bootstrapPathSeparator = lastIndexOf(bootstrapImageWithoutDigest, '/')
var bootstrapRepository = bootstrapTagSeparator > bootstrapPathSeparator
  ? substring(bootstrapImageWithoutDigest, 0, bootstrapTagSeparator)
  : bootstrapImageWithoutDigest
var legacyBootstrapRepository = 'mcr.microsoft.com/azuredocs/containerapps-helloworld'
var isBootstrap = empty(deployedImage) || startsWith(deployedImage, '${bootstrapRepository}:') || startsWith(deployedImage, '${legacyBootstrapRepository}:')

@description('The image to render: the already-deployed application image, or the bootstrap image on a first create')
output image string = isBootstrap ? bootstrapImage : deployedImage

@description('Extra environment variables, set only while the bootstrap image is in use, that make it listen on the ingress target port')
output bootstrapEnv array = isBootstrap
  ? [
      { name: 'ASPNETCORE_HTTP_PORTS', value: '${targetPort}' }
      { name: 'ASPNETCORE_URLS', value: 'http://+:${targetPort}' }
    ]
  : []
