# Grafana Alloy

Deploys the `k8s-monitoring` Helm chart (Alloy) to push metrics/events/logs to Grafana
Cloud, plus a separate `grafana-alloy-rules` Application syncing `PrometheusRule` CRDs
from `cluster-rules/` to Grafana Cloud's Mimir ruler via Alloy's
`mimir.rules.kubernetes` component (LogQL rules go to the Loki ruler instead, see below). Rule evaluation happens server-side in Mimir, not
in-cluster — alerts keep firing/resolving correctly even during a full cluster outage.

## What's git-managed vs. manual

| Layer                          | Where                                          | Managed         |
| ------------------------------ | ---------------------------------------------- | --------------- |
| Metrics/events/logs collection | `application-grafana-alloy.yaml` values        | git (this repo) |
| Alert rule definitions         | `cluster-rules/*.yaml` (`PrometheusRule` CRDs) | git (this repo) |
| Contact points + routing       | `config/alertmanager/`, pushed by a CronJob    | git (this repo) |

**How routing reaches Grafana Cloud:** the config lives in Grafana Cloud's Alertmanager,
not in a Kubernetes object ArgoCD could apply. So ArgoCD deploys the config files as a
ConfigMap, and the `alertmanager-config-sync` CronJob pushes them to the Cloud config API
every 30 minutes. See "Cloud Alertmanager configuration" below. Rejected alternatives: the
Grafana Terraform provider (a second IaC tool and state store for one receiver) and Alloy's
`mimir.alerts.kubernetes` (experimental stability level; a breaking change could stop the
whole Alloy instance from loading its config).

## Two rule paths: metrics (Mimir) and logs (Loki)

Alloy syncs `PrometheusRule` CRDs to two rulers, split by one label:

| Rule language | `PrometheusRule` label | Alloy component          | Ruler                              |
| ------------- | ---------------------- | ------------------------ | ---------------------------------- |
| PromQL        | none (default)         | `mimir.rules.kubernetes` | Mimir (`prometheus-prod-65-...`)   |
| LogQL         | `ruler: loki`          | `loki.rules.kubernetes`  | Loki (`logs-prod-012.grafana.net`) |

The Mimir component excludes `ruler: loki` with a `NotIn` match expression, and the Loki
component selects only it. Both sides are needed: Mimir rejects LogQL it cannot parse,
and Loki would reject PromQL. A rule without the label goes to Mimir, as every rule did
before the Loki path existed.

Use a LogQL rule only for a signal that exists solely in logs (for example a job's
summary line with no matching metric). Set the group's `interval` to the resolution the
signal needs: the Loki ruler re-reads the whole range window on every evaluation.

**Verified end to end (2026-10-15)** with a temporary always-firing LogQL rule: the shared
`grafana-cloud-credentials` token may write Loki rules, the Loki ruler at the push hostname
accepts them, and it hands firing alerts to the **same Cloud Alertmanager** as the Mimir
ruler, so they reach Telegram through the existing routing. Mimir's rule count was
unchanged by the `NotIn` exclusion.

Two rules for LogQL rules:

- **Write `expr: |-`, not `expr: |`.** Loki stores the expression without a trailing
  newline, so with `|` the copy Alloy reads back never matches the CRD, and
  `loki.rules.kubernetes` re-uploads the group on every `sync_interval` (30 s; visible as
  repeated `updated rule group` log lines). Verified: switching to `|-` stopped the
  uploads. Mimir rules keep `|`; that component does not show the problem.
- **The Alloy selectors must be live before the first `ruler: loki` rule.** When both arrive
  in one sync, `grafana-alloy-rules` can create the rule before Alloy reloads, and the old
  Mimir component (then still selecting everything) uploads it to Mimir, which rejects
  LogQL that is not also valid PromQL. Only the first introduction of the path is exposed.

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
that silently discards every alert. The response also contains the bot token and SMTP
password in plaintext, so do not paste it anywhere unredacted.

### Routing

