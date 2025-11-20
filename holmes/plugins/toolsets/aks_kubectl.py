"""Generic kubectl tool for AKS multi-tenant support.

This module provides a generic kubectl execution tool that can route between:
- Azure SDK RunCommand API (for multi-tenant AKS clusters)
- Local kubectl (for default/local Kubernetes clusters)

The tool uses a single generic interface instead of multiple specific tools,
providing flexibility while maintaining security through context-based routing.
"""

import json
import logging
from typing import Any, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.aks_base import BaseAKSTool


class CallKubectl(BaseAKSTool):
    """Generic kubectl command execution tool with multi-tenant Azure support.

    This tool provides a unified interface for executing kubectl commands that can:
    - Route to Azure SDK RunCommand for multi-tenant AKS clusters (when request_context contains Azure credentials)
    - Fall back to local kubectl for default clusters (when no Azure context is provided)

    Design rationale for generic tool vs specific tools:

    Advantages:
    1. Flexibility: LLM can construct any kubectl command without being limited by pre-defined tool schemas
    2. Simplified Maintenance: One tool implementation replaces 23+ specific tools in kubernetes.yaml
    3. Unified Routing Logic: Azure multi-tenant and local execution paths are centralized in one place
    4. Easier Testing: Single tool to test with various kubectl command patterns
    5. Better LLM Performance: Modern LLMs excel at constructing kubectl commands from natural language

    Considerations:
    1. Parameter Validation: Generic tool cannot validate kubectl arguments as strictly as typed parameters
    2. Security: Need to validate args to prevent dangerous operations (delete, apply, etc.)
    3. Error Messages: Generic tool may produce less helpful error messages compared to specific tools

    Security:
    - Read-only operations are preferred
    - Dangerous operations (delete, apply, patch) should require user approval
    - All credentials are passed via context and never exposed to LLM

    Example usage by LLM:
        call_kubectl(args="get pod api-server -n prod -o json")
        call_kubectl(args="logs deployment/checkout-service -n prod --tail=100")
        call_kubectl(args="describe node node-1")
    """

    def __init__(self, toolset: "AKSToolset"):
        super().__init__(
            name="call_kubectl",
            description=(
                "Execute kubectl commands to query Kubernetes resources. "
                "For Azure multi-tenant clusters, uses Azure SDK RunCommand API. "
                "Otherwise uses local kubectl. "
                "Supports all standard kubectl commands for reading cluster state "
                "(get, describe, logs, etc.). "
                "DO NOT include 'kubectl' prefix in the args parameter."
            ),
            parameters={
                "args": ToolParameter(
                    description=(
                        "kubectl command arguments (e.g., 'get pod api-server -n prod -o json'). "
                        "DO NOT include 'kubectl' prefix. "
                        "Use '-o json' or '-o yaml' for structured output when querying resources. "
                        "Examples: "
                        "'get pods -n production -l app=api', "
                        "'describe pod api-server-xyz -n production', "
                        "'logs deployment/checkout-service -n prod --tail=100'"
                    ),
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute kubectl command with routing based on context.

        Execution flow:
        1. Extract kubectl args from parameters
        2. Check if Azure multi-tenant context exists
        3. Route to Azure SDK or local kubectl accordingly
        4. Parse and return results

        Args:
            params: Tool parameters containing 'args' field
            context: Tool invocation context (may contain request_context with Azure credentials)

        Returns:
            StructuredToolResult with:
                - status: SUCCESS if command executed successfully, ERROR otherwise
                - data: Parsed command output (JSON if possible, otherwise raw text)
                - error: Error message if command failed
                - params: Original parameters for debugging
        """
        kubectl_args = params.get("args", "").strip()

        if not kubectl_args:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter 'args'. Please provide kubectl command arguments.",
                params=params,
            )

        # Security check: validate kubectl_args doesn't contain dangerous operations
        # This is a basic check - more sophisticated validation may be needed
        dangerous_commands = ["delete", "apply", "create", "patch", "replace", "edit"]
        first_word = kubectl_args.split()[0].lower() if kubectl_args else ""

        if first_word in dangerous_commands:
            logging.warning(
                f"Attempted to execute potentially dangerous kubectl command: {first_word}"
            )
            # For now, we allow these but log a warning
            # In production, you may want to require user approval via APPROVAL_REQUIRED status

        # Check if Azure multi-tenant context is provided
        azure_ctx = self._get_resource_context(context)

        try:
            if azure_ctx:
                # Azure multi-tenant path
                logging.info(
                    f"Executing kubectl command via Azure SDK: kubectl {kubectl_args} "
                    f"(subscription={azure_ctx.subscription_id}, "
                    f"resource_group={azure_ctx.resource_group}, "
                    f"resource={azure_ctx.resource_name})"
                )
                result = self._execute_kubectl_via_azure_sdk(azure_ctx, kubectl_args)
            else:
                # Local kubectl path
                logging.info(
                    f"Executing kubectl command locally: kubectl {kubectl_args}"
                )
                result = self._execute_kubectl_locally(kubectl_args)
        except ImportError as e:
            # Azure SDK not installed
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )
        except NotImplementedError as e:
            # Feature not yet implemented (should not happen with current code)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )
        except Exception as e:
            # Unexpected error during execution
            logging.exception(f"Failed to execute kubectl command: {kubectl_args}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to execute kubectl command: {str(e)}",
                params=params,
            )

        # Check if command succeeded
        exit_code = result.get("exit_code", -1)
        if exit_code != 0:
            stderr = result.get("stderr", "Unknown error")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"kubectl command failed (exit code {exit_code}): {stderr}",
                params=params,
                return_code=exit_code,
            )

        # Parse output
        stdout = result.get("stdout", "")
        data = self._parse_kubectl_output(stdout)

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
            return_code=exit_code,
        )

    def _parse_kubectl_output(self, stdout: str) -> Any:
        """Parse kubectl output into structured data.

        Attempts to parse JSON/YAML output into Python objects.
        Falls back to raw text if parsing fails.

        Args:
            stdout: kubectl command output

        Returns:
            Parsed data (dict/list if JSON, otherwise string)
        """
        if not stdout or not stdout.strip():
            return {"output": ""}

        # Try to parse as JSON (common for kubectl -o json)
        if stdout.strip().startswith("{") or stdout.strip().startswith("["):
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                pass

        # Fall back to raw text
        return {"output": stdout}

    def get_parameterized_one_liner(self, params: Dict[str, Any]) -> str:
        """Generate a human-readable one-line description of the tool call.

        This is used for logging and displaying tool usage to users.

        Args:
            params: Tool parameters

        Returns:
            One-line description like "kubectl get pods -n production"
        """
        args = params.get("args", "")
        return f"kubectl {args}"
