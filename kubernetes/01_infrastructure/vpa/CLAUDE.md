# CLAUDE.md

Helm chart Application at wave 1. Namespace: `vpa`. Wave 1 is required because VPA installs the `VerticalPodAutoscaler` CRD; wave-2 config apps may create VPA objects and would fail if the CRD is not yet registered.

VPA (Vertical Pod Autoscaler) automatically recommends — and optionally adjusts — CPU and
memory requests for running workloads based on observed usage. It consists of three controllers:

| Controller            | Role                                                                 |
| --------------------- | -------------------------------------------------------------------- |
| **Recommender**       | Watches container usage via the metrics API; stores recommendations  |
| **Updater**           | Evicts pods when their requests deviate from recommendations         |
| **Admission webhook** | Mutates pods at creation to inject VPA-recommended resource requests |

## How to use VPA for a workload

Create a `VerticalPodAutoscaler` object in the same namespace as the target Deployment.
Start with `updateMode: "Off"` to collect recommendations without touching pods:

```yaml
---
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: my-app
  namespace: my-namespace
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: my-app
  updatePolicy:
    updateMode: "Off"
  resourcePolicy:
    containerPolicies:
      - containerName: "*"
        minAllowed:
          cpu: 10m
          memory: 32Mi
        maxAllowed:
          cpu: "2"
          memory: 2Gi
```

## Update modes

| Mode       | Effect                                                             |
| ---------- | ------------------------------------------------------------------ |
| `Off`      | Recommendations computed and stored; nothing applied               |
| `Initial`  | Requests set at pod creation only; no evictions                    |
| `Recreate` | Requests set at creation + pods evicted when recommendations drift |
| `Auto`     | Same as `Recreate` today; may use in-place updates in future       |

## Viewing recommendations

After 24–48 hours of data collection:

```bash
kubectl describe vpa my-app -n my-namespace
```

Look for the `Recommendation` section. When satisfied with the values, promote to
`updateMode: "Initial"` for stateless workloads.

## File naming

VPA object files follow the standard convention: `verticalpodautoscaler-<name>.yaml`.
