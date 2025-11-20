"""Azure Kubernetes Service (AKS) multi-tenant toolset.

This toolset provides tools for investigating and troubleshooting AKS clusters
in multi-tenant environments where credentials are passed per-request.

Features:
- Per-request credential injection via request_context
- OAuth2 access token authentication
- Azure SDK RunCommand integration for remote kubectl execution
- Automatic routing between Azure SDK and local kubectl
- Credentials never exposed to LLM

Usage:
    Client provides request_context with Azure credentials:
    {
        "ask": "Why is my pod failing?",
        "context": {
            "cloud": "azure",
            "resource_id": "/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ContainerService/managedClusters/{cluster}",
            "access_token": "eyJ0eXAiOiJKV1Qi...",
            "tenant_id": "87654321-4321-4321-4321-210987654321"
        }
    }
"""

import logging
from typing import List

from holmes.core.tools import (
    StaticPrerequisite,
    Tool,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.aks_kubectl import CallKubectl


class AKSToolset(Toolset):
    """Azure Kubernetes Service multi-tenant toolset.

    This toolset provides generic kubectl execution capabilities with automatic
    routing between Azure SDK (for multi-tenant scenarios) and local kubectl
    (for default scenarios).

    Prerequisites:
    - For local execution: kubectl must be installed and configured
    - For Azure multi-tenant: Request must include Azure credentials in context

    Configuration:
    This toolset does not require configuration in config.yaml - it uses
    per-request context for multi-tenant scenarios and falls back to local
    kubectl when no Azure context is provided.
    """

    def __init__(self):
        # Check prerequisites
        prerequisite = StaticPrerequisite(enabled=False, disabled_reason="Initializing")

        super().__init__(
            name="aks/multi-tenant",
            description=(
                "Azure Kubernetes Service multi-tenant toolset. "
                "Provides generic kubectl command execution with automatic routing "
                "between Azure SDK RunCommand (for multi-tenant scenarios) and "
                "local kubectl (for default scenarios). "
                "Supports per-request credential injection via request_context."
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/aks/",
            icon_url="https://swimburger.net/media/ppnn3pcl/azure.png",
            prerequisites=[prerequisite],
            is_default=False,  # Not enabled by default - requires explicit configuration
            tools=[],  # Initialize with empty tools first
            tags=[ToolsetTag.CLUSTER],  # Server-side tool
        )

        # Now that parent is initialized and self.name exists, create the tools
        self.tools = self._create_tools()

        # Check prerequisites and update status
        enabled, disabled_reason = self._check_prerequisites()
        prerequisite.enabled = enabled
        prerequisite.disabled_reason = disabled_reason

    def _create_tools(self) -> List[Tool]:
        """Create the list of tools for this toolset.

        Returns:
            List of Tool instances
        """
        return [
            CallKubectl(toolset=self),
        ]

    def get_example_config(self) -> dict:
        return {}

    def _check_prerequisites(self) -> tuple[bool, str]:
        """Check if prerequisites for this toolset are met.

        For AKS multi-tenant toolset:
        - Local kubectl is optional (only needed for non-Azure execution)
        - Azure SDK integration is optional (only needed for multi-tenant scenarios)
        - Toolset is always enabled because it can handle both scenarios

        Returns:
            Tuple of (enabled, disabled_reason)
        """
        # Check if kubectl is available for local execution
        import subprocess

        kubectl_available = False
        try:
            result = subprocess.run(
                ["kubectl", "version", "--client"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                kubectl_available = True
                logging.debug("kubectl is available for local execution")
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logging.debug(f"kubectl not available: {e}")

        # Note: Azure SDK is checked at runtime when needed, not during initialization
        # This allows the toolset to work in both multi-tenant and local scenarios

        if kubectl_available:
            return True, ""
        else:
            return True, (
                "kubectl not found - local execution will not work. "
                "Azure multi-tenant execution via SDK will still work if request_context is provided."
            )
