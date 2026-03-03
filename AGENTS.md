# AGENTS.md — Wieseschwarm

## Project Intent

Wieseschwarm is a GitOps-managed, production-ready Kubernetes homelab cluster. The goal is full Day 2 operations automation: declarative infrastructure, automatic certificate renewal, encrypted secrets, scheduled backups, and automated dependency updates. Everything in this repo is applied to the cluster via Flux CD — no manual `kubectl apply` beyond the initial bootstrap.

## Technology Stack

| Layer | Tool | Notes |
|---|---|---|
| OS | Talos Linux | Immutable, declarative Kubernetes OS |
| GitOps | Flux CD v2.2.2 | Reconciles every 30s from this repo |
| Ingress | ingress-nginx | DaemonSet, host networking, default class |
| Certificates | cert-manager | LetsEncrypt ACME via Composable ClusterIssuers |
| Storage | OpenEBS Jiva | Replicated block storage, default StorageClass |
| Database | MySQL Operator + InnoDBCluster | Single instance, daily backups to PVC |
| DB Provisioning | db-operator (kloeckner-i) | Manages database connection CRDs |
| Backup | k8up | CRDs and operator for scheduled backups |
| Monitoring | Grafana k8s-monitoring | Prometheus agent → Grafana Cloud |
| VIP/HA | kube-vip v0.6.4 | ARP-based virtual IP for control plane |
| Dynamic Config | Composable Operator v0.3.1 | Injects values from Secrets/ConfigMaps at reconcile |
| Secrets | SOPS + age | Encrypted at rest, decrypted by Flux |
| Dependency Updates | Renovate | Auto-creates PRs for Helm chart/image updates |
| IaC | Kustomize | Pure YAML, no Helm templating in this repo |

## Directory Structure

```
kubernetes/
├── 00_init/          # Phase 0: Flux bootstrap, Helm repo registry
│   ├── base/flux/    # Flux installation + 11 HelmRepository CRDs
│   └── overlays/
│       ├── init-secrets/    # Secret bootstrap (applied manually once)
│       └── wieseschwarm/    # GitRepository + Kustomization pointing at 03_clusters
├── 01_infrastructure/       # Phase 1: Cluster infrastructure
│   ├── base/                # Generic, reusable HelmRelease/config manifests
│   └── overlays/wieseschwarm/  # Cluster-specific values, patches, secrets
└── 03_clusters/
    └── wieseschwarm/        # Root Flux Kustomizations (init.yaml + infrastructure.yaml)

talos/                       # Talos Linux node patches (gitignored where secrets exist)
.github/workflows/           # GitHub Actions: PR validation, commit message checks
```

Phase `02_` (applications) is reserved but not yet present.

## Kustomize Conventions

- **base/**: Generic, cluster-agnostic component definitions. No secrets, no cluster-specific values.
- **overlays/**: Cluster-specific patches, value overrides, and encrypted secret references.
- Every component lives in its own Kubernetes namespace.
- HelmReleases use version constraints (`^x.y.z`, `<x.y.0`) rather than pinning exact versions.
- CRD management: `crds: CreateReplace` on all HelmReleases.
- Prometheus Operator CRDs are installed as a separate HelmRelease (workaround for chart compatibility).

## Secret Management

SOPS with age encryption is used — not Sealed Secrets.

Rules defined in `.sops.yaml`:
- Files matching `*values.yaml` → fully encrypted.
- All other `.yaml` files → only `data:` and `stringData:` fields encrypted.

Never commit plaintext secrets. The `forbid-secrets` pre-commit hook enforces this.

## Bootstrap Procedure

1. Apply Talos patches from `talos/` to nodes (etcd metrics, OpenEBS iSCSI extension + mount).
2. Bootstrap Flux secrets and initial resources:
   ```bash
   make apply   # Runs kubectl kustomize on 00_init/overlays/init-secrets (twice — first may fail on CRD timing)
   ```
3. Flux takes over from here, reconciling `03_clusters/wieseschwarm/` automatically.

## Development Workflow

- **Branch**: work on feature branches, merge to `main` (the Flux source of truth).
- **Pre-commit hooks** (`.pre-commit-config.yaml`) run locally:
  - `detect-private-key` — blocks accidental credential commits
  - `yamllint` — enforces YAML style
  - `forbid-secrets` — validates SOPS encryption
  - `prettier` — code formatting
  - `k8svalidate` — validates manifests against the Kubernetes API (pre-push stage)
- **GitHub Actions** on every PR:
  - Runs all pre-commit + pre-push hooks.
  - Blocks merge if commit messages contain FIXUP or WIP.
- **Renovate** auto-creates PRs for Helm chart and image version bumps.

## Composable Operator Pattern

The Composable Operator (`composable-operator` namespace) enables dynamic value injection at reconcile time. It is used for:
- LetsEncrypt ClusterIssuers — email address injected from a Secret at deploy time rather than hardcoded.
- kube-vip — VIP address injected from a ConfigMap.

When adding new components that need cluster-specific runtime values, prefer this pattern over hardcoding in manifests.

## Component Dependency Order

Flux enforces dependencies via `dependsOn` in Kustomization manifests:

```
init (00_init) → infrastructure (01_infrastructure) → apps (02_, future)
```

Within infrastructure, rough order:
1. cert-manager CRDs + cert-manager
2. OpenEBS Jiva (storage)
3. MySQL Operator → MySQL InnoDBCluster → db-operator
4. ingress-nginx + kube-vip
5. Grafana k8s-monitoring + k8up

## Planned but Not Yet Deployed

- Application layer (`02_clusters/` or `02_apps/`) — Nextcloud, Paperless-NGX, Vaultwarden
- External DNS (HelmRepository is registered, no HelmRelease yet)
- Vertical Pod Autoscaler + Goldilocks for resource tuning
- Reloader for automatic pod restarts on ConfigMap/Secret changes

## What NOT to Do

- Do not run `kubectl apply` manually for infrastructure changes — commit to `main` and let Flux reconcile.
- Do not add Helm chart sources to overlays without first registering a `HelmRepository` in `00_init/base/flux/helm-repositories/`.
- Do not store plaintext secrets in any YAML file.
- Do not skip pre-commit hooks (`--no-verify`) — fix the underlying issue instead.
- Talos machine configs (full config files) must never be committed; only patches go in `talos/`.
