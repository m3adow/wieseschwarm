---
name: plan-application
description: Plan a new user-facing application for kubernetes/02_applications/apps (the wieseschwarm-applications submodule) before any manifests are written. Walks through every infra primitive available in the cluster (storage, database, backup, metrics, ingress, network policies), asks the storage-replication and domain-exposure questions explicitly, and always includes an in-place VPA, default-deny NetworkPolicies for ingress and egress, no service account token, and LAN routes restricted to LAN clients. Produces a written plan via superpowers:writing-plans, not manifests directly.
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
2. **Domain exposure?** Internal-only (`<app>.wieseschwarm.lan` via a LAN `IngressRoute`, default TLSStore, restricted to LAN clients by the `lan-clients-only` middleware) or also public (`<app>.wieseclan.eu.org` via the Cloudflare Tunnel)? Public exposure additionally needs a cert-manager `Certificate` (Let's Encrypt production) and an `external-dns` target annotation, and only works for first-level subdomains — see "Public exposure" in `kubernetes/CLAUDE.md`.

## Step 3: Walk the infra-primitive checklist

Every available infra-application must be considered — decide include/exclude with a stated reason, never skip silently:

| Primitive             | Include when                                 | Manifests                                                                                                                                                                                                                               | Reference                                                                                                              |
| --------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Piraeus storage       | Step 2.1 = yes                               | `persistentvolumeclaim-<app>.yaml`, `storageClassName: piraeus-replicated`                                                                                                                                                              | `demo/persistentvolumeclaim-demo.yaml`                                                                                 |
| MariaDB database      | App needs a SQL DB                           | `database-<app>.yaml`, `user-<app>.yaml`, `grant-<app>.yaml`, `sopssecret-<app>-mariadb-user.yaml`                                                                                                                                      | `mariadb-operator/CLAUDE.md`, `demo/database-demo.yaml`                                                                |
| K8up backup           | App has a PVC and/or DB worth restoring      | `sopssecret-<app>-k8up-b2.yaml`, `schedule-<app>-k8up.yaml` with `backup.podSecurityContext` = the app's runtime uid/gid — needs a dedicated B2 bucket + Application Key created manually first                                         | `k8up/CLAUDE.md`                                                                                                       |
| Metrics/alerts        | Always                                       | `prometheusrule-<app>.yaml`                                                                                                                                                                                                             | see "Metrics caveat" below, `demo/prometheusrule-demo.yaml`, `demo/servicemonitor-demo.yaml`                           |
| VPA (in-place)        | Always, no exceptions                        | `verticalpodautoscaler-<app>.yaml`                                                                                                                                                                                                      | see Step 6                                                                                                             |
| LAN ingress           | Any HTTP-serving app                         | `ingressroute-<app>-lan.yaml` referencing the `lan-clients-only` middleware (namespace `traefik`)                                                                                                                                       | `demo/ingressroute-demo-lan.yaml`, "LAN-only routes" in `kubernetes/CLAUDE.md`                                         |
| Public ingress + cert | Only if Step 2.2 = public                    | `certificate-<app>-public.yaml`, `ingressroute-<app>-public.yaml` with the `external-dns.kubernetes.io/target` annotation                                                                                                               | `demo/ingressroute-demo-public.yaml`, "Public exposure" in `kubernetes/CLAUDE.md`                                      |
| Reloader              | App reads a ConfigMap/Secret that can change | `configmap.reloader.stakater.com/reload` / `secret.reloader.stakater.com/reload` annotations on the pod template                                                                                                                        | `kubernetes/CLAUDE.md` "Reloader"                                                                                      |
| Security baseline     | Always                                       | non-root `securityContext`, `hostUsers: false`, `readOnlyRootFilesystem: true` + `emptyDir` mounts for writable paths, `automountServiceAccountToken: false` unless the app calls the Kubernetes API                                    | `kubernetes/CLAUDE.md` "Non-root workloads", "UserNamespacesSupport" (verified with PVCs) and "Service account tokens" |
| NetworkPolicies       | Always, no exceptions                        | both baseline components (ingress, egress) + one `networkpolicy-allow-<source>.yaml` per proven inbound source + one `networkpolicy-allow-egress-<destination>.yaml` per proven outbound destination (internet: the internet component) | see Step 4                                                                                                             |

### Metrics caveat

Grafana Alloy's `prometheusOperatorObjects` is enabled cluster-wide (`kubernetes/01_infrastructure/grafana-alloy/application-grafana-alloy.yaml`), with no namespace or label filter — it discovers `ServiceMonitor`/`PodMonitor` CRDs from any app. So if this app exposes a Prometheus-format `/metrics` endpoint (e.g. an exporter sidecar), the plan should include a `ServiceMonitor` (or a `PodMonitor` if there's no stable Service to target) alongside its `PrometheusRule` — see "Application metrics scraping" in `kubernetes/CLAUDE.md` for the how-to and the trap to avoid, and `demo/servicemonitor-demo.yaml` for a working reference. "Configure metrics collection if possible" therefore means both: a `ServiceMonitor`/`PodMonitor` to scrape any `/metrics` endpoint the app already exposes, and a `PrometheusRule` against the resulting series plus existing cluster-standard series (`kube_pod_container_status_restarts_total`, `kubelet_volume_stats_used_bytes`, etc. — see `demo/prometheusrule-demo.yaml`).

