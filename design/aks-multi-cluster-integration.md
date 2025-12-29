# Multi-Cluster Support via MCP Integration

## Overview

HolmesGPT supports multi-cluster scenarios by integrating with the **aks-mcp** (Azure Kubernetes Service Model Context Protocol) server. This design enables HolmesGPT to troubleshoot multiple AKS clusters dynamically using request-scoped context injection, without requiring pre-configured kubeconfig files.

## Design Principles

1. **Leverage MCP Protocol**: Use Model Context Protocol for tool execution
2. **Context Injection**: Pass cluster credentials via `_tool_context` parameter (universal across stdio/SSE/HTTP transports)
3. **Multi-cluster Logic in MCP Server**: Keep cloud-specific logic in aks-mcp, HolmesGPT remains cloud-agnostic
4. **Security First**: Credentials never exposed to LLM
5. **Request Scoping**: Context is per-request, not global configuration

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         HolmesGPT                                │
│                                                                   │
│  User Request with context                                       │
│    ↓                                                             │
│  ChatRequest/InvestigateRequest                                  │
│    ↓                                                             │
│  ToolCallingLLM (request_context flows through)                  │
│    ↓                                                             │
│  ToolInvokeContext                                               │
│    - request_context: Dict[str, Any]  ◀── NEW                    │
│    ↓                                                             │
│  RemoteMCPTool                                                   │
│    - Injects _tool_context into params                           │
│    - _tool_context contains request_context                      │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       │ MCP Protocol (stdio/SSE/HTTP)
                       │ params = {
                       │   "command": "get pods",
                       │   "_tool_context": {
                       │     "request_context": {
                       │       "azure_token": "...",
                       │       "aks_resource_id": "..."
                       │     }
                       │   }
                       │ }
                       ↓
┌─────────────────────────────────────────────────────────────────┐
│                       aks-mcp Server                             │
│                                                                   │
│  Tool Handler                                                    │
│    - Extracts _tool_context from params                          │
│    - Injects into Go context                                     │
│    - Removes _tool_context from params                           │
│    ↓                                                             │
│  RunCommandExecutor (Multi-cluster)                              │
│    - Extract request_context from Go context                    │
│    - Parse AKS resource ID (subscription/rg/cluster)            │
│    - Create Azure SDK client with provided token                │
│    - Call Azure RunCommand API with kubectl command             │
│    ↓                                                             │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        │ Azure RunCommand API
                        ↓
              Azure AKS Cluster(s)
```


## Request Flow Example

```
1. Client sends request:
   POST /api/chat
   {
     "ask": "Why is my pod crashing?",
     "context": {
       "azure_token": "eyJ0eXAi...",
       "aks_resource_id": "/subscriptions/.../managedClusters/cluster-1"
     }
   }

2. HolmesGPT extracts context and passes to ToolCallingLLM

3. LLM decides to call kubectl tool (without seeing credentials)

4. RemoteMCPTool injects _tool_context into params:
   {
     "command": "kubectl get pods -n prod",
     "_tool_context": {
       "request_context": {
         "azure_token": "eyJ0eXAi...",
         "aks_resource_id": "/subscriptions/.../cluster-1"
       }
     }
   }

5. MCP protocol sends to aks-mcp server

6. aks-mcp extracts _tool_context and routes to RunCommandExecutor

7. RunCommandExecutor:
   - Parses resource ID
   - Creates Azure client with token
   - Calls Azure RunCommand API
   - Returns kubectl output

8. Response flows back to HolmesGPT → LLM → User
```

## Security Considerations

1. **Credentials Hidden from LLM**
   - `request_context` never included in LLM messages
   - `ToolInvokeContext.model_dump()` redacts sensitive values
   - Logging happens after context extraction

2. **Universal Transport Support**
   - Context via `_tool_context` parameter (not headers)
   - Works with stdio/SSE/HTTP uniformly
   - No dependency on HTTP-specific features

3. **Request Scoping**
   - Context is per-request, not global
   - No persistent credential storage
   - Azure client created fresh per request

## Configuration Examples

### HolmesGPT Config
```yaml
# ~/.holmes/config.yaml
custom_toolsets:
  - id: aks-mcp
    type: mcp
    url: http://aks-mcp-server:8000/mcp/messages
    mode: sse
    context_fields: [request_context]
    enabled: true
```

### aks-mcp Server
```bash
aks-mcp --transport sse --port 8000 --enable-multi-cluster
```

### Client Request
```python
requests.post("http://holmes-server/api/chat", json={
    "ask": "Why is checkout-service pod crashing?",
    "context": {
        "azure_token": "eyJ0eXAi...",
        "aks_resource_id": "/subscriptions/.../managedClusters/prod-cluster"
    }
})
```

## Benefits

1. **Separation of Concerns**: HolmesGPT (AI reasoning) vs aks-mcp (cloud execution)
2. **Reusability**: aks-mcp can be used by other AI agents
3. **Maintainability**: Cloud-specific code isolated in aks-mcp
4. **Flexibility**: Local vs remote execution decided at runtime
5. **Extensibility**: Pattern applies to other cloud providers (EKS, GKE)
6. **Backward Compatible**: Works without context (uses local kubeconfig)

## Conclusion

This MCP-based design provides secure, flexible multi-cluster support while maintaining clean separation between AI reasoning (HolmesGPT) and cloud-specific execution (aks-mcp). The implementation is production-ready and has been tested with real AKS clusters.
