"""Base classes for Azure AKS multi-tenant toolsets.

This module provides base classes for tools that need to interact with Azure Kubernetes Service
clusters in a multi-tenant environment where credentials are passed per-request via context.

Design: Supports OAuth2 access token authentication for Azure resource access.
Resources are identified by Azure resource IDs and credentials are never exposed to the LLM.
"""

import logging
import re
from typing import ClassVar, Optional, Tuple

from holmes.core.tools import Tool, ToolInvokeContext


class AzureResourceContext:
    """Azure credentials and resource information extracted from request_context.

    This context is used to authenticate and identify Azure resources (primarily AKS clusters,
    but designed to be extensible to other resource types like VMs, storage accounts, etc.).

    Expected request_context format:
    {
        "cloud": "azure",
        "resource_id": "/subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/{resourceType}/{name}",
        "access_token": "eyJ0eXAiOiJKV1Qi...",
        "tenant_id": "87654321-4321-4321-4321-210987654321"  # Optional
    }

    Example for AKS cluster:
    {
        "cloud": "azure",
        "resource_id": "/subscriptions/12345678-1234-1234-1234-123456789012/resourceGroups/prod-rg/providers/Microsoft.ContainerService/managedClusters/prod-cluster",
        "access_token": "eyJ0eXAiOiJKV1Qi...",
        "tenant_id": "87654321-4321-4321-4321-210987654321"
    }
    """

    def __init__(
        self,
        cloud: str,
        resource_id: str,
        access_token: str,
        subscription_id: str,
        resource_group: str,
        resource_name: str,
        tenant_id: Optional[str] = None,
    ):
        """Initialize Azure resource context.

        Args:
            cloud: Cloud provider identifier (must be "azure")
            resource_id: Full Azure resource ID
            access_token: OAuth2 bearer token for Azure authentication
            subscription_id: Azure subscription ID (parsed from resource_id)
            resource_group: Azure resource group name (parsed from resource_id)
            resource_name: Resource name (parsed from resource_id) - e.g., cluster name for AKS
            tenant_id: Optional Azure tenant ID
        """
        self.cloud = cloud
        self.resource_id = resource_id
        self.access_token = access_token
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.resource_name = resource_name
        self.tenant_id = tenant_id

    def __repr__(self) -> str:
        """String representation with sanitized credentials."""
        return (
            f"AzureResourceContext(cloud={self.cloud}, "
            f"subscription_id={self.subscription_id}, "
            f"resource_group={self.resource_group}, "
            f"resource_name={self.resource_name}, "
            f"tenant_id={self.tenant_id}, "
            f"access_token=***REDACTED***)"
        )


