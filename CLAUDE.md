# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

- `kubernetes/` — active Kustomize manifests (ArgoCD GitOps)
- `kubernetes_old/` — **deprecated** Flux-based structure; do not edit
- `talos/` — Talos OS patches; see `talos/CLAUDE.md` for Talos-specific guidance

## YAML conventions

All YAML documents must start with `---` (yamllint `document-start: required`).

Truthy-like values (`true`, `false`, `yes`, `no`, `on`, `off`) must be quoted when used as strings — yamllint flags bare truthy values as errors.

`*-patch.yaml` files are Kustomize strategic-merge patches, not valid standalone Kubernetes manifests; they are excluded from `k8svalidate`.

## kubernetes/ structure

```
kubernetes/
  00_bootstrap/argocd/   # ArgoCD self-management (upstream manifest + ns patch)
  01_infrastructure/piraeus/  # Piraeus operator + datastore
  apps-of-apps.yaml      # Root Application: syncs all child Applications
  kustomization.yaml     # namespace: argocd
```

Build manifests with `--enable-helm`:

```bash
kubectl kustomize --enable-helm kubernetes/00_bootstrap/argocd
```

## ArgoCD bootstrap procedure

```bash
make argocd-bootstrap       # Deploy ArgoCD, wait for pods
make argocd-repo-configure  # Create SSH deploy key secret
make argocd-apps-bootstrap  # Apply root App of Apps
make argocd-password        # Print initial admin password
```

**Before merging to main**: change `targetRevision` in all Application resources from `feature/2026-rework` to `main`.

## Pre-commit hooks

Two stages — both run in CI (`pre-commit.yaml` workflow):

| Stage        | When      | Includes                                       |
| ------------ | --------- | ---------------------------------------------- |
| `pre-commit` | On commit | prettier, yamllint, detect-secrets, SOPS guard |
| `pre-push`   | On push   | k8svalidate (full K8s manifest validation)     |

Run locally: `pre-commit run --all-files`

## SOPS encryption

`.sops.yaml` controls what gets encrypted. Most manifests encrypt only `data`/`stringData` fields; `values.yaml` and `talos/secret/` files are fully encrypted. Never commit unencrypted secrets — the `forbid-secrets` hook catches most cases.

## Branch and PR conventions

- Branch naming: `feature/<description>`
- Merge strategy: merge commits to `main` (no squash)
- PRs required — no direct pushes to `main`
- GitHub Actions run pre-commit on all files for every PR

## Piraeus schematic

The Talos Image Factory schematic ID in `talos/piraeus-patch.yaml` must be updated when upgrading Talos or changing the DRBD extension version. See `talos/CLAUDE.md` for architecture details.
