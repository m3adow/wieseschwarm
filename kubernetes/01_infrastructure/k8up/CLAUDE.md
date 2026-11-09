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

**Per namespace: create `sopssecret-<app>-k8up-b2.yaml`**

Create unencrypted, then run `sops -e -i`:

```yaml
---
apiVersion: isindir.github.com/v1alpha3
kind: SopsSecret
metadata:
  name: <app>-k8up-b2-sops
  namespace: <target-namespace>
spec:
  secretTemplates:
    - name: k8up-b2
      stringData:
        repository-password: <restic-repo-encryption-password>
        account-id: <b2-application-key-id>
        account-key: <b2-application-key>
```

**Per namespace: create `schedule-<app>-k8up.yaml`**

```yaml
---
apiVersion: k8up.io/v1
kind: Schedule
metadata:
  name: <app>-k8up-schedule
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
    # Ends a hung restic run; why 25 min: kubernetes/01_infrastructure/k8up/CLAUDE.md
    activeDeadlineSeconds: 1500
    failedJobsHistoryLimit: 3
    successfulJobsHistoryLimit: 1
    # Read as the app's own uid: k8up's default uid 65532 skips files that are not world-readable.
    podSecurityContext:
      runAsUser: <app-uid>
      runAsGroup: <app-gid>
  prune:
    schedule: "0 3 * * 0"
    activeDeadlineSeconds: 1500
    retention:
      keepLast: 5
      keepDaily: 14
      keepWeekly: 4
  check:
    schedule: "0 4 * * 1"
    activeDeadlineSeconds: 1500
```

`backup` nightly 02:00, `prune` Sunday 03:00 (enforces retention), `check` Monday 04:00 (repo integrity).

**`backup.podSecurityContext` is required.** Set `runAsUser` and `runAsGroup` to the uid and gid
the application's pods run as (their `securityContext.runAsUser`/`runAsGroup`). Without it, the
backup pod runs as the k8up image's default uid 65532 with no supplementary groups, and restic
cannot read any file that is not world-readable. It logs one `ERROR … during archival` line per
skipped file and ends with `backup finished … "errors": N`, but the Job still completes, so no
alert fires. One application's nightly backup skipped its config file this way until 2026-10-15.

Running as the owner reads every file the owner can read, whatever its group and other bits, and
changes nothing on the volume.
The alternatives are worse:

- `supplementalGroups: [<app-gid>]` only works while the application keeps group-read bits; a
  file it rewrites as mode 600 is skipped again.
- `fsGroup` on the backup pod makes the kubelet re-group the whole volume before every backup.

An application whose pods write as more than one uid needs a backup identity that can read all of
them; check the `backup finished` line for `"errors": 0` after the first run. Set the same
`podSecurityContext` on any one-off `Backup`. A `Restore` gets the same `runAsUser`/`runAsGroup`, so
restored files come back owned by the application, **plus `fsGroup` set to the application's gid**
whenever it restores into a fresh volume. A new volume's root directory belongs to root, so a
restore pod running as the application's uid cannot create anything in it: restic logs
`mkdir … permission denied` for every path and ends with `Fatal: There were N errors`, yet the
`Restore` still reports `Completed=True` (verified 2026-10-15). Check the job log's
`Summary: Restored N files/dirs` line, never only the status. Use the full resource names
`backups.k8up.io` and `restores.k8up.io` with `kubectl`: the short name `backups` resolves to
mariadb-operator's `Backup` type on this cluster.

