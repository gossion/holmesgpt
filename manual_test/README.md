
## multi-tenant-config.yaml
```yaml
model: azure/*
api_key: *
api_base: https://*.openai.azure.com/
api_version: 2024-02-15-preview

toolsets:
  # Disable local Kubernetes toolsets - we're using AKS multi-tenant instead
  kubernetes/core:
    enabled: false
  kubernetes/live-metrics:
    enabled: false
  kubernetes/kube-prometheus-stack:
    enabled: false
  kubernetes/krew-extras:
    enabled: false
  kubernetes/kube-lineage-extras:
    enabled: false

  # Enable AKS multi-tenant toolset for per-request credential injection
  aks/multi-tenant:
    enabled: true
```

## server:
$ poetry run python server.py --config-file ./multi-tenant-config.yaml


## client:
$ bash multi-tenant-test.sh
Nodes running in the cluster:

1. `aks-nodepool1-31093287-vmss000000`
   - Internal IP: `10.224.0.4`
   - Instance Type: `Standard_D4ads_v6`
   - OS: `Ubuntu 22.04.5 LTS`
   - Kubernetes Version: `v1.32.7`

2. `aks-nodepool1-31093287-vmss000001`
   - Internal IP: `10.224.0.5`
   - Instance Type: `Standard_D4ads_v6`
   - OS: `Ubuntu 22.04.5 LTS`
   - Kubernetes Version: `v1.32.7`