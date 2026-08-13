# Demo application

This app is kept on `main` **deliberately**. It is a living reference implementation
that exercises nearly every infrastructure primitive in the cluster and doubles as a
smoke test after upgrades or rebuilds: if the demo is healthy, storage, database,
metrics, ingress, certificates, and autoscaling all work. Backup is the one
exception — it is present as a structural example only and is not exercised.

| Primitive       | Exercised by                                                                                             |
| --------------- | -------------------------------------------------------------------------------------------------------- |
| Piraeus storage | `persistentvolumeclaim-demo.yaml` (RWO, default StorageClass)                                            |
| MariaDB CRs     | `database-demo.yaml`, `user-demo.yaml`, `grant-demo.yaml`                                                |
| K8up backup     | `schedule-demo-k8up.yaml` + `sopssecret-demo-k8up-b2.yaml` (**disabled** — structure only)               |
| Metrics/alerts  | `servicemonitor-demo.yaml` (scrape config) + `prometheusrule-demo.yaml` (alerts), both via Grafana Alloy |
| LAN ingress     | `ingressroute-demo-lan.yaml` (`demo.wieseschwarm.lan`, default TLSStore)                                 |
| Public ingress  | `ingressroute-demo-public.yaml` (`demo.wieseclan.eu.org` via Cloudflare Tunnel)                          |
| cert-manager    | `certificate-demo-public.yaml` (Let's Encrypt production)                                                |
| VPA             | `verticalpodautoscaler-demo.yaml`                                                                        |

## Intentional deviations from production patterns

- **`strategy: Recreate`** on the Deployment: the PVC is RWO, so the old pod must
  release it before the replacement can attach. Any single-replica workload with an
  RWO Piraeus PVC needs this.
- **VPA `minReplicas: 1`**: the VPA updater refuses to evict workloads below
  2 replicas by default, so without this override the Recreate fallback in
  `updateMode: InPlaceOrRecreate` would never trigger for the single-replica demo
  Deployment. In-place resize is always attempted first; eviction is the fallback
  and briefly takes the demo down — acceptable here.
- **K8up backup is disabled**: k8up has no `suspend` field, so the Schedule keeps its
  `backend` block while every job block (`backup`, `prune`, `check`) stays commented
  out — a Schedule with no job blocks reconciles to `Ready` and registers no crons.
  The `k8up-b2` SopsSecret carries deliberately bogus credentials, so uncommenting the
  job blocks alone will not produce working backups; real B2 credentials are needed
  too. Real apps should use the nightly schedule/retention template from
  `kubernetes/01_infrastructure/k8up/CLAUDE.md`.

## Public exposure

`demo.wieseclan.eu.org` is exposed to the internet through the Cloudflare Tunnel —
see "Public exposure" in `kubernetes/CLAUDE.md` for the convention (the IngressRoute
annotation here is the reference example). The app serves a static page and has no
secrets beyond its own generated DB password — the `k8up-b2` values are bogus.
Removing `ingressroute-demo-public.yaml` (and its Certificate) makes it LAN-only.
