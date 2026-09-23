// Demo export copy of Kratos's blob-storage module.
//
// Diverges from the repo-root infra/modules/blob-storage.bicep: the standalone
// hosted-agent demo keeps the storage account publicly reachable (RBAC still
// governs access) instead of adding a blob private endpoint and its matching
// private DNS zone behind a VNet. A Foundry hosted agent isn't
// injected into the export's network, so a private endpoint would leave the
// agent unable to sync its skills from Blob after `azd up`. For a production
// deployment, re-enable private networking (see the repo-root module).

@description('Name of the Storage Account')
param name string

@description('Location')
param location string

@description('Tags')
param tags object = {}

@description('Name of the blob container for skills')
param skillsContainerName string = 'skills'

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

output id string = storageAccount.id
output name string = storageAccount.name
output endpoint string = 'https://${storageAccount.name}.blob.${environment().suffixes.storage}'
