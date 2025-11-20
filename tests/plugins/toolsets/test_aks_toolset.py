"""Tests for AKS multi-tenant toolset."""

from unittest.mock import Mock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.aks_base import AzureResourceContext
from holmes.plugins.toolsets.aks_kubectl import CallKubectl
from holmes.plugins.toolsets.aks_toolset import AKSToolset
from tests.conftest import create_mock_tool_invoke_context


class TestAzureResourceContext:
    """Tests for AzureResourceContext."""

    def test_context_creation(self):
        ctx = AzureResourceContext(
            cloud="azure",
            resource_id="/subscriptions/sub-123/resourceGroups/rg-test/providers/Microsoft.ContainerService/managedClusters/cluster-test",
            access_token="test-token-123",
            subscription_id="sub-123",
            resource_group="rg-test",
            resource_name="cluster-test",
            tenant_id="tenant-123",
        )

        assert ctx.cloud == "azure"
        assert ctx.subscription_id == "sub-123"
        assert ctx.resource_group == "rg-test"
        assert ctx.resource_name == "cluster-test"
        assert ctx.access_token == "test-token-123"
        assert ctx.tenant_id == "tenant-123"

    def test_context_repr_sanitizes_token(self):
        ctx = AzureResourceContext(
            cloud="azure",
            resource_id="/subscriptions/sub-123/resourceGroups/rg-test/providers/Microsoft.ContainerService/managedClusters/cluster-test",
            access_token="test-token-123",
            subscription_id="sub-123",
            resource_group="rg-test",
            resource_name="cluster-test",
        )

        repr_str = repr(ctx)
        assert "test-token-123" not in repr_str
        assert "***REDACTED***" in repr_str
        assert "sub-123" in repr_str


class TestBaseAKSTool:
    """Tests for BaseAKSTool."""

    def test_parse_resource_id_valid(self):
        tool = CallKubectl(toolset=Mock())

        resource_id = "/subscriptions/sub-123/resourceGroups/rg-test/providers/Microsoft.ContainerService/managedClusters/cluster-test"
        result = tool._parse_resource_id(resource_id)

        assert result is not None
        subscription_id, resource_group, resource_name = result
        assert subscription_id == "sub-123"
        assert resource_group == "rg-test"
        assert resource_name == "cluster-test"

    def test_parse_resource_id_invalid(self):
        tool = CallKubectl(toolset=Mock())

        invalid_ids = [
            "",
            "/invalid/resource/id",
            "/subscriptions/sub-123",
            "not-a-resource-id",
        ]

        for invalid_id in invalid_ids:
            result = tool._parse_resource_id(invalid_id)
            assert result is None

    def test_get_resource_context_no_request_context(self):
        tool = CallKubectl(toolset=Mock())

        context = create_mock_tool_invoke_context(
            max_token_count=1000,
        )

        result = tool._get_resource_context(context)
        assert result is None

    def test_get_resource_context_non_azure_cloud(self):
        tool = CallKubectl(toolset=Mock())

        context = create_mock_tool_invoke_context(
            max_token_count=1000, request_context={"cloud": "aws"}
        )

        result = tool._get_resource_context(context)
        assert result is None

    def test_get_resource_context_missing_required_fields(self):
        tool = CallKubectl(toolset=Mock())

        context = create_mock_tool_invoke_context(
            max_token_count=1000, request_context={"cloud": "azure"}
        )

        with pytest.raises(ValueError, match="missing 'resource_id' field"):
            tool._get_resource_context(context)

    def test_get_resource_context_invalid_resource_id(self):
        tool = CallKubectl(toolset=Mock())

        context = create_mock_tool_invoke_context(
            max_token_count=1000,
            request_context={
                "cloud": "azure",
                "resource_id": "invalid-id",
                "access_token": "token-123",
            },
        )

        with pytest.raises(ValueError, match="Failed to parse Azure resource_id"):
            tool._get_resource_context(context)

    def test_get_resource_context_valid(self):
        tool = CallKubectl(toolset=Mock())

        context = create_mock_tool_invoke_context(
            max_token_count=1000,
            request_context={
                "cloud": "azure",
                "resource_id": "/subscriptions/sub-123/resourceGroups/rg-test/providers/Microsoft.ContainerService/managedClusters/cluster-test",
                "access_token": "token-123",
                "tenant_id": "tenant-123",
            },
        )

        result = tool._get_resource_context(context)

        assert result is not None
        assert result.cloud == "azure"
        assert result.subscription_id == "sub-123"
        assert result.resource_group == "rg-test"
        assert result.resource_name == "cluster-test"
        assert result.access_token == "token-123"
        assert result.tenant_id == "tenant-123"