Note this is distinct from `applicationObservability` (OTLP tracing/APM), which remains disabled cluster-wide and is out of scope — do not confuse the two.

## Step 4: NetworkPolicies — deny by default, grant only what is needed

Policies are enforced (Talos-managed `kube-network-policies`). Ingress is receiver-owned, and app namespaces also deny egress; the overview is "Network policies" in `kubernetes/CLAUDE.md` (with "Egress (app namespaces only)"), rules and the host-source set are in `kubernetes/01_infrastructure/network-policies/CLAUDE.md`, reference policies in `demo/networkpolicy-*.yaml`. The app namespace starts at default-deny in both directions and gets one allow policy per inbound source and per outbound destination that the inventories below name. A port or destination without a named reason gets no rule.

### 4.1 Baseline components

The app's kustomization sets `namespace: <app>` from the scaffold Task on. The NetworkPolicy Task (4.4) adds both baselines, not the scaffold, so the red runs see an open namespace:

```yaml
components:
  - ../components/networkpolicy-baseline
  - ../components/networkpolicy-egress-baseline
```

- `networkpolicy-baseline`: `default-deny-ingress`, `allow-same-namespace`, `allow-own-node`.
- `networkpolicy-egress-baseline`: `default-deny-egress`, DNS to CoreDNS, the own namespace, and k8up job pods to the API server and to any public address on TCP 443 (for B2).

Both are byte-identical copies of `kubernetes/01_infrastructure/network-policies/components/baseline/` and `.../egress-baseline/`; change each pair together. `components/networkpolicy-egress-internet` (4.3) exists only in the submodule.

Never point at the public components (`../../01_infrastructure/...`) as `demo/` does. It builds locally because the submodule sits inside the public checkout, but ArgoCD clones the private repo alone, where that path does not exist.

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

### 4.3 Outbound inventory (required table) and egress policies

The egress baseline already allows DNS, the own namespace and the k8up job pods. Every other outbound connection gets a row (`Destination | Port | Why | Policy file`) and is either granted or knowingly blocked. Fill the table from the app's documentation (update checks, telemetry, mail, external login providers, webhooks, storage backends), then confirm it with the inventory run in 4.4. Features that call out only on demand (mail, external login, imports) rarely show up in an inventory window, so ask the user about each one the app has. A destination the app can live without, such as an update check or telemetry, is the user's call: block or allow.

Each egress policy carries the label `netpol.wieseschwarm/direction: egress` and `policyTypes: [Egress]`, follows the selector rules of 4.2 (with `to` in place of `from`), and always has `ports`.

| Destination           | Grant with                                                                                                                                                                                                                                                                                                 |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MariaDB               | `networkpolicy-allow-egress-mariadb.yaml` as in `demo/` (namespace `mariadb` + pod `maxscale`, TCP 3306). Separately, `netpol.wieseschwarm/mariadb-client: "true"` goes into `namespace.yaml` from the scaffold Task on: MaxScale's ingress rule needs it for the first sync, whatever the egress policies |
| Internet, TCP 80/443  | `../components/networkpolicy-egress-internet` in `components:`, and `netpol.wieseschwarm/internet: "true"` in each pod template that needs it                                                                                                                                                              |
| Internet, other ports | the same, plus the patch below                                                                                                                                                                                                                                                                             |
| Another namespace     | one policy with `namespaceSelector` + `podSelector` in the same `to` element; that namespace needs a matching ingress rule                                                                                                                                                                                 |
| A LAN host            | one policy with an `ipBlock` `/32` and its port (the internet component excludes `192.168.0.0/16`)                                                                                                                                                                                                         |

