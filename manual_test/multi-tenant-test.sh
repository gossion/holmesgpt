#!/bin/bash
# Simple curl-based test for AKS multi-tenant support

set -e

# Configuration
HOLMES_SERVER="http://localhost:5050"
CLUSTER_RESOURCE_ID="/subscriptions/6276724d-3cab-4a80-92af-97baf900ba3f/resourceGroups/guwe-rg-oidc-demo-1/providers/Microsoft.ContainerService/managedClusters/aks-oidc-demo"
TENANT_ID="72f988bf-86f1-41af-91ab-2d7cd011db47"

ACCESS_TOKEN=$(az account get-access-token --resource https://management.azure.com --query accessToken -o tsv)

if [ -z "$ACCESS_TOKEN" ]; then
    echo "Failed to get access token. Please run: az login"
    exit 1
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${HOLMES_SERVER}/api/chat" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
    "ask": "What nodes are running in the cluster?",
    "context": {
        "cloud": "azure",
        "resource_id": "${CLUSTER_RESOURCE_ID}",
        "access_token": "${ACCESS_TOKEN}",
        "tenant_id": "${TENANT_ID}"
    }
}
EOF
)

HTTP_BODY=$(echo "$RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "$HTTP_BODY" | jq -r '.answer // .analysis // .'
else
    echo "Error (HTTP ${HTTP_CODE}):"
    echo "$HTTP_BODY" | jq '.' 2>/dev/null || echo "$HTTP_BODY"
    exit 1
fi
