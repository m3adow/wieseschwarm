---
name: plan-application
description: Plan a new user-facing application for kubernetes/02_applications/apps (the wieseschwarm-applications submodule) before any manifests are written. Walks through every infra primitive available in the cluster (storage, database, backup, metrics, ingress, network policies), asks the storage-replication and domain-exposure questions explicitly, and always includes an in-place VPA and default-deny NetworkPolicies. Produces a written plan via superpowers:writing-plans, not manifests directly.
---

You are helping plan (not yet implement) a new application for `kubernetes/02_applications/apps/` — the private `wieseschwarm-applications` submodule. The goal is a written plan the user approves before any YAML is created, following the same process used for the `demo` app and the existing submodule apps.

## Step 1: Gather the basics

Ask (if not already given):

1. **App name** and container image(s)
2. **What it does** — one sentence
3. **Replica count** — single-replica apps need `strategy: Recreate` if they use an RWO PVC, and a VPA `minReplicas: 1` override (see Step 6)
4. **Config/secrets** it needs beyond a database password

## Step 2: Ask the two required decisions

Do not assume these — ask explicitly:

1. **Replicated storage?** Does the app need a `PersistentVolumeClaim`? The only `StorageClass` today is `piraeus-replicated` (DRBD, `placementCount: 2`, `ReadWriteOnce` only, `WaitForFirstConsumer` — see `kubernetes/01_infrastructure/piraeus/CLAUDE.md`). If the app is stateless, skip storage entirely.
2. **Domain exposure?** Internal-only (`<app>.wieseschwarm.lan` via a LAN `IngressRoute`, default TLSStore) or also public (`<app>.wieseclan.eu.org` via the Cloudflare Tunnel)? Public exposure additionally needs a cert-manager `Certificate` (Let's Encrypt production) and an `external-dns` target annotation, and only works for first-level subdomains — see "Public exposure" in `kubernetes/CLAUDE.md`.

## Step 3: Walk the infra-primitive checklist

Every available infra-application must be considered — decide include/exclude with a stated reason, never skip silently:

| Primitive             | Include when                                 | Manifests                                                                                                                                                                                       | Reference                                                                                    |
| --------------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Piraeus storage       | Step 2.1 = yes                               | `persistentvolumeclaim-<app>.yaml`, `storageClassName: piraeus-replicated`                                                                                                                      | `demo/persistentvolumeclaim-demo.yaml`                                                       |
| MariaDB database      | App needs a SQL DB                           | `database-<app>.yaml`, `user-<app>.yaml`, `grant-<app>.yaml`, `sopssecret-<app>-mariadb-user.yaml`                                                                                              | `mariadb-operator/CLAUDE.md`, `demo/database-demo.yaml`                                      |
| K8up backup           | App has a PVC and/or DB worth restoring      | `sopssecret-<app>-k8up-b2.yaml`, `schedule-<app>-k8up.yaml` with `backup.podSecurityContext` = the app's runtime uid/gid — needs a dedicated B2 bucket + Application Key created manually first | `k8up/CLAUDE.md`                                                                             |
| Metrics/alerts        | Always                                       | `prometheusrule-<app>.yaml`                                                                                                                                                                     | see "Metrics caveat" below, `demo/prometheusrule-demo.yaml`, `demo/servicemonitor-demo.yaml` |
| VPA (in-place)        | Always, no exceptions                        | `verticalpodautoscaler-<app>.yaml`                                                                                                                                                              | see Step 6                                                                                   |
| LAN ingress           | Any HTTP-serving app                         | `ingressroute-<app>-lan.yaml`                                                                                                                                                                   | `demo/ingressroute-demo-lan.yaml`                                                            |
| Public ingress + cert | Only if Step 2.2 = public                    | `certificate-<app>-public.yaml`, `ingressroute-<app>-public.yaml` with the `external-dns.kubernetes.io/target` annotation                                                                       | `demo/ingressroute-demo-public.yaml`, "Public exposure" in `kubernetes/CLAUDE.md`            |
| Reloader              | App reads a ConfigMap/Secret that can change | `configmap.reloader.stakater.com/reload` / `secret.reloader.stakater.com/reload` annotations on the pod template                                                                                | `kubernetes/CLAUDE.md` "Reloader"                                                            |
| Security baseline     | Always                                       | non-root `securityContext`, `hostUsers: false`, `readOnlyRootFilesystem: true` + `emptyDir` mounts for writable paths                                                                           | `kubernetes/CLAUDE.md` "Non-root workloads" and "UserNamespacesSupport" (verified with PVCs) |
| NetworkPolicies       | Always, no exceptions                        | baseline component + one `networkpolicy-allow-<source>.yaml` per proven inbound source                                                                                                          | see Step 4                                                                                   |

### Metrics caveat

Grafana Alloy's `prometheusOperatorObjects` is enabled cluster-wide (`kubernetes/01_infrastructure/grafana-alloy/application-grafana-alloy.yaml`), with no namespace or label filter — it discovers `ServiceMonitor`/`PodMonitor` CRDs from any app. So if this app exposes a Prometheus-format `/metrics` endpoint (e.g. an exporter sidecar), the plan should include a `ServiceMonitor` (or a `PodMonitor` if there's no stable Service to target) alongside its `PrometheusRule` — see "Application metrics scraping" in `kubernetes/CLAUDE.md` for the how-to and the trap to avoid, and `demo/servicemonitor-demo.yaml` for a working reference. "Configure metrics collection if possible" therefore means both: a `ServiceMonitor`/`PodMonitor` to scrape any `/metrics` endpoint the app already exposes, and a `PrometheusRule` against the resulting series plus existing cluster-standard series (`kube_pod_container_status_restarts_total`, `kubelet_volume_stats_used_bytes`, etc. — see `demo/prometheusrule-demo.yaml`).