More internet ports go in the app's kustomization:

```yaml
patches:
  - target:
      kind: NetworkPolicy
      name: allow-egress-internet
    patch: |-
      - op: add
        path: /spec/egress/0/ports/-
        value:
          protocol: TCP
          port: 587
```

Opt pods in with the label. The component selects on it instead of shipping `podSelector: {}` for apps to narrow by patch, because a patch whose target matches nothing still builds, and `{}` would then put every pod in the namespace online. A missing label or a missed port patch only cuts traffic off, and the egress probe (4.4) shows it.

An app that fetches user-controlled URLs (link previews, webhooks, importers) and gets the internet component can still be pointed at any internet host, though not at the LAN or the cluster. List it under the plan's risks.

### 4.4 Verification Task

The plan has a NetworkPolicy Task after the Task that wires the Application and the first successful ArgoCD sync from the feature branch (the red runs need live listeners and a running pod), and before the submodule PR merges, so `main` never carries the app without its policies:

1. Egress inventory, before any policy exists: `TALOSCONFIG=<path> scripts/netpol-egress-inventory.sh '^<app>$' <minutes> <outfile>` while exercising every feature, with one pod restart in the window (start-up checks run then). Every destination outside the namespace must match a 4.3 row; settle the rest with the user.
2. Rendered selectors: `kubectl kustomize kubernetes/02_applications/apps/<app> | yq 'select(.kind == "NetworkPolicy") | .metadata.namespace + " " + .metadata.name + " " + ([.spec.ingress[]?.from[]?.podSelector, .spec.egress[]?.to[]?.podSelector] | map(select(. != null)) | tojson)'`. Every policy is in `<app>`; no peer shows the app's own name.
3. Probe lines:
   - Ingress (`EXPECT <service FQDN with trailing dot> <Service port>`) go into the submodule's `kubernetes/02_applications/apps/netpol-expectations.txt`: `DENY` for every port granted only to specific sources, `ALLOW` for LB ports. Never add app lines to the public `scripts/netpol-expectations.txt`.
   - Egress go into `kubernetes/02_applications/apps/<app>/netpol-egress-expectations.txt`. Start from `demo/netpol-egress-expectations.txt` (its header line has the probe command; adjust namespace and pod selector). Add an `ALLOW` per granted destination: with the internet component `ALLOW github.com 443` replaces `DENY github.com 443`, plus `ALLOW` for each patched port and `DENY github.com 22` to prove the port limit.
4. Red, then green: before the policy commit syncs, `scripts/netpol-probe.sh <(grep -F '.<app>.svc' kubernetes/02_applications/apps/netpol-expectations.txt)` and `scripts/netpol-egress-probe.sh <app> <pod-selector> kubernetes/02_applications/apps/<app>/netpol-egress-expectations.txt` fail every `DENY` line; after it, both pass all lines.
5. Each granted source and destination still works (URL via Traefik, `up{job="<ServiceMonitor's Service name>"} == 1`, DB connection, LB port, mail, external API), and `scripts/netpol-denials.sh <RFC3339 time after the last green probe run>` shows no row with `<app>/…` on any node, in either direction. The probes' `DENY` lines are dropped on purpose, so start the window after them and exercise the app inside it. Denial lines carry only a packet id, and `logs ds/…` reads a single pod, so a plain `grep -i denied` cannot prove this.
6. `scripts/netpol-coverage.sh` passes (the namespace has `default-deny-ingress` and `default-deny-egress`).
7. No token: `kubectl -n <app> get pods -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.volumes[*].name}{"\n"}{end}'` shows no `kube-api-access-*` volume on the app's pods (k8up job pods keep theirs).
8. LAN-only route: the URL works from a LAN client and returns 403 from a pod. Run the pod check in a scratch namespace without policies, read the result from the logs, and delete the namespace in the same run. A bare pod older than 10 minutes fires `KubePodWithoutController`, and `kubectl run --rm -i` can attach late and lose the output:

   ```bash
   NS=netpol-lan-check H=<app>.wieseschwarm.lan
   kubectl create ns $NS
   kubectl -n $NS run curl --restart=Never --image=curlimages/curl:8.22.0 --command -- \
     curl -sk -o /dev/null -w '%{http_code}\n' --resolve $H:443:192.168.10.40 https://$H/
   kubectl -n $NS wait --for=jsonpath='{.status.phase}'=Succeeded pod/curl --timeout=120s
   kubectl -n $NS logs curl   # expect 403
   kubectl delete ns $NS
   ```

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
