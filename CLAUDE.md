# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

- `kubernetes/` — active Kustomize manifests (ArgoCD GitOps); see `kubernetes/CLAUDE.md`
- `kubernetes/02_applications/apps/` — git submodule pointing to the private `wieseschwarm-applications` repo; contains user-facing application manifests
- `kubernetes_old/` — **deprecated** Flux-based structure; do not edit
- `talos/` — Talos OS patches; see `talos/CLAUDE.md`
- `.claude/` — Claude Code config (skills, agents, hooks, permissions)

The `wieseschwarm-applications` submodule is a **private** GitHub repository. It holds application manifests that must not be public (usernames, internal hostnames, etc.). After cloning this repo, populate the submodule with:

```bash
git submodule update --init
```

### Public repo, private submodule: what must not cross

This repository is public. Nothing that exists only inside the private `apps/` submodule may be
named here — not application names, not internal hostnames, usernames, or LAN topology belonging to
one. That applies to documentation and `.claude/` config exactly as much as to manifests. (The
submodule's _repository_ name is already public via `.gitmodules`, so there is no need to scrub
that; it is the app-level detail that must not leak.)

The trap: the submodule is checked out in your working tree, so its app names sit right there while
you write. Naming one in a `CLAUDE.md` "for concreteness" is the easy mistake, and a reviewer
reading only the diff will not notice.

- **Need a worked example?** Cite `kubernetes/02_applications/demo/`. It is public and exists for
  exactly this purpose.
- **Referring to prior art that happens to live in the submodule?** Describe it without naming it:
  "some manifests in the applications submodule", not "`<appname>` does X".
- **Exempt:** the submodule's own `CLAUDE.md` may name its apps freely, as may anything under
  `docs/superpowers/` — that path is gitignored and never committed.

Check before committing. This derives the names from your local checkout rather than listing them,
since hardcoding them here would be the very leak it looks for:

```bash
names=$(ls -d kubernetes/02_applications/apps/*/ | xargs -n1 basename \
        | grep -vxE 'applications|docs' | paste -sd'|')
git grep -inE "$names"             # content of tracked files
git ls-files | grep -inE "$names"  # and tracked filenames
```

## Path conventions

Never use absolute paths in `.claude/` config files (settings.json, agent files, skills). Use paths relative to the repository root so the repo works regardless of where it is checked out.

## YAML conventions

All YAML documents must start with `---` (yamllint `document-start: required`).

Truthy-like values (`true`, `false`, `yes`, `no`, `on`, `off`) must be quoted when used as strings — yamllint flags bare truthy values as errors.

`*-patch.yaml` files are Kustomize strategic-merge patches, not valid standalone Kubernetes manifests; they are excluded from `k8svalidate`.

## Pre-commit hooks

Two stages — both run in CI (`pre-commit.yaml` workflow):

| Stage        | When      | Includes                                                |
| ------------ | --------- | ------------------------------------------------------- |
| `pre-commit` | On commit | prettier, yamllint, detect-secrets, SOPS guard          |
| `pre-push`   | On push   | k8svalidate (full K8s manifest validation), kube-linter |

Run locally: `pre-commit run --all-files`

kube-linter runs as a `local` hook with a customized `entry` pinning the
`stackrox/kube-linter` docker image (the upstream `kube-linter-docker` hook
hardcodes a stale image tag). Renovate bumps the pin via a regex manager.

## SOPS encryption

`.sops.yaml` controls what gets encrypted. Most manifests encrypt only `data`/`stringData` fields; `values.yaml` and `talos/secret/` files are fully encrypted. Never commit unencrypted secrets — the `forbid-secrets` hook catches most cases.

Requires the Age key at `$SOPS_AGE_KEY_FILE` (default: `~/.config/sops/age/keys.txt`). To mount it in the cluster: `make sops-bootstrap`.

For SopsSecret CRD naming and encryption scope, see `kubernetes/CLAUDE.md`.

## ArgoCD bootstrap procedure

See `kubernetes/CLAUDE.md` for the full bootstrap procedure and pre-merge checklist.

## Branch and PR conventions

- Branch naming: `feature/<description>`
- Merge strategy: merge commits to `main` (no squash)
- PRs required — no direct pushes to `main`
- GitHub Actions run pre-commit on all files for every PR
- Commit messages: plain descriptive, no `feat:`/`fix:` prefixes

## Custom skills and agents

Five skills in `.claude/skills/`:

| Skill                | Purpose                                                                                 |
| -------------------- | --------------------------------------------------------------------------------------- |
| `add-infra-app`      | Guided walkthrough for adding a new infrastructure component to `01_infrastructure/`    |
| `plan-application`   | Guided walkthrough for planning a new user-facing application in `02_applications/apps` |
| `talos-regen-apply`  | Regenerate Talos control plane config and apply to all nodes                            |
| `validate-manifests` | Build manifests with kustomize and run yamllint + k8svalidate                           |
| `argocd-bootstrap`   | Step-by-step ArgoCD bootstrap (user-invocable only)                                     |

Two agents in `.claude/agents/`:

| Agent                | Purpose                                                              |
| -------------------- | -------------------------------------------------------------------- |
| `talos-upgrader`     | Orchestrate safe rolling Talos OS upgrades with pre-flight + dry-run |
| `talos-k8s-upgrader` | Orchestrate safe Kubernetes version upgrades on Talos                |

## Renovate

`renovate.json` auto-bumps Helm chart versions in Application specs. Minor and patch updates (`packageRules` automerge entry) are auto-merged via GitHub's native auto-merge once required status checks pass — this includes Helm chart bumps, so a passing CI run does not guarantee wave-ordering or values compatibility was considered. Major updates still open as regular PRs requiring manual review and merge.

Auto-merge depends on repo-level settings that aren't visible in this tree: `allow_auto_merge` is enabled on the repo, and branch protection on `main` requires the `checks` and `pre-commit` status checks before any merge (including auto-merge).
