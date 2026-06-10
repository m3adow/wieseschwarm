# Demo application

This app is kept on `main` **deliberately**. It is a living reference implementation
that exercises every infrastructure primitive in the cluster and doubles as a smoke
test after upgrades or rebuilds: if the demo is healthy, storage, database, backup,
metrics, ingress, certificates, and autoscaling all work.

| Primitive       | Exercised by                                                                    |
| --------------- | ------------------------------------------------------------------------------- |
| Piraeus storage | `persistentvolumeclaim-demo.yaml` (RWO, default StorageClass)                   |
| MariaDB CRs     | `database-demo.yaml`, `user-demo.yaml`, `grant-demo.yaml`                       |
| K8up backup     | `schedule-demo-k8up.yaml` + `sopssecret-demo-k8up-b2.yaml`                      |
| Metrics/alerts  | `prometheusrule-demo.yaml` (scraped via Grafana Alloy)                          |
| LAN ingress     | `ingressroute-demo-lan.yaml` (`demo.wieseschwarm.lan`, default TLSStore)        |
| Public ingress  | `ingressroute-demo-public.yaml` (`demo.wieseclan.eu.org` via Cloudflare Tunnel) |
| cert-manager    | `certificate-demo-public.yaml` (Let's Encrypt production)                       |
| VPA             | `verticalpodautoscaler-demo.yaml`                                               |

## Intentional deviations from production patterns

- **`strategy: Recreate`** on the Deployment: the PVC is RWO, so the old pod must
  release it before the replacement can attach. Any single-replica workload with an
  RWO Piraeus PVC needs this.
- **VPA `minReplicas: 1`**: the VPA updater refuses to evict workloads below
  2 replicas by default, so without this override `updateMode: Auto` would never
  apply recommendations here. Consequence: each VPA-driven eviction briefly takes
  the demo down. Acceptable for the demo; for real single-replica apps prefer
  `updateMode: "Initial"` or accept the same tradeoff consciously.
- **Monthly K8up schedule with `keepLast: 1`**: demo pacing only, to show the
  mechanism without burning B2 storage. Real apps should use the nightly
  schedule/retention template from `kubernetes/01_infrastructure/k8up/CLAUDE.md`.

## Public exposure

`demo.wieseclan.eu.org` is exposed to the internet through the Cloudflare Tunnel —
see "Public exposure" in `kubernetes/CLAUDE.md` for the convention (the IngressRoute
annotation here is the reference example). The app serves a static page and has no
secrets beyond its own generated DB password and dedicated B2 bucket; removing
`ingressroute-demo-public.yaml` (and its Certificate) makes it LAN-only.
