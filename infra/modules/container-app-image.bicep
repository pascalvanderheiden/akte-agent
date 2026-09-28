/*
  Resolves the container image a Container App renders to, for both of this
  template's `host: containerapp` services (agent-service, obo-mcp-server).

  Two things have to be true at once, and a hardcoded image satisfies neither:

  1. FIRST PROVISION — the application image does not exist in ACR yet, because
     `azd deploy` builds it after `azd provision`. The app still has to come up,
     and the platform probes it on the ingress `targetPort`. A bootstrap image
     that listens on some *other* port (the classic
     `containerapps-helloworld`, which hardcodes port 80 and ignores `PORT`)
     leaves the first revision stuck in `ActivationFailed`. The bootstrap image
     here listens on whichever port it is told to, and it is told the same
     value the ingress declares — the caller passes one value that feeds both,
     so the two cannot drift.

  2. EVERY LATER PROVISION — `azd provision` runs again (CI does it before each
     deploy). Rendering the bootstrap image again would tear a healthy
     application revision down and replace it with the stand-in. So the image
     currently on the Container App is read back and returned unchanged, making
     provisioning a no-op for the running application.

  The bootstrap port is set through environment variables and NEVER through a
  `command`/`args` override: `azd deploy` swaps `template.containers[0].image`
  on the existing app and leaves the rest of the container definition alone, so
  a command set here would survive the deploy and run against the application
  image — the app would start the stand-in's web server instead of itself.
  Leftover environment variables are inert by comparison, and the next
  provision drops them.

  Scope: this module is called from the container app modules themselves, so
  the lookup resolves in the same resource group as the Container App it is
  resolving an image for. Calling it from anywhere else needs that scope passed
  in explicitly, or it reads the wrong app.

  `exists` comes from azd, which records `SERVICE_<NAME>_RESOURCE_EXISTS` in the
  environment once a service has been deployed; ARM cannot read a resource that
  may not exist, hence the flag rather than a probe.
*/

@description('Name of the Container App whose currently-deployed image must be preserved')
param containerAppName string

@description('True once azd has deployed an application image to this Container App (SERVICE_<NAME>_RESOURCE_EXISTS)')
param exists bool

@description('Ingress target port; the bootstrap image is told to listen on exactly this port')
param targetPort int

@description('Image used only until the first real application image is deployed. Must take its listening port from the environment variables below rather than hardcoding one. Pinned to a major version so a republished floating tag cannot change first-provision behaviour.')
param bootstrapImage string = 'mcr.microsoft.com/dotnet/samples:aspnetapp-10.0'

resource deployedApp 'Microsoft.App/containerApps@2024-03-01' existing = if (exists) {
  name: containerAppName
}

var deployedImage = exists ? deployedApp!.properties.template.containers[0].image : ''
var isBootstrap = empty(deployedImage)

@description('The image to render: the already-deployed application image, or the bootstrap image on a first create')
output image string = isBootstrap ? bootstrapImage : deployedImage

@description('Extra environment variables, set only while the bootstrap image is in use, that make it listen on the ingress target port')
output bootstrapEnv array = isBootstrap
  ? [
      { name: 'ASPNETCORE_HTTP_PORTS', value: '${targetPort}' }
      { name: 'ASPNETCORE_URLS', value: 'http://+:${targetPort}' }
    ]
  : []

@description('True when the bootstrap image is being used rather than a deployed application image')
output isBootstrap bool = isBootstrap
