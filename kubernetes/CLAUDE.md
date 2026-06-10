# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Structure overview

```
kubernetes/
  00_bootstrap/argocd/        # ArgoCD self-management (upstream manifest + ns patch)
  01_infrastructure/          # All infrastructure operators and their configs
    <component>/
      application.yaml        # Helm chart Application (ArgoCD)
      config/
        application.yaml      # Config Application (ArgoCD), higher wave
        kustomization.yaml
        <resources>.yaml
  apps-of-apps.yaml           # Root Application — syncs everything under kubernetes/
  kustomization.yaml          # Root: lists all child Application manifests
```

## App-of-Apps pattern

`apps-of-apps.yaml` is the single entry point. It tells ArgoCD to sync `kubernetes/` from this repo, which picks up all Applications via `kustomization.yaml`. ArgoCD then reconciles each child Application independently.

**Do not edit `apps-of-apps.yaml`** unless changing the repo URL or global sync policy. New components go into `kustomization.yaml` as resources pointing to their Application manifests.

## Sync-wave ordering

Wave annotations control ArgoCD rollout order within a sync operation:

| Wave | Current occupants                                                                                                                                                                                | Purpose                                                |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| 0    | `mariadb-operator-crds`, `prometheus-operator-crds`                                                                                                                                              | CRD-only installs; wave-1 operators depend on them     |
| 1    | `cert-manager`, `metallb`, `mariadb-operator`, `vpa`                                                                                                                                             | CRD-providing operators; wave-2 config depends on them |
| 2    | `cert-manager-config`, `metallb-config`, `traefik`, `reloader`, `reflector`, `k8up`, `mariadb-operator-config`, `metrics-server`, `grafana-alloy`, `grafana-alloy-rules`, `grafana-alloy-config` | Operators/config that only need core K8s resources     |
| 3    | `traefik-config`, `cloudflared`, `external-dns`                                                                                                                                                  | Finalize + config needing wave-2 resources             |
| 4    | `demo`, `wieseschwarm-apps`                                                                                                                                                                      | User-facing applications; need all infrastructure      |
| —    | `argocd`, `piraeus`, `sops-secrets-operator`                                                                                                                                                     | No wave; bootstrap or independent                      |

**Rule of thumb:** Helm chart installs at wave N, their Kustomize config at wave N+1. If a resource depends on a CRD installed by wave 1, put it at wave 2 or later.

**Wave-1 is reserved for operators whose CRDs are consumed by wave-2 resources.** cert-manager installs the `Certificate` CRD used by `traefik-config`; metallb installs `IPAddressPool`/`L2Advertisement` used by `metallb-config`. Simple operators that install no CRDs (or whose CRDs nothing else depends on) belong at wave 2, not wave 1. Exception: operators may be pre-registered at wave 1 when their CRDs are _anticipated_ to be consumed by future wave-2 config apps — document the rationale in the Application's sync-wave annotation comment.

Set waves via annotation on the Application:

```yaml
annotations:
  argocd.argoproj.io/sync-wave: "2"
```

## Adding a new infrastructure component

There is no single mandatory pattern — use what fits:

- **Helm operator + config app (most common):** Two Applications, separate waves. Use when the component installs CRDs or operators that config resources depend on.
- **Config-only (no Helm):** A single Application pointing to a `kustomization.yaml`. Use for plain manifests with no chart.
- **Single Helm app:** A single Application with inline values. Use for simple components with no custom CRs.

Run `/add-infra-app` for a guided walkthrough.

## ArgoCD syncOptions

Avoid adding `- ServerSideApply=true` to an Application's `syncOptions` unless there is a concrete reason (e.g., the controller or Helm chart requires it to handle large CRDs that exceed the annotation size limit, or the upstream chart explicitly documents it). SSA changes ownership semantics — fields managed by other controllers can be taken over by ArgoCD, causing unexpected conflicts or drift on the next sync. Client-side apply (the default) is sufficient for the vast majority of applications.

## Helm values

All Helm values are currently inlined in Application specs (`spec.sources[].helm.values`), not in separate `values.yaml` files. Keep this pattern unless values are large or need SOPS encryption (SOPS only encrypts `values.yaml` files fully — see root CLAUDE.md).

## Namespace mapping

| Component              | Namespace               |
| ---------------------- | ----------------------- |
| ArgoCD                 | `argocd`                |
| cert-manager           | `cert-manager`          |
| cloudflared            | `cloudflared`           |
| external-dns           | `external-dns`          |
| Grafana Alloy          | `monitoring`            |
| MetalLB                | `metallb-system`        |
| Metrics Server         | `metrics-server`        |
| Traefik                | `traefik`               |
| VPA                    | `vpa`                   |
| Piraeus                | `piraeus-datastore`     |
| SOPS Secrets Operator  | `sops-secrets-operator` |
| Reloader               | `reloader`              |
| Reflector              | `reflector`             |
| k8up                   | `k8up`                  |
| MariaDB Operator       | `mariadb-operator`      |
| MariaDB Galera Cluster | `mariadb`               |

