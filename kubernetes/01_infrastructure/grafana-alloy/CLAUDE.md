# Grafana Alloy

Deploys the `k8s-monitoring` Helm chart (Alloy) to push metrics/events/logs to Grafana
Cloud, plus a separate `grafana-alloy-rules` Application syncing `PrometheusRule` CRDs
from `cluster-rules/` to Grafana Cloud's Mimir ruler via Alloy's
`mimir.rules.kubernetes` component. Rule evaluation happens server-side in Mimir, not
in-cluster — alerts keep firing/resolving correctly even during a full cluster outage.

## What's git-managed vs. manual

| Layer                          | Where                                          | Managed         |
| ------------------------------ | ---------------------------------------------- | --------------- |
| Metrics/events/logs collection | `application-grafana-alloy.yaml` values        | git (this repo) |
| Alert rule definitions         | `cluster-rules/*.yaml` (`PrometheusRule` CRDs) | git (this repo) |
| Contact points + routing       | Cloud Alertmanager config API (see below)      | **manual**      |

**Why routing is manual:** it lives in Grafana Cloud's Alertmanager, not as a Kubernetes
CRD this cluster's ArgoCD can sync. The config also embeds the Telegram bot token in
plaintext, so it could not be committed as-is anyway. Grafana Cloud has a Terraform
provider that could manage this declaratively, but adopting it for one receiver would add
a second IaC tool/state store to a repo that's otherwise pure Kustomize+ArgoCD.

## Two Alertmanagers, only one of which matters

Grafana Cloud exposes **two independent Alertmanagers**. Confusing them is the single
easiest mistake to make here:

| Alertmanager                     | Gets our alerts? | Config location                       |
| -------------------------------- | ---------------- | ------------------------------------- |
| Grafana **built-in** ("Grafana") | **No, never**    | Grafana UI: Alerting → Contact points |
| Grafana **Cloud** (Mimir)        | **Yes**          | Mimir config API (see below)          |

Our `PrometheusRule` CRDs are _data source-managed_ rules: Alloy syncs them to the Mimir
ruler, the ruler evaluates them, and the ruler dispatches firing alerts to the **Cloud
Alertmanager**. Grafana's built-in Alertmanager is never in that path, so anything
configured under Alerting → Contact points / Notification policies has no effect on these
alerts.

**This cost a full debugging session on 2026-08-13.** A correct Telegram contact point and
routing tree sat in the built-in Alertmanager and delivered nothing for weeks. Two things
hid it:

- The contact point **Test** button calls the integration directly and bypasses the
  Alertmanager, so it passes even when routing is broken or in the wrong Alertmanager. A
  green Test proves the bot token and chat ID, nothing more.
- `manageAlerts: false` on the `grafanacloud-wieseschwarm-prom` data source hides these
  rules from Alerting → Alert rules and Alert activity, so the UI looks empty.

Diagnose delivery with the `grafanacloud-usage` data source, not the UI:
`grafanacloud_instance_ruler_notifications_sent_total:rate5m` (ruler dispatched),
`grafanacloud_instance_alertmanager_alerts` (alerts held) and
`..._alertmanager_notifications_total` (notifications sent). Alerts held with zero
notifications **and** zero failures means the Alertmanager received the alert and had no
receiver to send it to.

## Cloud Alertmanager configuration

| Item                | Value                                                                |
| ------------------- | -------------------------------------------------------------------- |
| Endpoint            | `https://alertmanager-prod-eu-west-2.grafana.net`                    |
| Config API          | `GET`/`POST` `/api/v1/alerts`                                        |
| Basic auth user     | `1636417` (Alertmanager instance ID, not the Prometheus instance ID) |
| Basic auth password | Access policy token, scopes `alerts:read` + `alerts:write`           |

Read the live config:

```bash
curl -s -u "1636417:$AM_TOKEN" \
  https://alertmanager-prod-eu-west-2.grafana.net/api/v1/alerts
```

`alertmanager_config: ""` means nothing is configured and Mimir falls back to a default
that silently discards every alert. `mimirtool alertmanager load ./config.yaml` is the
supported equivalent of a POST.

Current routing (as of 2026-08-13): every severity goes to Telegram; `critical` repeats
every 2h instead of 4h.

**Email is not configured.** The Cloud Alertmanager has no mail server of its own, so
`email_configs` needs a `global.smtp_smarthost` pointing at an SMTP relay you supply —
unlike the built-in Alertmanager, where Grafana Cloud provides SMTP. Adding
`critical` → Telegram + email therefore means supplying SMTP credentials **and** two
sibling routes with `continue: true` on the first: a route dispatches to exactly one
receiver, and the root receiver applies only when no child route matches.

`docs/superpowers/plans/2026-06-09-prometheus-rules-alerts.md` Tasks 5-7 describe the
original bot creation steps, but its Tasks 6-7 send you to the **wrong Alertmanager** —
follow the README's Grafana Cloud section instead.

## Job label reference

Alloy's `clusterMetrics`/`telemetryServices` collectors register under
`integrations/kubernetes/<component>` job names, not the plain names
(`kube-state-metrics`, `node-exporter`, etc.) that upstream Prometheus-ecosystem alert
rules (e.g. `kube-prometheus-stack`'s bundled `kubernetes-mixin` rules) assume.
**Exception: node-exporter is scraped by a separate feature, `hostMetrics.linuxHosts`,
not `clusterMetrics`** — its job label follows that feature's own convention instead:
no `kubernetes/` segment, and an underscore. Job names as of 2026-08-11, confirmed
against the `k8s-monitoring` chart's own defaults (not inferred from a naming pattern):

| Component          | Feature                  | Job label                                    |
| ------------------ | ------------------------ | -------------------------------------------- |
| kube-state-metrics | `clusterMetrics`         | `integrations/kubernetes/kube-state-metrics` |
| kubelet            | `clusterMetrics`         | `integrations/kubernetes/kubelet`            |
| cadvisor           | `clusterMetrics`         | `integrations/kubernetes/cadvisor`           |
| node-exporter      | `hostMetrics.linuxHosts` | `integrations/node_exporter`                 |

**Any alert rule adapted from an upstream source must have its `job=` selectors rewritten
to match this table.** Getting this wrong doesn't error — the rule just never fires,
silently. See "Metric filtering" below for the other independent silent-failure gate
(allowlist tuning) that must also be checked before trusting an adapted rule.

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

## Extracting more alerts from kube-prometheus-stack later

```bash
helm template prometheus-community/kube-prometheus-stack \
  --set defaultRules.create=true \
  --set prometheus.enabled=false --set alertmanager.enabled=false \
  --set grafana.enabled=false --set kubeStateMetrics.enabled=false \
  --set nodeExporter.enabled=false --set prometheusOperator.enabled=false \
  --set kubeApiServer.enabled=false --set kubeControllerManager.enabled=false \
  --set kubeScheduler.enabled=false --set kubeEtcd.enabled=false \
  --set kubeProxy.enabled=false \
| yq 'select(.kind == "PrometheusRule")'
```

Rewrite every `job=` selector per the table above, verify every referenced metric exists
in Grafana Cloud Explore (adding to `includeMetrics` if not), and drop any `cluster`
label references from `by`/`on` clauses — this account's series don't carry one.
