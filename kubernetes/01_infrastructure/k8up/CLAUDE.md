# k8up

Backup operator using restic. Helm chart Application at wave 2. Namespace: `k8up`.

## Application sync configuration

`application-k8up.yaml` has two non-default settings that deviate from the project defaults:

**`ServerSideApply=true` (syncOption):** k8up installs large CRDs that exceed the 256 KB annotation size limit used by ArgoCD's default client-side apply. Without SSA, ArgoCD fails to apply the CRDs with an annotation overflow error.

**`argocd.argoproj.io/compare-options: ServerSideDiff=true` (annotation):** ArgoCD uses a bundled local Kubernetes schema for comparison diffs. Kubernetes 1.33 added `ReplicaSet.status.terminatingReplicas`, which ArgoCD's bundled schema does not yet declare. With SSA enabled, this causes every diff check to fail with "field not declared in schema". Setting `ServerSideDiff=true` delegates comparison to the cluster via a dry-run SSA request, so the cluster's own schema is used instead of ArgoCD's local copy. This applies to all ArgoCD versions shipping a schema older than K8s 1.33.

## Adding backups to an application namespace

Work happens inside the `wieseschwarm-applications` private submodule (`kubernetes/02_applications/apps/`).

Each application namespace uses its own dedicated B2 Application Key and its own restic repo password. Credentials are never shared between namespaces and are not mirrored via Reflector — the `k8up` namespace holds no shared backup credentials.

**Prerequisites (one-time per namespace):**

1. In Backblaze B2, create a dedicated Application Key restricted to that application's backup bucket with capabilities: `readFiles`, `writeFiles`, `deleteFiles`, `listBuckets`, `listFiles`, `readBucketEncryption`. One key per bucket — do not reuse a key across applications.
2. Note the Key ID and Application Key (shown once). The `b2` backend talks to the B2 native API directly — no S3 endpoint is needed.
3. Generate a unique restic repo password: `openssl rand -base64 32`. Store it safely — backups cannot be restored without it. Do not reuse the same password across namespaces.

**Per namespace: create `sopssecret-k8up-b2.yaml`**

Create unencrypted, then run `sops -e -i`:

```yaml
---
apiVersion: isindir.github.com/v1alpha3
kind: SopsSecret
metadata:
  name: k8up-b2-sops
  namespace: <target-namespace>
spec:
  secretTemplates:
    - name: k8up-b2
      stringData:
        repository-password: <restic-repo-encryption-password>
        account-id: <b2-application-key-id>
        account-key: <b2-application-key>
```

**Per namespace: create `schedule.yaml`**

```yaml
---
apiVersion: k8up.io/v1
kind: Schedule
metadata:
  name: k8up-schedule
  namespace: <target-namespace>
spec:
  backend:
    repoPasswordSecretRef:
      name: k8up-b2
      key: repository-password
    # Use the native b2 backend, not s3: restic's S3 backend does not work
    # against B2's S3-compatible API in this setup (verified with the demo
    # app — backups failed until switched to b2).
    b2:
      bucket: <b2-bucket-name>
      accountIDSecretRef:
        name: k8up-b2
        key: account-id
      accountKeySecretRef:
        name: k8up-b2
        key: account-key
  backup:
    schedule: "0 2 * * *"
    failedJobsHistoryLimit: 3
    successfulJobsHistoryLimit: 1
  prune:
    schedule: "0 3 * * 0"
    retention:
      keepLast: 5
      keepDaily: 14
      keepWeekly: 4
  check:
    schedule: "0 4 * * 1"
```

`backup` nightly 02:00, `prune` Sunday 03:00 (enforces retention), `check` Monday 04:00 (repo integrity).

**Add both to the application's `kustomization.yaml`:**

```yaml
resources:
  - sopssecret-k8up-b2.yaml
  - schedule.yaml
```

By default, k8up backs up every PVC in the namespace. To exclude a pod's volumes: annotate the pod with `k8up.io/backup: "false"`.

## Monitoring and alerting

`config/servicemonitor-k8up.yaml` scrapes the chart's `k8up-metrics` Service. Alert rules
live with every other rule in
`01_infrastructure/grafana-alloy/cluster-rules/prometheusrule-k8up.yaml`.

**Why this Application is multi-source.** A Helm-source Application cannot carry a
hand-written manifest, and the usual escape hatch — a separate `*-config` Application at
wave N+1 — exists for config that consumes CRDs the parent chart installs. This
ServiceMonitor consumes only `monitoring.coreos.com/v1` from `prometheus-operator-crds`
(wave -4), so it has no ordering dependency on k8up at all and needs no second
Application. `spec.sources` puts it in this Application instead. Renovate still bumps the
chart: its `argocd` manager reads `spec.sources` as well as `spec.source`.

**Why not `metrics.serviceMonitor.enabled: true`.** The chart's template exposes no
`metricRelabelings`, and the endpoint serves 708 series of which only 24 are `k8up_*` —
the rest is controller-runtime, workqueue, and Go runtime boilerplate. The chart's object
also cannot be patched: Kustomize never runs over a Helm source, and giving it one would
mean enabling `--enable-helm` in `argocd-cm` cluster-wide.

### The backup metric gap

`k8up_schedule_last_job_succeeded` emits series for `jobType=check` and `jobType=prune`
**only** — never `backup`. This was verified empirically against a long-running operator
with successful backups in the window, not inferred from the chart. Backup CRs do carry
the `k8up.io/schedule-name` label that `operator/job/job.go` gates on, so the cause is
upstream, not configuration.

Consequently `K8upBackupStale` derives from kube-state-metrics job completion timestamps,
selected via `kube_job_owner{owner_kind="Backup"}`. That owner kind is unique to k8up
here — mariadb-operator's scheduled backup Jobs are owned by a `CronJob`, not its
identically-named `Backup` CRD.

| Alert                   | Signal                                     | Fires when              | Severity |
| ----------------------- | ------------------------------------------ | ----------------------- | -------- |
| `K8upBackupStale`       | KSM job completion, `owner_kind=Backup`    | no success in 36h       | critical |
| `K8upCheckStale`        | KSM job completion, `owner_kind=Check`     | no success in 10d       | warning  |
| `K8upBackupJobFailed`   | `k8up_jobs_failed_counter{jobType=backup}` | any failure in 6h       | critical |
| `K8upScheduleJobFailed` | `k8up_schedule_last_job_succeeded == 0`    | last check/prune failed | warning  |
| `K8upMetricsAbsent`     | `absent(k8up_schedules_gauge)`             | scrape or operator down | warning  |

**Known blind spot:** the two staleness alerts key off retained Job objects.
`successfulJobsHistoryLimit: 1` keeps exactly one successful Job per type, so the series
persists and the alert keeps firing as intended. But a namespace whose Jobs are all
deleted, or whose Schedule is removed outright, produces no series and therefore no
alert. A Schedule made inert by omitting its job blocks (see the demo app) sits in this
state deliberately and stays silent. Detecting a _never-ran_ backup needs the periodic
restore drill, which does not exist yet.

**Not covered by any of this:** whether a restore actually works. `check` runs plain
`restic check` with no `--read-data`, so pack contents are never re-hashed, and no
`Restore` has ever been run against these repositories.
