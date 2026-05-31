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

| Wave | Current occupants                                  | Purpose                                           |
| ---- | -------------------------------------------------- | ------------------------------------------------- |
| 1    | `cert-manager`, `metallb`                          | Install operators/CRDs first                      |
| 2    | `cert-manager-config`, `metallb-config`, `traefik` | Configure after operators are ready               |
| 3    | `traefik-config`                                   | Finalize (e.g., TLS store depends on certificate) |
| —    | `argocd`, `piraeus`, `sops-secrets-operator`       | No wave; bootstrap or independent                 |

**Rule of thumb:** Helm chart installs at wave N, their Kustomize config at wave N+1. If a resource depends on a CRD installed by wave 1, put it at wave 2 or later.

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

| Component             | Namespace               |
| --------------------- | ----------------------- |
| ArgoCD                | `argocd`                |
| cert-manager          | `cert-manager`          |
| MetalLB               | `metallb-system`        |
| Traefik               | `traefik`               |
| Piraeus               | `piraeus-datastore`     |
| SOPS Secrets Operator | `sops-secrets-operator` |

All Applications use `CreateNamespace=true`; ArgoCD creates namespaces on-demand.

## Secrets (SopsSecret)

Files containing `SopsSecret` CRDs **must** be named `sopssecret-<descriptive-name>.yaml` (e.g. `sopssecret-cloudflare-token.yaml`).

`.sops.yaml` has a dedicated rule matching `sopssecret-.*\.yaml` that encrypts only the `spec` block — the right scope for `SopsSecret`, which stores sensitive data in `spec.secretTemplates[*].{stringData,data}`. Files that do not match this pattern fall through to the generic rule and encrypt only `data`/`stringData`, which **does not cover** `SopsSecret` payloads and will leave secrets unencrypted.

The SOPS operator (in `sops-secrets-operator` namespace) decrypts them at runtime using the age key mounted from secret `sops-age-key`.

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