class TestCallKubectl:
    """Tests for CallKubectl tool."""

    def test_invoke_missing_args(self):
        toolset = AKSToolset()
        tool = toolset.tools[0]

        context = create_mock_tool_invoke_context(max_token_count=1000)

        result = tool._invoke({}, context)

        assert result.status == StructuredToolResultStatus.ERROR
        assert "Missing required parameter" in result.error

    @patch("subprocess.run")
    def test_invoke_local_execution_success(self, mock_run):
        mock_run.return_value = Mock(
            returncode=0, stdout='{"apiVersion": "v1"}', stderr=""
        )

        toolset = AKSToolset()
        tool = toolset.tools[0]

        context = create_mock_tool_invoke_context(max_token_count=1000)

        result = tool._invoke({"args": "version --client --output=json"}, context)

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None
        # Note: mock_run is called multiple times:
        # 1. During toolset initialization (checking kubectl availability)
        # 2. During actual tool execution
        assert mock_run.call_count >= 1

    @patch("subprocess.run")
    def test_invoke_local_execution_failure(self, mock_run):
        mock_run.return_value = Mock(
            returncode=1, stdout="", stderr="Error: kubectl not found"
        )

        toolset = AKSToolset()
        tool = toolset.tools[0]

        context = create_mock_tool_invoke_context(max_token_count=1000)

        result = tool._invoke({"args": "get pods"}, context)

        assert result.status == StructuredToolResultStatus.ERROR
        assert "kubectl command failed" in result.error

    def test_parse_kubectl_output_json(self):
        toolset = AKSToolset()
        tool = toolset.tools[0]

        json_output = '{"apiVersion": "v1", "kind": "Pod"}'
        result = tool._parse_kubectl_output(json_output)

        assert isinstance(result, dict)
        assert result["apiVersion"] == "v1"
        assert result["kind"] == "Pod"

    def test_parse_kubectl_output_text(self):
        toolset = AKSToolset()
        tool = toolset.tools[0]

        text_output = "NAME    READY   STATUS\npod-1   1/1     Running"
        result = tool._parse_kubectl_output(text_output)

        assert isinstance(result, dict)
        assert "output" in result
        assert "pod-1" in result["output"]

    def test_get_parameterized_one_liner(self):
        toolset = AKSToolset()
        tool = toolset.tools[0]

        params = {"args": "get pods -n production"}
        one_liner = tool.get_parameterized_one_liner(params)

        assert one_liner == "kubectl get pods -n production"


class TestAKSToolset:
    """Tests for AKSToolset."""

    def test_toolset_initialization(self):
        toolset = AKSToolset()

        assert toolset.name == "aks/multi-tenant"
        assert len(toolset.tools) == 1
        assert isinstance(toolset.tools[0], CallKubectl)

    def test_toolset_prerequisites(self):
        toolset = AKSToolset()

        assert len(toolset.prerequisites) == 1
        prerequisite = toolset.prerequisites[0]
        assert prerequisite.enabled in [True, False]

    def test_get_example_config(self):
        toolset = AKSToolset()
        config = toolset.get_example_config()

        assert isinstance(config, dict)
        assert config == {}