Note this is distinct from `applicationObservability` (OTLP tracing/APM), which remains disabled cluster-wide and is out of scope — do not confuse the two.

## Step 4: NetworkPolicies — deny by default, grant only what is needed

Policies are enforced (Talos-managed `kube-network-policies`). The model is ingress-only and receiver-owned; the overview is "Network policies" in `kubernetes/CLAUDE.md`, rules and the host-source set are in `kubernetes/01_infrastructure/network-policies/CLAUDE.md`, reference policies in `demo/networkpolicy-*.yaml`. The app namespace starts at default-deny and gets one allow policy per inbound source that the inventory below names. A port without a named consumer gets no rule.

### 4.1 Baseline component

The app's kustomization sets `namespace: <app>` from the scaffold Task on. The NetworkPolicy Task (4.4) adds the baseline (`default-deny-ingress`, `allow-same-namespace`, `allow-own-node`) with `components: [../components/networkpolicy-baseline]`, not the scaffold, so the red run sees an open namespace. That component is a byte-identical copy of `kubernetes/01_infrastructure/network-policies/components/baseline/`; change both together.

Never point at the public component (`../../01_infrastructure/...`) as `demo/` does. It builds locally because the submodule sits inside the public checkout, but ArgoCD clones the private repo alone, where that path does not exist.

### 4.2 Inbound inventory (required table in the plan)

One row per listening container port and source: `Container port | Source | Why | Policy file`. Check every candidate:

| Source                           | Grant when                                                                   | Peer (`from`)                                                                                               |
| -------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Traefik                          | Any IngressRoute, LAN or public (public arrives via cloudflared → Traefik)   | namespace `traefik` + pod `traefik`, see `demo/networkpolicy-allow-traefik.yaml`                            |
| alloy-metrics                    | A ServiceMonitor/PodMonitor scrapes the app; metrics port only               | namespace `monitoring` + pod `alloy-metrics`, see `demo/networkpolicy-allow-alloy-metrics.yaml`             |
| LAN clients                      | A MetalLB LoadBalancer Service exposes the port; LB ports only               | no `from` (`externalTrafficPolicy: Cluster` masquerades client IPs); `networkpolicy-allow-lan-clients.yaml` |
| kube-apiserver, hostNetwork pods | The app serves a webhook or aggregated API, or a hostNetwork client calls it | the host-source `ipBlock` set                                                                               |
| Another app namespace            | A named workload there calls this app                                        | `kubernetes.io/metadata.name` namespace + that workload's pod label                                         |

No rule needed for: kubelet probes (the baseline's `allow-own-node`; the engine does not exempt a probe's teardown packets), same-namespace traffic incl. K8up backup pods and sidecars (baseline), cloudflared (goes through Traefik), cert-manager (DNS-01, no solver pod).

A listener with no consumer (debug, pprof, admin ports) gets a row with Source `none`: disable it in the app config if possible, and give it no Service port and no rule. Default-deny then blocks it from other namespaces.

Each policy file:

- Selects pods with `matchExpressions`, top-level and in peers. The `labels:` transformer rewrites `matchLabels` to the app's own name.
- Selects on labels the pods really carry (`kubectl get pods --show-labels`). An app without the `labels:` transformer has no `app.kubernetes.io/name`, and a selector on it matches nothing.
- Puts `namespaceSelector` and `podSelector` in the **same** `from` element (AND). Two separate list items mean OR.
- Lists container ports, not Service ports (the engine evaluates after DNAT), and always has `ports`.

### 4.3 Outbound inventory (required table, no egress policies)

Egress is unrestricted cluster-wide. The plan still lists every outbound connection (`Destination | Port | Why`) so a later egress pass has the data. Two consequences today:

