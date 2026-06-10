---
name: argocd-bootstrap
description: Step-by-step ArgoCD bootstrap for the wieseschwarm cluster. Use after a fresh cluster setup or after destroying and recreating ArgoCD. Walks through all Makefile targets in the correct order.
disable-model-invocation: true
---

Walk through the full ArgoCD bootstrap procedure for wieseschwarm. Execute and verify each step before continuing.

## Prerequisites

- Talos cluster is up and kubeconfig is configured (`KUBECONFIG` env var or `~/.kube/config`)
- SSH deploy key exists at `kubernetes/secret/` (gitignored)
- SOPS Age key is available for decryption

## Step 1: Deploy ArgoCD

```bash
make argocd-bootstrap
```

This applies `kubernetes/00_bootstrap/argocd/` via kustomize (upstream ArgoCD v2.13.3 manifest + namespace), then waits for the `argocd-server` deployment to be ready.

Verify:

```bash
kubectl -n argocd get pods
```

All pods should be Running before proceeding.

## Step 2: Configure git repository secret

```bash
make argocd-repo-configure
```

This creates the SSH secret so ArgoCD can pull from `git@github.com:m3adow/wieseschwarm.git`.

Verify:

```bash
kubectl -n argocd get secret argocd-repo-* 2>/dev/null || kubectl -n argocd get cm argocd-cm -o yaml
```

## Step 3: Apply root App of Apps

```bash
make argocd-apps-bootstrap
```

This applies `kubernetes/apps-of-apps.yaml`, which triggers ArgoCD to sync all child Applications.

Verify ArgoCD is reconciling:

```bash
kubectl -n argocd get applications
```

## Step 4: Get admin password

```bash
make argocd-password
```

## Step 5: Verify sync status

Access the ArgoCD UI to confirm all Applications are Synced and Healthy:

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
# Open https://localhost:8080
```

Or via CLI:

```bash
kubectl -n argocd get applications -o wide
```

## Reminder: targetRevision

If bootstrapping from a feature branch, Application resources may have `targetRevision` set to a feature branch. Change these to `main` before merging.