class BaseAKSTool(Tool):
    """Base class for AKS-aware tools that support multi-tenant Azure subscriptions.

    Tools inheriting from this class can:
    - Extract Azure credentials from request_context
    - Parse Azure resource IDs to identify target resources
    - Route execution between Azure SDK (for multi-tenant) and local kubectl (for default)
    - Keep credentials hidden from the LLM

    Usage pattern:
        class MyAKSTool(BaseAKSTool):
            def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
                azure_ctx = self._get_resource_context(context)
                if azure_ctx:
                    # Execute via Azure SDK
                    return self._execute_via_azure_sdk(azure_ctx, params)
                else:
                    # Execute locally
                    return self._execute_locally(params)
    """

    RESOURCE_ID_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"^/subscriptions/(?P<subscription>[^/]+)"
        r"/resourceGroups/(?P<resource_group>[^/]+)"
        r"/providers/(?P<provider>[^/]+)"
        r"/(?P<resource_type>[^/]+)"
        r"/(?P<resource_name>[^/]+)$",
        re.IGNORECASE,
    )

    def _parse_resource_id(self, resource_id: str) -> Optional[Tuple[str, str, str]]:
        """Parse Azure resource ID to extract subscription, resource group, and resource name.

        Supports Azure resource ID format:
        /subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/{provider}/{resourceType}/{resource_name}

        Example:
            /subscriptions/12345678-1234-1234-1234-123456789012/resourceGroups/prod-rg/providers/Microsoft.ContainerService/managedClusters/prod-cluster

        Args:
            resource_id: Azure resource ID string

        Returns:
            Tuple of (subscription_id, resource_group, resource_name) if parsing succeeds, None otherwise
        """
        if not resource_id:
            logging.warning("Empty resource_id provided")
            return None

        match = self.RESOURCE_ID_PATTERN.match(resource_id.strip())
        if not match:
            logging.warning(f"Failed to parse resource_id: {resource_id}")
            return None

        subscription_id = match.group("subscription")
        resource_group = match.group("resource_group")
        resource_name = match.group("resource_name")

        logging.debug(
            f"Parsed resource_id: subscription={subscription_id}, "
            f"resource_group={resource_group}, resource_name={resource_name}"
        )

        return (subscription_id, resource_group, resource_name)

    def _get_resource_context(
        self, context: ToolInvokeContext
    ) -> Optional[AzureResourceContext]:
        """Extract Azure resource context from request_context.

        Validates that:
        - request_context exists
        - cloud provider is "azure"
        - Required fields (resource_id, access_token) are present
        - resource_id can be parsed successfully

        Args:
            context: Tool invocation context containing request_context

        Returns:
            AzureResourceContext if all validations pass, None if this is not an Azure multi-tenant request

        Raises:
            ValueError: If Azure context is partially specified but invalid (missing required fields or unparseable)
        """
        if not context.request_context:
            logging.debug("No request_context provided, using local execution")
            return None

        rc = context.request_context
        cloud = rc.get("cloud", "").lower()

        if cloud != "azure":
            logging.debug(
                f"Cloud provider is '{cloud}', not 'azure', using local execution"
            )
            return None

        # Cloud is Azure, validate required fields
        resource_id = rc.get("resource_id")
        access_token = rc.get("access_token")

        if not resource_id:
            raise ValueError(
                "Azure multi-tenant request missing 'resource_id' field. "
                "Request context must include: cloud='azure', resource_id, access_token"
            )

        if not access_token:
            raise ValueError(
                "Azure multi-tenant request missing 'access_token' field. "
                "Request context must include: cloud='azure', resource_id, access_token"
            )

        # Parse resource_id
        parsed = self._parse_resource_id(resource_id)
        if not parsed:
            raise ValueError(
                f"Failed to parse Azure resource_id: {resource_id}. "
                f"Expected format: /subscriptions/{{sub}}/resourceGroups/{{rg}}/providers/{{provider}}/{{resourceType}}/{{name}}"
            )

        subscription_id, resource_group, resource_name = parsed

        azure_context = AzureResourceContext(
            cloud=cloud,
            resource_id=resource_id,
            access_token=access_token,
            subscription_id=subscription_id,
            resource_group=resource_group,
            resource_name=resource_name,
            tenant_id=rc.get("tenant_id"),
        )

        logging.info(
            f"Extracted Azure resource context: subscription={subscription_id}, "
            f"resource_group={resource_group}, resource_name={resource_name}"
        )

        return azure_context

    def _execute_kubectl_via_azure_sdk(
        self, azure_ctx: AzureResourceContext, kubectl_args: str
    ) -> dict:
        """Execute kubectl command via Azure SDK RunCommand API.

        Uses Azure ContainerService ManagedClusters.begin_run_command() to execute
        kubectl commands against an AKS cluster without requiring local kubeconfig.

        Args:
            azure_ctx: Azure resource context with credentials and resource information
            kubectl_args: kubectl arguments (WITHOUT 'kubectl' prefix)

        Returns:
            Dict with keys:
                - exit_code: int (0 for success)
                - stdout: str (command output)
                - stderr: str (error output)

        Raises:
            ImportError: If azure-mgmt-containerservice is not installed
            Exception: If Azure API call fails
        """
        try:
            from azure.core.credentials import AccessToken
            from azure.mgmt.containerservice import ContainerServiceClient
            from azure.mgmt.containerservice.models import RunCommandRequest
        except ImportError as e:
            logging.error(
                "Azure SDK not available. Install with: pip install azure-mgmt-containerservice"
            )
            raise ImportError(
                "azure-mgmt-containerservice package is required for Azure multi-tenant execution. "
                "Install it with: pip install azure-mgmt-containerservice"
            ) from e

        try:
            from datetime import datetime

            from azure.core.credentials import TokenCredential

            class AccessTokenCredential(TokenCredential):
                """Custom credential that uses the provided access token."""

                def __init__(self, access_token: str):
                    self.access_token = access_token

                def get_token(self, *scopes, **kwargs):  # type: ignore
                    return AccessToken(
                        token=self.access_token,
                        expires_on=int(datetime.now().timestamp()) + 3600,
                    )

            credential = AccessTokenCredential(azure_ctx.access_token)

            client = ContainerServiceClient(
                credential=credential, subscription_id=azure_ctx.subscription_id
            )

            command = f"kubectl {kubectl_args}"
            logging.info(
                f"Executing kubectl command via Azure SDK RunCommand: {command} "
                f"(cluster={azure_ctx.resource_name}, rg={azure_ctx.resource_group})"
            )

            request = RunCommandRequest(command=command, context="")

            poller = client.managed_clusters.begin_run_command(
                resource_group_name=azure_ctx.resource_group,
                resource_name=azure_ctx.resource_name,
                request_payload=request,
            )

            result = poller.result(timeout=300)

            exit_code = result.exit_code if hasattr(result, "exit_code") else 0
            stdout = result.logs if hasattr(result, "logs") else ""

            logging.debug(f"Azure SDK RunCommand completed with exit code {exit_code}")

            return {"exit_code": exit_code, "stdout": stdout, "stderr": ""}

        except Exception as e:
            logging.exception(
                f"Failed to execute kubectl via Azure SDK: {kubectl_args} "
                f"(cluster={azure_ctx.resource_name})"
            )
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Azure SDK RunCommand failed: {str(e)}",
            }

    def _execute_kubectl_locally(self, kubectl_args: str) -> dict:
        """Execute kubectl command locally using subprocess.

        Falls back to local kubectl execution when no Azure context is provided
        or when Azure SDK execution is not available.

        Args:
            kubectl_args: kubectl arguments (WITHOUT 'kubectl' prefix)

        Returns:
            Dict with keys:
                - exit_code: int (0 for success)
                - stdout: str (command output)
                - stderr: str (error output)
        """
        import shlex
        import subprocess

        try:
            command = ["kubectl"] + shlex.split(kubectl_args)

            logging.debug(f"Executing local kubectl command: {' '.join(command)}")

            result = subprocess.run(command, capture_output=True, text=True, timeout=60)

            logging.debug(
                f"kubectl command completed with exit code {result.returncode}"
            )

            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            logging.error(f"kubectl command timed out after 60 seconds: {kubectl_args}")
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after 60 seconds: kubectl {kubectl_args}",
            }
        except Exception as e:
            logging.exception(f"Failed to execute kubectl command: {kubectl_args}")
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Failed to execute kubectl command: {str(e)}",
            }