Every severity goes to Telegram, repeating every 4h. `critical` repeats on Telegram every 2h
and additionally goes to email, repeating every 6h. A route dispatches to exactly one
receiver and the root receiver applies only when no child route matches, so `critical` needs
two sibling routes with `continue: true` on the first. The Cloud Alertmanager has no mail server of its own (unlike Grafana's built-in
one), so the email receiver names its own relay. Like every SMTP value, it comes from the Secret.

`NodeNotReady` inhibits `KubeDaemonSetRolloutStuck` and `PodStuck` (`equal: [cluster]`). One
node outage otherwise floods the chat with every DaemonSet on that node. DaemonSet and pod
alerts carry no `node` label, so the inhibition is cluster-wide: while any node is NotReady,
a genuinely stuck pod on another node is muted too. Accepted for a 3-node homelab.

### Config sync

| Item       | Value                                                                              |
| ---------- | ---------------------------------------------------------------------------------- |
| Source     | `config/alertmanager/alertmanager-config.yaml`, `telegram.tmpl`                    |
| ConfigMap  | `alertmanager-config-<hash>` (`configMapGenerator`)                                |
| Secrets    | SopsSecret `alertmanager-credentials` (`sopssecret-alertmanager-credentials.yaml`) |
| Pusher     | CronJob `alertmanager-config-sync`, `*/30 * * * *`                                 |
| Push token | `MIMIR_API_KEY`, a dedicated `alerts:read` + `alerts:write` access policy token    |

The config in git holds `${VAR}` placeholders for the bot token, chat ID and every SMTP value:
the repo is public. The CronJob's `render` initContainer fills them from the Secret with `yq`
(`envsubst(nu,ne)`, so a missing key fails the Job instead of pushing an empty value) into a
memory-backed `emptyDir`, and `load` pushes it with `mimirtool alertmanager load`. Mimir
rejects config fields that load files (`*_file`), so mounting the Secret and referencing it
from the config is not an option. Mimir loads uploaded template files by name; the config needs
no `templates:` field.

A merge reaches Grafana Cloud at the next half hour. To push immediately:

```bash
kubectl -n monitoring create job --from=cronjob/alertmanager-config-sync push-now
```

It is a CronJob, not an ArgoCD PostSync hook, for two reasons: the SOPS operator updates the
Secret asynchronously after ArgoCD applies the SopsSecret, so a hook can run with the old
secret values; and a hook never corrects drift, while the CronJob overwrites manual edits
within 30 minutes. An invalid config fails the Job (`mimirtool` validates before sending)
and leaves the live config untouched; `KubeJobFailed` (warning, Telegram) and `PodStuck`
(critical, so also email) then report it. Both stay firing until the failed Job is deleted
(`kubectl -n monitoring delete job <name>`). Rotate the push token before its expiry, or
every run fails.

A sync that stops running produces no failed Job at all: suspended, pruned by ArgoCD, or never
successful since it was created. `AlertmanagerConfigSyncStale` (warning, in
`prometheusrule-cluster-metrics-pipeline.yaml`) covers that from
`kube_cronjob_status_last_successful_time`: it fires when the last success is over 2h old or
the series is absent, after 45 minutes. The metric is on the chart's default kube-state-metrics
allowlist.

`successfulJobsHistoryLimit: 0` is deliberate: a run that succeeds on a retry keeps its failed
first pod for as long as the Job exists, and `PodStuck` would page for a failure that already
healed itself. The CronJob's `.status.lastSuccessfulTime` still shows when the last push worked.

### Telegram template and the 4096 limit

Telegram rejects messages over 4096 characters. Alertmanager's Telegram notifier handles that
differently by `parse_mode`: in `HTML` (the default) it replaces the **whole** message with
"Alertmanager notification could not be sent: message length exceeds Telegram limits", and
counts that as a successful send, so `notifications_failed_total` stays 0. In plain text it
truncates instead. Upstream v0.34.1 also counts raw runes, markup and HTML escapes included,
and `'` alone escapes to five characters.