**`activeDeadlineSeconds: 1500` is required on `backup`, `prune` and `check`.** Without it a hung
restic run never ends. restic 0.19.1, which k8up v2.16.1 ships, reaches B2 through blazer v0.7.2:
any connection error on an upload counts as retryable, and blazer retries the same upload URL
after one second, without limit ([Backblaze/blazer#31](https://github.com/Backblaze/blazer/issues/31)).
Lock refreshes still succeed, so restic never gives up, and nothing else in the chain has a
timeout. On 2026-11-09 one application's database-stream backup hung like this for 12 h 45 min:
stream fully read, no open socket, lock fresh. k8up starts a `check` or `prune` only while no other
job on the repository is active, so that week's `check` waited behind it, and the only signal was
`KubeJobNotCompleted` after 12 h. After `kill -QUIT` on restic, k8up's retry finished in 22 s.

When the deadline passes, the Job controller stops the run and marks the Job `Failed`
(`DeadlineExceeded`). k8up counts that as a failed job: a backup raises `K8upBackupJobFailed`
(critical) about 5 minutes later, a check or prune `K8upScheduleJobFailed`, and `KubeJobFailed`
stays until the failed Job is deleted. Nothing needs repair; the next scheduled run retries.

**Why 25 minutes and not more.** A killed run leaves its repository lock behind: the kubelet
signals k8up, which then kills restic outright (`exec.CommandContext`), so restic never unlocks.
restic treats a lock as stale only 30 minutes after its last refresh, k8up's `restic unlock`
before each job removes only stale locks, and k8up never passes `--retry-lock`. The Sunday `prune`
starts one hour after the backup, so a backup deadline over 30 minutes would hand it a fresh lock
and fail it as well; 25 minutes leaves the lock at least 35 minutes old. In general, keep a job's
deadline at least 30 minutes shorter than the gap to its Schedule's next job. 25 minutes is about
30 times the slowest run seen (apart from the hung one, every job from 2026-10-18 to 2026-11-09
took 19 to 46 s), but it caps a full upload: 1 GiB needs about 6 Mbit/s upstream. An application
whose first backup or a prune moves more data needs a longer deadline and a wider gap between its
jobs.

Keep the deadline once a restic with blazer v0.9.0 or later ships (restic's master branch has it,
no release as of 2026-11-09). It bounds any other hang as well.

**Why not switch to the `s3` backend.** restic's docs recommend B2's S3-compatible API "due to
issues with error handling in the current B2 library". Two things stand in the way here: the demo
failure behind the comment in the template was never diagnosed, and S3 deletes only hide files on
B2. restic's S3 backend deletes without a version ID, B2 turns that into a delete marker, and the
hidden versions stay billed. With every bucket on B2's default lifecycle "Keep all versions"
(checked 2026-11-09), `prune` would free nothing. Switching needs each bucket set to "Keep only
the last version" first, which is safe because restic writes every file only once, plus a fresh
test with the demo app. The native backend does delete: it calls `b2_delete_file_version` until
no version is left, and falls back to hiding only when the key lacks `deleteFiles` (every key has
it, checked 2026-11-09).

**Add both to the application's `kustomization.yaml`:**

```yaml
resources:
  - schedule-<app>-k8up.yaml
  - sopssecret-<app>-k8up-b2.yaml
```

These are the file and object names every application here uses (`demo/schedule-demo-k8up.yaml`,
`demo/sopssecret-demo-k8up-b2.yaml`), following the repo's `<kind>-<descriptive-name>.yaml`
convention. The Secret the SopsSecret produces stays plain `k8up-b2`, which the Schedule references.

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

### Why the ServiceMonitor sets `honorLabels: true`

k8up's operator runs in `k8up` but emits metrics _about_ other namespaces, tagging each
series with the namespace it backs up. A ServiceMonitor scrape attaches the target's own
`namespace` label, and with the `honor_labels: false` default Prometheus keeps that and
renames k8up's to `exported_namespace` — standard behaviour, but it makes
`{{ $labels.namespace }}` in an alert render `k8up` instead of the namespace at risk.

`honorLabels: true` lets k8up's label win, so `namespace` means "namespace being backed
up" for both the k8up-derived and the kube-state-metrics-derived alerts in
`prometheusrule-k8up.yaml`. k8up exposes only `namespace`, `jobType`, and `schedule`, so
`namespace` is the sole collision — `job` and `instance` are unaffected.

Note the neighbouring kube-state-metrics alerts need no such setting: the k8s-monitoring
chart scrapes KSM through a bespoke job that attaches no target `namespace` at all, so
nothing collides there. Only the ServiceMonitor path adds those labels.

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

| Alert                    | Signal                                     | Fires when               | Severity |
| ------------------------ | ------------------------------------------ | ------------------------ | -------- |
| `K8upBackupStale`        | KSM job completion, `owner_kind=Backup`    | no success in 36h        | critical |
| `K8upCheckStale`         | KSM job completion, `owner_kind=Check`     | no success in 10d        | warning  |
| `K8upBackupJobFailed`    | `k8up_jobs_failed_counter{jobType=backup}` | any failure in 6h        | critical |
| `K8upScheduleJobFailed`  | `k8up_schedule_last_job_succeeded == 0`    | last check/prune failed  | warning  |
| `K8upMetricsAbsent`      | `absent(k8up_schedules_gauge)`             | scrape or operator down  | warning  |
| `K8upBackupSkippedFiles` | LogQL on the backup job log (Loki ruler)   | latest backup errors > 0 | warning  |

**Known blind spot:** the two staleness alerts key off retained Job objects.
`successfulJobsHistoryLimit: 1` keeps exactly one successful Job per type, so the series
persists and the alert keeps firing as intended. But a namespace whose Jobs are all
deleted, or whose Schedule is removed outright, produces no series and therefore no
alert. A Schedule made inert by omitting its job blocks (see the demo app) sits in this
state deliberately and stays silent. Detecting a _never-ran_ backup needs the periodic
restore drill, which does not exist yet.

**Skipped files are a log-only signal.** restic's per-run error count never becomes a
metric: k8up pushes its restic statistics to a Pushgateway that does not exist here
(`prometheus send failed … 127.0.0.1:80` in every backup log). `K8upBackupSkippedFiles` is
therefore a LogQL rule (`grafana-alloy/cluster-rules/prometheusrule-k8up-logs.yaml`,
evaluated by the Loki ruler, see `grafana-alloy/CLAUDE.md`). It reads the backup pod's
`backup finished … "errors": N` line, keeps the latest value per namespace within 26 h, and
fires while it is above 0; it resolves after the next clean backup. It depends on that exact
log wording: check it whenever Renovate bumps k8up. A backup that did not run at all logs
nothing, which `K8upBackupStale` covers.

**Not covered by any of this:** whether a restore actually works. `check` runs plain
`restic check` with no `--read-data`, so pack contents are never re-hashed. One manual restore
test has been run (2026-10-15, one application): a fresh snapshot of the volume and of an
application-aware database dump, restored into a scratch volume, passed the application's own
integrity checks. There is still no periodic restore drill.
