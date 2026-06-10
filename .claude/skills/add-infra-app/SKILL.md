---
name: add-infra-app
description: Add a new infrastructure Application to kubernetes/01_infrastructure/ following repo conventions. Guides through: pattern selection (Helm+config vs. single-app vs. config-only), wave assignment, directory/file creation, root kustomization.yaml update, and optional SopsSecret setup.
---

You are helping add a new infrastructure component to the `kubernetes/01_infrastructure/` directory. Follow these steps in order.

## Step 1: Gather requirements

Ask the user (if not already provided via $ARGUMENTS):

1. **Component name** — e.g., `prometheus`, `external-dns`
2. **Pattern** — which fits best?
   - **Helm + config** (most common): Helm chart installs the operator; separate `config/` Application manages CRs. Use when the chart installs CRDs or an operator that custom resources depend on.
   - **Helm only**: Single Application with inline values. Use for simple components with no custom CRs.
   - **Config only**: Single Application pointing to a `kustomization.yaml` with plain manifests. Use when no Helm chart is involved.
3. **Helm chart details** (if using Helm): repo URL, chart name, version constraint (e.g., `1.x`)
4. **Namespace** — what namespace should the component live in?
5. **Wave** — what sync wave? Refer to `kubernetes/CLAUDE.md` wave table. If this component's CRDs or operator must exist before something else, use wave 1; its config at wave 2. If it depends on cert-manager or MetalLB, use wave 2+.
6. **Secrets needed?** — does this component require any sensitive config (API keys, tokens)? If yes, a `SopsSecret` will be needed.

## Step 2: Create the directory structure

Based on the chosen pattern:

### Helm + config pattern

```
kubernetes/01_infrastructure/<name>/
  application.yaml          # Helm chart Application
  config/
    application.yaml        # Config Application
    kustomization.yaml      # References all config resources
    <resource>.yaml         # CRs, ConfigMaps, etc.
```

### Helm only

```
kubernetes/01_infrastructure/<name>/
  application.yaml          # Helm chart Application with inline values
```

### Config only

```
kubernetes/01_infrastructure/<name>/
  application.yaml          # Application pointing to this directory
  kustomization.yaml        # References all resources
  <resource>.yaml
```

## Step 3: Write the Application manifest(s)

Use these templates. All YAML documents must start with `---`.

### Helm Application template

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <name>
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "<wave>"
spec:
  project: default
  source:
    repoURL: <helm-repo-url>
    chart: <chart-name>
    targetRevision: <version-constraint>
    helm:
      releaseName: <name>
      values: |
        # inline values here
  destination:
    server: https://kubernetes.default.svc
    namespace: <namespace>
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### Config Application template (git source)

```yaml
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <name>-config
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "<wave+1>"
spec:
  project: default
  source:
    repoURL: git@github.com:m3adow/wieseschwarm.git
    targetRevision: main
    path: kubernetes/01_infrastructure/<name>/config
  destination:
    server: https://kubernetes.default.svc
    namespace: <namespace>
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**Important:** when working on a feature branch, set `targetRevision` to that branch for testing and change it back to `main` before merging — see the pre-merge checklist in `kubernetes/CLAUDE.md`.

**ServerSideApply:** do NOT add `- ServerSideApply=true` to `syncOptions` by default — see "ArgoCD syncOptions" in `kubernetes/CLAUDE.md`. Opt in only when the chart installs CRDs too large for client-side apply (annotation size limit), and pair it with the `argocd.argoproj.io/compare-options: ServerSideDiff=true` annotation (see `k8up/CLAUDE.md` for a documented example).

## Step 4: Write the config kustomization.yaml (if using config/)

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - <resource>.yaml
```

## Step 5: Handle secrets (if needed)

If the component requires a secret:

1. Create `kubernetes/01_infrastructure/<name>/config/sopssecret-<name>-<purpose>.yaml`
2. The filename **must** match `sopssecret-*.yaml` — see root CLAUDE.md for why.
3. Template:

```yaml
---
apiVersion: isindir.github.com/v1alpha3
kind: SopsSecret
metadata:
  name: <name>-sops
  namespace: <namespace>
spec:
  secretTemplates:
    - name: <secret-k8s-name>
      stringData:
        <key>: <plaintext-value>
```

4. Encrypt it: `sops --encrypt --in-place kubernetes/01_infrastructure/<name>/config/sopssecret-<name>-<purpose>.yaml`
5. Add it to the config `kustomization.yaml` resources list.

## Step 6: Register in root kustomization.yaml

Add entries to `kubernetes/kustomization.yaml` under `resources:`. Order matters for readability — group by component:

```yaml
# <ComponentName>
- 01_infrastructure/<name>/application.yaml
- 01_infrastructure/<name>/config/application.yaml # if using config pattern
```

## Step 7: Validate

Run the validate-manifests skill or:

```bash
kubectl kustomize --enable-helm kubernetes/ | kubectl apply --dry-run=client -f -
pre-commit run k8svalidate --all-files
```

Check for missing CRDs (common with Helm charts — the CRDs must be installed before config resources can validate).