The default template printed every label, annotation and the `generatorURL` per alert; one
URL-encoded `KubeDaemonSetRolloutStuck` expression alone is about 1,400 characters, so three
grouped DaemonSet alerts already overflowed. `telegram.tmpl` therefore prints one line per
alert (`description`, else `summary`, else the alert name), cut at 180 characters, at most 10
lines per firing/resolved section plus `… and N more`, and the receiver sets `parse_mode: ""`.
Worst case is about 3,800 characters. Alerts within a group are sorted by their label set as
strings, so numbered labels order `1, 10, 11, 2`; Go templates offer no sort to change that.

Test template or routing changes locally before merging (Docker required):

```bash
python3 -I scripts/alertmanager-telegram-test.py
```

It renders the config with dummy secrets, runs it in a local `prom/alertmanager` against a
Telegram capture server and an SMTP stub, and checks size limits, plain-text output, routing
per severity, inhibition, and that every placeholder maps to its field and to a CronJob env
entry. A new `${VAR}` placeholder therefore needs the CronJob env entry, the Secret key, the
harness's `DUMMY_ENV` and the break-glass variable list.

The `alertmanager-harness` pre-commit hook runs it automatically: locally on pushes that touch
`config/` or the harness, and in CI on such PRs. That includes Renovate bumps of the CronJob's
`yq` and `mimirtool` images, because the harness renders with the CronJob's `render` image and
validates the result with `mimirtool alertmanager verify` from its `load` image.

### Break-glass manual upload

Prefer the normal path even under pressure: fix the config in git, merge, wait for the
ArgoCD sync and run the push-now Job above. Upload by hand only when that path is broken.

A plain `kubectl patch` of the CronJob's `suspend` does not hold: `grafana-alloy-config` self-
heals it back within minutes, and the root app self-heals the child Application. Stop the
sync from the top down:

```bash
kubectl -n argocd patch application apps-of-apps --type json \
  -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]'
kubectl -n argocd patch application grafana-alloy-config --type json \
  -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]'
kubectl -n monitoring patch cronjob alertmanager-config-sync -p '{"spec":{"suspend":true}}'
```

Then render into a temp dir outside the repo and upload. Export the secrets with `read -rs`
so they appear neither in shell history nor in the process list:

```bash
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
d=kubernetes/01_infrastructure/grafana-alloy/config/alertmanager
for v in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID SMTP_TO SMTP_FROM SMTP_USERNAME SMTP_PASSWORD SMTP_SMARTHOST \
  MIMIR_API_KEY; do
  read -rsp "$v: " "${v?}" && export "${v?}"; echo
done
expr=$(yq '.spec.jobTemplate.spec.template.spec.initContainers[0].args[0]' \
  "$d/../cronjob-alertmanager-config-sync.yaml" | sed -E "s/^yq '(.*)' \/config.*/\1/")
docker run --rm -u "$(id -u)" -v "$PWD/$d:/in:ro" \
  -e TELEGRAM_BOT_TOKEN -e TELEGRAM_CHAT_ID -e SMTP_TO -e SMTP_FROM -e SMTP_USERNAME -e SMTP_PASSWORD -e SMTP_SMARTHOST \
  mikefarah/yq:4.54.1 "$expr" /in/alertmanager-config.yaml > "$work/rendered.yaml"
docker run --rm -u "$(id -u)" -v "$work:/w:ro" -v "$PWD/$d:/in:ro" -e MIMIR_API_KEY \
  -e MIMIR_ADDRESS=https://alertmanager-prod-eu-west-2.grafana.net -e MIMIR_TENANT_ID=1636417 \
  grafana/mimirtool:3.2.2 alertmanager load /w/rendered.yaml /in/telegram.tmpl
```

To return to normal, land the same change in git, then re-apply the root from git with
`make argocd-apps-bootstrap`. That restores the root's `automated` block, the root restores
the child's, and the child sets `suspend: false` again.

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
