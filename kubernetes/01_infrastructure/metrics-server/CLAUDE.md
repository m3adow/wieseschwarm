# CLAUDE.md

Helm chart Application at wave 2. Namespace: `metrics-server`.

metrics-server provides the Kubernetes metrics API (`metrics.k8s.io`), which powers
`kubectl top` and supplies the CPU/memory usage data that the VPA recommender consumes.

## Talos note

Talos kubelets use self-signed certificates. This deployment passes `--kubelet-insecure-tls`
to bypass TLS verification when scraping kubelet metrics. This is intentional for homelab use.
The production-grade alternative (kubelet certificate rotation + `kubelet-serving-cert-approver`)
is documented in the [Talos metrics-server guide](https://docs.siderolabs.com/kubernetes-guides/monitoring-and-observability/deploy-metrics-server).

## Verify

After ArgoCD syncs, both commands should return resource data (not errors):

```bash
kubectl top nodes
kubectl top pods -A
```
