@description('Name of the Storage Account')
param name string

@description('Location')
param location string

@description('Tags')
param tags object = {}

@description('Name of the blob container for skills')
param skillsContainerName string = 'skills'

@description('Subnet ID for the blob private endpoint. Leave empty to skip creating one (e.g. local/dev deployments with no VNet).')
param subnetId string = ''

@description('VNet ID for the blob private DNS zone link. Required when subnetId is set.')
param vnetId string = ''

var createPrivateEndpoint = !empty(subnetId) && !empty(vnetId)

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource skillsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: skillsContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Private access to blob. Required whenever public network access is turned
// off (directly, or by an Azure Policy that enforces it): without this the
// container apps cannot reach the skills container and every blob call fails
// with AuthorizationFailure.
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-09-01' = if (createPrivateEndpoint) {
  name: '${name}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${name}-plsc'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = if (createPrivateEndpoint) {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource dnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (createPrivateEndpoint) {
  parent: privateDnsZone
  name: '${name}-vnet-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-09-01' = if (createPrivateEndpoint) {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob-dns'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

output id string = storageAccount.id
output name string = storageAccount.name
output endpoint string = 'https://${storageAccount.name}.blob.${environment().suffixes.storage}'
