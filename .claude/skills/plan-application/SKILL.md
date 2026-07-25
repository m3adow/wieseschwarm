---
name: plan-application
description: Plan a new user-facing application for kubernetes/02_applications/apps (the wieseschwarm-applications submodule) before any manifests are written. Walks through every infra primitive available in the cluster (storage, database, backup, metrics, ingress), asks the storage-replication and domain-exposure questions explicitly, and always includes an in-place VPA. Produces a written plan via superpowers:writing-plans, not manifests directly.
---

You are helping plan (not yet implement) a new application for `kubernetes/02_applications/apps/` — the private `wieseschwarm-applications` submodule. The goal is a written plan the user approves before any YAML is created, following the same process used for the `demo`, `rustdesk`, and `wiki.js` apps.

## Step 1: Gather the basics

Ask (if not already given):

1. **App name** and container image(s)
2. **What it does** — one sentence
3. **Replica count** — single-replica apps need `strategy: Recreate` if they use an RWO PVC, and a VPA `minReplicas: 1` override (see Step 5)
4. **Config/secrets** it needs beyond a database password

## Step 2: Ask the two required decisions

Do not assume these — ask explicitly:

1. **Replicated storage?** Does the app need a `PersistentVolumeClaim`? The only `StorageClass` today is `piraeus-replicated` (DRBD, `placementCount: 2`, `ReadWriteOnce` only, `WaitForFirstConsumer` — see `kubernetes/01_infrastructure/piraeus/CLAUDE.md`). If the app is stateless, skip storage entirely.
2. **Domain exposure?** Internal-only (`<app>.wieseschwarm.lan` via a LAN `IngressRoute`, default TLSStore) or also public (`<app>.wieseclan.eu.org` via the Cloudflare Tunnel)? Public exposure additionally needs a cert-manager `Certificate` (Let's Encrypt production) and an `external-dns` target annotation, and only works for first-level subdomains — see "Public exposure" in `kubernetes/CLAUDE.md`.

## Step 3: Walk the infra-primitive checklist

Every available infra-application must be considered — decide include/exclude with a stated reason, never skip silently:

| Primitive             | Include when                                 | Manifests                                                                                                                          | Reference                                                                         |
| --------------------- | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Piraeus storage       | Step 2.1 = yes                               | `persistentvolumeclaim-<app>.yaml`, `storageClassName: piraeus-replicated`                                                         | `demo/persistentvolumeclaim-demo.yaml`                                            |
| MariaDB database      | App needs a SQL DB                           | `database-<app>.yaml`, `user-<app>.yaml`, `grant-<app>.yaml`, `sopssecret-<app>-mariadb-user.yaml`                                 | `mariadb-operator/CLAUDE.md`, `demo/database-demo.yaml`                           |
| K8up backup           | App has a PVC and/or DB worth restoring      | `sopssecret-<app>-k8up-b2.yaml`, `schedule-<app>-k8up.yaml` — needs a dedicated B2 bucket + Application Key created manually first | `k8up/CLAUDE.md`                                                                  |
| Metrics/alerts        | Always                                       | `prometheusrule-<app>.yaml`                                                                                                        | see "Metrics caveat" below, `demo/prometheusrule-demo.yaml`                       |
| VPA (in-place)        | Always, no exceptions                        | `verticalpodautoscaler-<app>.yaml`                                                                                                 | see Step 5                                                                        |
| LAN ingress           | Any HTTP-serving app                         | `ingressroute-<app>-lan.yaml`                                                                                                      | `demo/ingressroute-demo-lan.yaml`                                                 |
| Public ingress + cert | Only if Step 2.2 = public                    | `certificate-<app>-public.yaml`, `ingressroute-<app>-public.yaml` with the `external-dns.alpha.kubernetes.io/target` annotation    | `demo/ingressroute-demo-public.yaml`, "Public exposure" in `kubernetes/CLAUDE.md` |
| Reloader              | App reads a ConfigMap/Secret that can change | `configmap.reloader.stakater.com/reload` / `secret.reloader.stakater.com/reload` annotations on the pod template                   | `kubernetes/CLAUDE.md` "Reloader"                                                 |
| Security baseline     | Always                                       | non-root `securityContext`, `hostUsers: false`, `readOnlyRootFilesystem: true` + `emptyDir` mounts for writable paths              | `kubernetes/CLAUDE.md` "Non-root workloads"                                       |

### Metrics caveat

Grafana Alloy's `applicationObservability` is disabled cluster-wide (`kubernetes/01_infrastructure/grafana-alloy/application-grafana-alloy.yaml`) — only `clusterMetrics` (kube-state-metrics, cAdvisor, node-exporter) is collected. A custom `/metrics` exporter sidecar in the app is **not** scraped today; enabling that is a cluster-wide change out of scope for a single app's plan. "Configure metrics collection if possible" means: write a `PrometheusRule` against existing cluster-standard series (`kube_pod_container_status_restarts_total`, `kubelet_volume_stats_used_bytes`, etc. — see `demo/prometheusrule-demo.yaml`), not adding a new exporter container.

## Step 4: Backup prerequisites

If K8up is included, the plan must call out the one-time **manual** prerequisites the user has to do outside git before the `Schedule` can work (see `k8up/CLAUDE.md`):

1. Dedicated B2 bucket for this app
2. Dedicated B2 Application Key scoped to that bucket (never reused across apps)
3. A unique `openssl rand -base64 32` restic repo password

## Step 5: VPA is mandatory, and in-place

Every application plan includes a `VerticalPodAutoscaler` with `updateMode: "InPlaceOrRecreate"` — never `Off`, `Initial`, or plain `Recreate` in the final plan. This is a hard requirement for this cluster, not a per-app choice. See `kubernetes/01_infrastructure/vpa/CLAUDE.md` for why in-place-first is the standing convention (resizes live; only evicts when in-place resize isn't possible).

If the app runs a single replica, add `minReplicas: 1` to `updatePolicy` — the VPA updater's default `--min-replicas=2` blocks the `Recreate` fallback for single-replica Deployments otherwise (see `demo/verticalpodautoscaler-demo.yaml` and its note in `demo/CLAUDE.md`).

## Step 6: Write the plan document

**REQUIRED SUB-SKILL:** Use superpowers:writing-plans to produce the actual plan document from the decisions above.

This repo already adapts that skill's Task/Step format to Kubernetes manifests instead of code — follow the same shape, using `docs/superpowers/plans/2026-06-11-rustdesk.md` and `docs/superpowers/plans/2026-06-15-wikijs.md` as concrete references:

- Save to `docs/superpowers/plans/YYYY-MM-DD-<app-name>.md`
- One **Task** per resource or tightly-related group of resources (e.g. namespace + kustomization scaffold, PVC, Deployment, VPA, ingress, backup)
- Each Task's "test" step is `kubectl kustomize kubernetes/02_applications/apps/<app>` plus the validate-manifests skill, not unit tests
- Include a final Task for wiring into `apps/kustomization.yaml` and the ArgoCD `Application` in `applications/application-<app>.yaml`, and a final Task for committing
- Call out any pre-flight steps (e.g. picking a free MetalLB IP, creating the B2 bucket/key) before Task 1, as the rustdesk plan does

## Step 7: Hand off

Once the plan is approved, implementation follows `kubernetes/02_applications/apps/CLAUDE.md` layout conventions (Application CRDs in `applications/`, workload manifests in `<appname>/`), using `demo/` and `rustdesk/` as reference implementations, and finishes with the `validate-manifests` skill.