All Applications use `CreateNamespace=true`; ArgoCD creates namespaces on-demand.

## Backups (k8up)

k8up backs up PVCs using restic to Backblaze B2. Schedules are namespace-scoped and live in the `wieseschwarm-applications` submodule alongside their applications.

Each backed-up namespace uses its own dedicated B2 Application Key (scoped to that namespace's bucket) and its own unique restic repo password. Credentials are never shared across namespaces — there is no centralized backup secret in the `k8up` namespace.

Each namespace that contains PVCs to back up needs two files:

- `sopssecret-k8up-b2.yaml` — SopsSecret (encrypted) with the dedicated B2 Application Key ID, Application Key, and restic repo password; decrypts to a Secret named `k8up-b2`
- `schedule.yaml` — k8up Schedule using the native `b2` backend referencing `k8up-b2` (do not use the `s3` backend against B2)

Full YAML templates are in `kubernetes/01_infrastructure/k8up/CLAUDE.md`.

By default, k8up backs up every PVC in the namespace. To exclude a pod's volumes: annotate the pod with `k8up.io/backup: "false"`.

## Cluster utilities

### Reloader

Reloader (Stakater) triggers a rolling restart of Deployments, StatefulSets, and DaemonSets when a referenced ConfigMap or Secret changes. Workloads opt in via annotation on the pod template.

| Annotation                                            | Effect                                            |
| ----------------------------------------------------- | ------------------------------------------------- |
| `reloader.stakater.com/auto: "true"`                  | Restart on any referenced ConfigMap/Secret change |
| `secret.reloader.stakater.com/reload: "my-secret"`    | Restart only when `my-secret` changes             |
| `configmap.reloader.stakater.com/reload: "my-config"` | Restart only when `my-config` changes             |

Annotations go on the Deployment/StatefulSet/DaemonSet under `spec.template.metadata.annotations`, not on the ConfigMap/Secret itself.

### Reflector

Reflector (Emberstack) automatically mirrors Secrets and ConfigMaps from a source namespace to target namespaces. The source resource must be annotated; Reflector creates and keeps the copies in sync.

Required annotations on the source Secret/ConfigMap:

```yaml
reflector.v1.k8s.emberstack.com/reflection-allowed: "true"
reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces: "ns-a,ns-b"
# Leave reflection-allowed-namespaces empty ("") to allow all namespaces.
```

To have Reflector **auto-create** the mirror without a pre-existing target Secret, also add:

```yaml
reflector.v1.k8s.emberstack.com/reflection-auto-enabled: "true"
reflector.v1.k8s.emberstack.com/reflection-auto-namespaces: "ns-a,ns-b"
```

When used inside a `SopsSecret` template, these annotations are stored as plaintext in git (only `stringData`/`data` values are encrypted) and are visible to Reflector after the SOPS operator creates the underlying Secret.

A common use: annotate the wildcard TLS Secret produced by cert-manager so application namespaces can mount the same certificate without duplicating the `Certificate` resource.

## Public exposure (Cloudflare Tunnel + external-dns)

Public internet exposure runs through a Cloudflare Tunnel (`cloudflared`, 2 replicas) to Traefik. Tunnel routing is **git-managed** in the cloudflared ConfigMap: a single wildcard ingress rule (`*.wieseclan.eu.org` → `https://traefik.traefik.svc.cluster.local:443`, `noTLSVerify: true`) routes everything to Traefik, so per-app exposure never touches the tunnel config.

**Caution:** the tunnel runs with a token, but its remote configuration is empty ("Published application routes" in the Zero Trust dashboard shows none), which is why the local config file applies. Never add routes in the dashboard — a non-empty remote configuration takes precedence and the git-managed ConfigMap would be silently ignored (cloudflare/cloudflared#633).

**Single source of truth for the tunnel:**

| Item            | Value                                                   |
| --------------- | ------------------------------------------------------- |
| Tunnel ID       | `0c7f5b3b-6179-4d8a-beff-954e8e87e37c`                  |
| CNAME target    | `0c7f5b3b-6179-4d8a-beff-954e8e87e37c.cfargotunnel.com` |
| Public DNS zone | `wieseclan.eu.org` (Cloudflare, Free plan)              |

**To expose an application publicly**, add an IngressRoute with the external-dns target annotation — nothing else:

```yaml
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: <app>-public
  namespace: <namespace>
  annotations:
    external-dns.alpha.kubernetes.io/target: "0c7f5b3b-6179-4d8a-beff-954e8e87e37c.cfargotunnel.com"
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`<app>.wieseclan.eu.org`)
      kind: Rule
      services:
        - name: <service>
          port: <port>
  tls:
    secretName: <app>-public-tls # cert-manager Certificate, letsencrypt-production issuer
```

external-dns (wave 3, `sources: [traefik-proxy]`) sees the IngressRoute and creates a proxied CNAME for the host pointing at the tunnel. Removing the IngressRoute removes the DNS record (`policy: sync`).

**Constraints:**

- Only first-level subdomains (`<app>.wieseclan.eu.org`): Cloudflare Universal SSL (Free plan) does not cover deeper levels (`a.b.wieseclan.eu.org`).
- Any DNS record pointing at the tunnel reaches Traefik; the IngressRoute set is the real exposure gate (no matching route → 404).
- If the tunnel is ever recreated: update the tunnel token SopsSecret, the table above, and every `external-dns.alpha.kubernetes.io/target` annotation (grep for `cfargotunnel.com`). Leave the new tunnel's dashboard routes empty so the git-managed ConfigMap stays authoritative.
- Reloader restarts the cloudflared pods when the ConfigMap changes; routing edits in git go live on the next ArgoCD sync without manual action.

## Database provisioning (native MariaDB CRDs)

See `kubernetes/01_infrastructure/mariadb-operator/CLAUDE.md` for the full `Database`, `User`,
and `Grant` CR patterns used to provision per-application MariaDB databases.

## Secrets (SopsSecret)

Files containing `SopsSecret` CRDs **must** be named `sopssecret-<descriptive-name>.yaml` (e.g. `sopssecret-cloudflare-token.yaml`).

`.sops.yaml` has a dedicated rule matching `sopssecret-.*\.yaml` that encrypts only `stringData` and `data` keys — the actual secret values within `spec.secretTemplates[*]`. Non-sensitive fields like template names and labels remain readable in git. Files that do not match this pattern fall through to the generic rule, which also encrypts only `data`/`stringData` — sufficient for SopsSecrets since those keys are the same ones that carry the secrets.

The SOPS operator (in `sops-secrets-operator` namespace) decrypts them at runtime using the age key mounted from secret `sops-age-key`.

**NEVER run `sops --decrypt` or any equivalent command to read secret contents.** If a secret file needs to be modified (e.g. adding a field or label), tell the user exactly what change is needed and let them perform the decryption, editing, and re-encryption themselves.

## File naming conventions

All YAML files in `kubernetes/` follow `<kind>-[patch-]<descriptive-name>.yaml`:

| Part                 | Rule                                                                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `<kind>`             | Lowercased Kubernetes `kind` verbatim — `configmap`, `application`, `ingressroute`, `linstorsatelliteconfiguration`, etc. No abbreviation. |
| `-patch-`            | Added after `<kind>` when the file is a Kustomize strategic-merge or JSON6902 patch.                                                       |
| `<descriptive-name>` | Kebab-case; typically matches `metadata.name` of the contained resource.                                                                   |

`kustomization.yaml` is excluded — it is the Kustomize entry point, not a resource file.

The `sopssecret-*` prefix satisfies both this convention and the `.sops.yaml` encryption rule (see Secrets section above).

**Kustomization lists:** `resources` and `patches` lists in `kustomization.yaml` files must be sorted alphabetically within their logical groups. Sort by the filename **without** the `.yaml` extension, so a name that is a prefix of another comes first: `certificate-wildcard.yaml` before `certificate-wildcard-lan.yaml` (plain byte-wise sort of the full filename would order them the other way around, since `-` < `.`). The wave sections in `kubernetes/kustomization.yaml` are logical groups — sort within each group, not across groups.

**ConfigMap data:** Keys in `data:` blocks of `ConfigMap` files must be sorted alphabetically.

## Build commands

```bash
# Build the full kubernetes/ tree (resolves Helm charts)
kubectl kustomize --enable-helm kubernetes/

# Build a specific component
kubectl kustomize --enable-helm kubernetes/01_infrastructure/cert-manager/config

# Validate all manifests (pre-push hook — excludes *-patch.yaml)
pre-commit run k8svalidate --all-files
```

Always use `--enable-helm`; without it, Helm-sourced resources are silently omitted.

## Pre-merge checklist

Before merging to `main`, update every `targetRevision` from the feature branch to `main`:

- `apps-of-apps.yaml`
- All child `application.yaml` files that reference this repo as source

Run `grep -r "targetRevision" kubernetes/` to find all occurrences.