- Uses MariaDB: add `netpol.wieseschwarm/mariadb-client: "true"` to `namespace.yaml`, or MaxScale drops the connection.
- Fetches user-controlled URLs (link previews, arbitrary webhooks, importers): list it under the plan's risks as SSRF into the LAN and ask the user whether to accept it or add an app-specific egress policy.

### 4.4 Verification Task

The plan has a NetworkPolicy Task after the Task that wires the Application and the first successful ArgoCD sync from the feature branch (the red run needs live listeners), and before the submodule PR merges, so `main` never carries the app without its policies:

1. Rendered selectors: `kubectl kustomize kubernetes/02_applications/apps/<app> | yq 'select(.kind == "NetworkPolicy") | .metadata.namespace + " " + .metadata.name + " " + ([.spec.ingress[]?.from[]?.podSelector // {}] | tojson)'`. Every policy is in `<app>`; no peer shows the app's own name.
2. Probe lines (`EXPECT <service FQDN with trailing dot> <Service port>`) go into the submodule's `kubernetes/02_applications/apps/netpol-expectations.txt`: `DENY` for every port granted only to specific sources, `ALLOW` for LB ports. Never add app lines to the public `scripts/netpol-expectations.txt`.
3. Red, then green: `scripts/netpol-probe.sh <(grep -F '.<app>.svc' kubernetes/02_applications/apps/netpol-expectations.txt)` fails every `DENY` line before the policy commit syncs and passes all lines after.
4. Each granted source still works (URL via Traefik, `up{job="<ServiceMonitor's Service name>"} == 1`, DB connection, LB port), and `scripts/netpol-denials.sh <RFC3339 time the policies synced>` shows no row with `dstPod="<app>/…"` on any node. Denial lines carry only a packet id, and `logs ds/…` reads a single pod, so a plain `grep -i denied` cannot prove this.
5. `scripts/netpol-coverage.sh` passes (the namespace has its `default-deny-ingress`).

## Step 5: Backup prerequisites

If K8up is included, the plan must call out the one-time **manual** prerequisites the user has to do outside git before the `Schedule` can work (see `k8up/CLAUDE.md`):

1. Dedicated B2 bucket for this app
2. Dedicated B2 Application Key scoped to that bucket (never reused across apps)
3. A unique `openssl rand -base64 32` restic repo password

The plan must also state the uid and gid the app's pods run as, because the `Schedule` sets `backup.podSecurityContext` to exactly those. Without it, the backup pod runs as k8up's default uid 65532 and silently skips every file that is not world-readable (see `k8up/CLAUDE.md`).

## Step 6: VPA is mandatory, and in-place

Every application plan includes a `VerticalPodAutoscaler` with `updateMode: "InPlaceOrRecreate"` — never `Off`, `Initial`, or plain `Recreate` in the final plan. This is a hard requirement for this cluster, not a per-app choice. See `kubernetes/01_infrastructure/vpa/CLAUDE.md` for why in-place-first is the standing convention (resizes live; only evicts when in-place resize isn't possible).

If the app runs a single replica, add `minReplicas: 1` to `updatePolicy` — the VPA updater's default `--min-replicas=2` blocks the `Recreate` fallback for single-replica Deployments otherwise (see `demo/verticalpodautoscaler-demo.yaml` and its note in `demo/CLAUDE.md`).

## Step 7: Write the plan document

**REQUIRED SUB-SKILL:** Use superpowers:writing-plans to produce the actual plan document from the decisions above.

This repo already adapts that skill's Task/Step format to Kubernetes manifests instead of code — follow the same shape, using earlier application plans in `docs/superpowers/plans/` as concrete references if they are available:

- Save to `docs/superpowers/plans/YYYY-MM-DD-<app-name>.md`
- One **Task** per resource or tightly-related group of resources (e.g. namespace + kustomization scaffold, PVC, Deployment, VPA, ingress, backup)
- Each Task's "test" step is `kubectl kustomize kubernetes/02_applications/apps/<app>` plus the validate-manifests skill, not unit tests
- Include a Task for wiring into `apps/kustomization.yaml` and the ArgoCD `Application` in `applications/application-<app>.yaml` (feature-branch `targetRevision`), then the NetworkPolicy Task from Step 4.4, then a final Task for committing
- Put the inbound and outbound inventory tables from Step 4 near the top
- Call out any pre-flight steps (e.g. picking a free MetalLB IP, creating the B2 bucket/key) before Task 1

## Step 8: Hand off

Once the plan is approved, implementation follows `kubernetes/02_applications/apps/CLAUDE.md` layout conventions (Application CRDs in `applications/`, workload manifests in `<appname>/`), using `demo/` and the existing submodule apps as reference implementations, and finishes with the `validate-manifests` skill.
