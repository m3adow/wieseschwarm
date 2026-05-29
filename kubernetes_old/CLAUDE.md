# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

This directory (`kubernetes_old/`) is **deprecated**. Active Kubernetes manifests are in the sibling `../kubernetes/` directory. Flux CD syncs from `./kubernetes/` paths, not this one. Do not add new resources here.

## Structure

Kustomize base/overlay pattern throughout:

- `base/` — upstream resource definitions
- `overlays/wieseschwarm/` — cluster-specific patches and additions
- `03_clusters/wieseschwarm/` — Flux `Kustomization` CRs that wire everything together

## Flux CD GitOps

All infrastructure changes take effect only after being committed to `main` and synced by Flux (30s interval). Direct `kubectl apply` is only valid for bootstrapping. Flux Kustomizations declare `dependsOn:` ordering — respect it when adding resources.

## SOPS / Secrets

Encrypted overlays require the `sops-keys` secret to exist in `flux-system` before Flux can decrypt them. Bootstrap order: `00_init` (including secrets bootstrap) must be applied before `01_infrastructure`.

## YAML Conventions

- All documents must start with `---`
- `yamllint` enforces: truthy values as errors, 1 space minimum between content and inline comments, no CRLFs or tabs
- `*-patch.yaml` files are excluded from `k8svalidate` (Talos patches are not valid K8s manifests)
