# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

- `kubernetes/` — active Kustomize manifests (ArgoCD GitOps); see `kubernetes/CLAUDE.md`
- `kubernetes_old/` — **deprecated** Flux-based structure; do not edit
- `talos/` — Talos OS patches; see `talos/CLAUDE.md`
- `.claude/` — Claude Code config (skills, agents, hooks, permissions)

## Path conventions

Never use absolute paths in `.claude/` config files (settings.json, agent files, skills). Use paths relative to the repository root so the repo works regardless of where it is checked out.

## YAML conventions

All YAML documents must start with `---` (yamllint `document-start: required`).

Truthy-like values (`true`, `false`, `yes`, `no`, `on`, `off`) must be quoted when used as strings — yamllint flags bare truthy values as errors.

`*-patch.yaml` files are Kustomize strategic-merge patches, not valid standalone Kubernetes manifests; they are excluded from `k8svalidate`.

## Pre-commit hooks

Two stages — both run in CI (`pre-commit.yaml` workflow):

| Stage        | When      | Includes                                       |
| ------------ | --------- | ---------------------------------------------- |
| `pre-commit` | On commit | prettier, yamllint, detect-secrets, SOPS guard |
| `pre-push`   | On push   | k8svalidate (full K8s manifest validation)     |

Run locally: `pre-commit run --all-files`

## SOPS encryption

`.sops.yaml` controls what gets encrypted. Most manifests encrypt only `data`/`stringData` fields; `values.yaml` and `talos/secret/` files are fully encrypted. Never commit unencrypted secrets — the `forbid-secrets` hook catches most cases.

Requires the Age key at `$SOPS_AGE_KEY_FILE` (default: `~/.config/sops/age/keys.txt`). To mount it in the cluster: `make sops-bootstrap`.

For SopsSecret CRD naming and encryption scope, see `kubernetes/CLAUDE.md`.

## ArgoCD bootstrap procedure

```bash
make argocd-bootstrap       # Deploy ArgoCD, wait for pods
make argocd-repo-configure  # Create SSH deploy key secret
make argocd-apps-bootstrap  # Apply root App of Apps
make argocd-password        # Print initial admin password
```

**Before merging to main**: change `targetRevision` in all Application resources from the feature branch to `main`. Run `grep -r "targetRevision" kubernetes/` to find all occurrences.

## Branch and PR conventions

- Branch naming: `feature/<description>`
- Merge strategy: merge commits to `main` (no squash)
- PRs required — no direct pushes to `main`
- GitHub Actions run pre-commit on all files for every PR
- Commit messages: plain descriptive, no `feat:`/`fix:` prefixes

## Custom skills and agents

Four skills in `.claude/skills/`:

| Skill                | Purpose                                                                              |
| -------------------- | ------------------------------------------------------------------------------------ |
| `add-infra-app`      | Guided walkthrough for adding a new infrastructure component to `01_infrastructure/` |
| `talos-regen-apply`  | Regenerate Talos control plane config and apply to all nodes                         |
| `validate-manifests` | Build manifests with kustomize and run yamllint + k8svalidate                        |
| `argocd-bootstrap`   | Step-by-step ArgoCD bootstrap (user-invocable only)                                  |

Two agents in `.claude/agents/`:

| Agent                | Purpose                                                              |
| -------------------- | -------------------------------------------------------------------- |
| `talos-upgrader`     | Orchestrate safe rolling Talos OS upgrades with pre-flight + dry-run |
| `talos-k8s-upgrader` | Orchestrate safe Kubernetes version upgrades on Talos                |

## Renovate

`renovate.json` auto-bumps Helm chart versions in Application specs. Review Renovate PRs before merging — chart upgrades may require wave-ordering or values changes.
