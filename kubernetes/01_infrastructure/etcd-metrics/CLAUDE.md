# CLAUDE.md

Scrape configuration for etcd. etcd itself is **Talos-managed**: it runs as a host service on
every node, not as a pod. `talos/wieseschwarm-all-patch.yaml` makes it serve its metrics on port
2381 (see "etcd metrics" in `talos/CLAUDE.md`, which also covers why that port is open to the
LAN). This directory only tells Alloy where to scrape. The alert lives in
`grafana-alloy/cluster-rules/prometheusrule-etcd.yaml`.

## How the scrape finds etcd

- **The EndpointSlice lists the node IPs by hand.** The Service has no selector, because there is
  no pod to select. Update the EndpointSlice when a node's IP changes or a control-plane node is
  added or removed.
- **It depends on Alloy discovering ServiceMonitors through EndpointSlices.** The k8s-monitoring
  chart renders `kubernetes_role = "endpointslice"` for `prometheus.operator.servicemonitors`
  (checked in the `grafana-alloy-alloy-metrics` ConfigMap on 2026-11-27). Alloy's own default is
  `endpoints`. If a chart update switches to it, the scrape finds no targets without any error,
  because Kubernetes does not mirror a hand-written EndpointSlice into an Endpoints object.
- **The `node` label comes from the EndpointSlice's `nodeName`**, through a relabeling in the
  ServiceMonitor. The alert names the node with it.

## What is kept

The ServiceMonitor keeps only these metrics, about 115 series for three nodes:

| Metric                                                                          | Why                                        |
| ------------------------------------------------------------------------------- | ------------------------------------------ |
| `etcd_disk_wal_fsync_duration_seconds` (histogram)                              | Disk latency of every write; read by alert |
| `etcd_disk_backend_commit_duration_seconds` (histogram)                         | Disk latency of the backend commits        |
| `etcd_server_leader_changes_seen_total`, `etcd_server_has_leader`, `_is_leader` | Leader churn and which member leads        |
| `etcd_server_proposals_failed_total`                                            | Writes that never committed                |

Check the scrape in Grafana with `up{job="etcd-metrics"}`, which should show three series at 1.
