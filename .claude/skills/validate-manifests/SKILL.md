---
name: validate-manifests
description: Build and validate kubernetes/ manifests with kustomize, then run k8svalidate checks. Use before pushing changes to kubernetes/ to catch structural errors early.
---

Validate the kubernetes/ manifests in this order:

## Step 1: kustomize build

Run kustomize build for the changed path (or all if no specific path given). Always use `--enable-helm`:

```bash
# For a specific directory:
kubectl kustomize --enable-helm kubernetes/<path>

# For all key entry points:
kubectl kustomize --enable-helm kubernetes/00_bootstrap/argocd
kubectl kustomize --enable-helm kubernetes/01_infrastructure/piraeus
```

Report any build errors before continuing.

## Step 2: yamllint

Run yamllint on all modified YAML files in kubernetes/ using the project's config:

```bash
yamllint -c .github/yamllint.yaml <files>
```

Common issues to watch for:

- Missing `---` document start
- Bare truthy values (`true`/`false`/`yes`/`no`) that should be quoted
- Trailing spaces or wrong indentation

## Step 3: k8svalidate

Run k8svalidate via pre-commit on the modified files (skip `*-patch.yaml` files — they're not valid standalone manifests):

```bash
pre-commit run k8svalidate --files <files>
```

Or run all pre-push hooks at once:

```bash
pre-commit run --hook-stage pre-push --files <files>
```

## Summary

Report:

- Which directories built successfully
- Any yamllint warnings or errors
- k8svalidate results
- Any files skipped (patch files) and why
