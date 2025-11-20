#!/usr/bin/env python3
"""Test script for AKS multi-tenant support.

This script tests the HolmesGPT server's ability to interact with an AKS cluster
using per-request credentials via the context parameter.

Requirements:
- Azure CLI must be logged in (az login)
- User must have appropriate permissions on the AKS cluster
"""

import sys

import requests
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential

# Your AKS cluster details
CLUSTER_RESOURCE_ID = "/subscriptions/6276724d-3cab-4a80-92af-97baf900ba3f/resourceGroups/guwe-rg-oidc-demo-1/providers/Microsoft.ContainerService/managedClusters/aks-oidc-demo"
TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"
HOLMES_SERVER_URL = "http://localhost:5050"


def get_azure_access_token() -> str:
    """Get Azure access token using DefaultAzureCredential.

    This will use the current Azure CLI login or other available credentials.

    Returns:
        Access token string

    Raises:
        AzureError: If unable to acquire token
    """
    print("🔐 Acquiring Azure access token...")
    try:
        # DefaultAzureCredential will try multiple authentication methods:
        # 1. Environment variables
        # 2. Managed Identity
        # 3. Azure CLI (az login)
        # 4. Azure PowerShell
        # 5. Interactive browser
        credential = DefaultAzureCredential()

        # Get token for Azure Resource Manager scope
        # This is needed to call Azure management APIs
        token = credential.get_token("https://management.azure.com/.default")

        print("✅ Successfully acquired access token")
        print(f"   Token expires at: {token.expires_on}")
        return token.token

    except AzureError as e:
        print(f"❌ Failed to acquire Azure access token: {e}")
        print("\nTroubleshooting:")
        print("1. Make sure you're logged in: az login")
        print(
            "2. Set correct subscription: az account set --subscription <subscription-id>"
        )
        print(
            "3. Check you have access to the AKS cluster: az aks show --resource-group guwe-rg-oidc-demo-1 --name aks-oidc-demo"
        )
        sys.exit(1)


def test_aks_query(question: str, access_token: str) -> None:
    """Send a question to Holmes server with AKS context.

    Args:
        question: The question to ask Holmes
        access_token: Azure access token for authentication
    """
    print(f"\n📤 Sending question to Holmes: {question}")

    request_payload = {
        "ask": question,
        "context": {
            "cloud": "azure",
            "resource_id": CLUSTER_RESOURCE_ID,
            "access_token": access_token,
            "tenant_id": TENANT_ID,
        },
    }

    # Don't print the full token for security
    print(
        f"   Context: cloud=azure, tenant={TENANT_ID[:8]}..., token={access_token[:20]}..."
    )

    try:
        response = requests.post(
            f"{HOLMES_SERVER_URL}/api/chat",
            json=request_payload,
            timeout=300,  # 5 minutes timeout for complex investigations
        )

        print(f"\n📥 Response status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print("✅ Success! Holmes response:")
            print("=" * 80)

            # Print the analysis
            if "analysis" in result:
                print(result["analysis"])
            elif "answer" in result:
                print(result["answer"])
            else:
                print(result)

            print("=" * 80)

            # Print tool calls if available
            if "tool_calls" in result:
                print(f"\n🔧 Tools used: {len(result['tool_calls'])}")
                for i, tool_call in enumerate(result["tool_calls"], 1):
                    print(f"   {i}. {tool_call.get('tool_name', 'unknown')}")

        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")

    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to Holmes server at {HOLMES_SERVER_URL}")
        print(
            "   Make sure the server is running: poetry run python server.py --config-file ./multi-tenant-config.yaml"
        )
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("❌ Request timed out after 5 minutes")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


def main():
    """Main test function."""
    print("=" * 80)
    print("HolmesGPT AKS Multi-Tenant Test")
    print("=" * 80)
    print("\n🎯 Target cluster: aks-oidc-demo")
    print("   Resource group: guwe-rg-oidc-demo-1")
    print("   Subscription: 6276724d-3cab-4a80-92af-97baf900ba3f")
    print(f"   Tenant: {TENANT_ID}")

    # Get access token
    access_token = get_azure_access_token()

    # Test questions
    test_questions = [
        # Basic cluster information
        "What nodes are running in the cluster?",
        # # Pod information
        # "List all pods in all namespaces",
        # # Check for any issues
        # "Are there any pods that are not running or have issues?",
        # # Namespace-specific query
        # "Show me all resources in the kube-system namespace",
    ]

    print("\n" + "=" * 80)
    print("Running test queries...")
    print("=" * 80)

    for i, question in enumerate(test_questions, 1):
        print(f"\n{'=' * 80}")
        print(f"Test {i}/{len(test_questions)}")
        print(f"{'=' * 80}")
        test_aks_query(question, access_token)

        if i < len(test_questions):
            print("\n⏳ Waiting before next query...")
            import time

            time.sleep(2)

    print("\n" + "=" * 80)
    print("✅ All tests completed!")
    print("=" * 80)


if __name__ == "__main__":
    main()
