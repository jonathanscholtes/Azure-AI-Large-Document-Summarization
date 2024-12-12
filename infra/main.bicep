targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name which is used to generate a short unique hash for each resource')
param environmentName string

@minLength(1)
@maxLength(64)
@description('Name which is used to generate a short unique hash for each resource')
param projectName string

@minLength(1)
@description('Primary location for all resources')
param location string


var resourceToken = uniqueString(environmentName,location,az.subscription().subscriptionId)

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${projectName}-${environmentName}-${location}-${resourceToken}'
  location: location
}


module managedIdentity 'core/security/managed-identity.bicep' = {
  name: 'managed-identity'
  scope: resourceGroup
  params: {
    name: 'id-${projectName}-${environmentName}'
    location: location
  }
}

module openAIService 'core/ai/openai-account.bicep' = {
  name: 'openAIService'
  scope: resourceGroup
  params: {
    name: 'aoai-${projectName}-${environmentName}-${resourceToken}'
    location: location
    identityName: managedIdentity.outputs.managedIdentityName
    customSubdomain: 'openai-app-${resourceToken}'
  }
  dependsOn: [managedIdentity]
}

module blobStorageServices 'core/storage/blob-storage-account.bicep' = {
  name: 'blobStorageServices'
  scope: resourceGroup
  params: {
    accountName: 'sa${projectName}${environmentName}'
    location: location
    identityName: managedIdentity.outputs.managedIdentityName
  }
dependsOn:[managedIdentity]
}

module monitoring 'core/monitor/monitoring.bicep' = {
  name: 'monitoring'
  scope: resourceGroup
  params: {
    location: location
    logAnalyticsName: 'log-${projectName}-${environmentName}'
    applicationInsightsName: 'appi-${projectName}-${environmentName}'
    applicationInsightsDashboardName: 'appid-${projectName}-${environmentName}'
  }
}


module appServicePlanLinux 'core/host/app-service.bicep' = {
  name: 'appServicePlanLinux'
  scope: resourceGroup
  params: {
    location:location
    name:  'asp-lnx-${projectName}-${environmentName}-${resourceToken}'
    linuxMachine: true
  }
  dependsOn:[managedIdentity]
}

module summaryFunction 'app/summary-function.bicep' = {
  name: 'summaryFunction'
  scope: resourceGroup
  params: {
    appServicePlanName: appServicePlanLinux.outputs.appServicePlanName
    functionAppName: 'func-summary-${resourceToken}'
    location: location
    StorageBlobURL:blobStorageServices.outputs.storageBlobURL
    StorageAccountName: blobStorageServices.outputs.StorageAccountName
    logAnalyticsWorkspaceName: monitoring.outputs.logAnalyticsWorkspaceName
    appInsightsName: monitoring.outputs.applicationInsightsName
    OpenAIEndPoint: openAIService.outputs.endpoint
    identityName: managedIdentity.outputs.managedIdentityName
  }
  dependsOn:[appServicePlanLinux, monitoring,openAIService]
}

output resourceGroupName string = resourceGroup.name
output functionAppName string = summaryFunction.outputs.functionAppName
