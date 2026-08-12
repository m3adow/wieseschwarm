# Grafana Alloy

Deploys the `k8s-monitoring` Helm chart (Alloy) to push metrics/events/logs to Grafana
Cloud, plus a separate `grafana-alloy-rules` Application syncing `PrometheusRule` CRDs
from `cluster-rules/` to Grafana Cloud's Mimir ruler via Alloy's
`mimir.rules.kubernetes` component.

## Metric filtering: two layers, know which one to use

There are two independent places to drop or keep metrics — they run at different
pipeline stages and answer different questions:

| Layer                                                                            | Stage                          | Scope                                              | Question it answers                                                  |
| -------------------------------------------------------------------------------- | ------------------------------ | -------------------------------------------------- | -------------------------------------------------------------------- |
| `destinations.<name>.metricProcessingRules`                                      | post-scrape, at `remote_write` | everything shipped to that destination, any source | "Never ship this metric to Grafana Cloud, no matter where it's from" |
| `<feature>.<source>.metricsTuning` (e.g. `clusterMetrics.kubelet.metricsTuning`) | pre-scrape, at collection      | one specific source only                           | "Don't even bother scraping this from this one integration"          |

**`metricProcessingRules` is the general-purpose default.** It's raw Alloy config text
injected straight into the `prometheus.remote_write` block using the native
[`write_relabel_config`](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.remote_write/#write_relabel_config-block)
syntax, so it catches metrics from `clusterMetrics`, `hostMetrics`, ServiceMonitor-scraped
apps — anything — with one rule:

```yaml
destinations:
  grafana-cloud-metrics:
    metricProcessingRules: |
      write_relabel_config {
        source_labels = ["__name__"]
        regex          = "go_.*|rest_client_.*"
        action         = "drop"
      }
```

Use this for cluster-wide defaults you never want to reconsider per source. A syntax
mistake here fails Alloy's own config validation loudly at startup/reload — it can't
silently no-op the way a misplaced values key can (see below).

**Per-source `metricsTuning` is for fine-tuning one integration's own scrape.** It's
cheaper (drops before Alloy even scrapes/stores the series, not just before shipping it),
but it only affects that one source. **It is scoped per source, not per feature** — there
is no top-level `clusterMetrics.metricsTuning` key in the chart's schema. Only
`clusterMetrics.<source>.metricsTuning` (e.g. `clusterMetrics.kubelet.metricsTuning`,
`clusterMetrics.cadvisor.metricsTuning`) exists. `hostMetrics.linuxHosts.metricsTuning`
is the exception — that feature's tuning genuinely is one block for the whole feature,
not per-source, since `linuxHosts` only has one source (node-exporter).

**A wrong key here doesn't error — Helm doesn't validate unknown values keys, so the
config is silently accepted and does nothing.** A `clusterMetrics.metricsTuning` block
sat in this file for months doing nothing before this was caught (see git history on
`application-grafana-alloy.yaml` around the `excludeMetrics` block for the incident).
Before trusting either layer, verify the metric actually disappears from Grafana Cloud
Explore — don't assume from the values file alone.

Relatedly: Alloy/Prometheus relabel regexes are RE2 and fully anchored, not shell globs.
`go_*` matches only the literal `go_`/`go`/`go__`… — never `go_goroutines`. Use `go_.*`.
