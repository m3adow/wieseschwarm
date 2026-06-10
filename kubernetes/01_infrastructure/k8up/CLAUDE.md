# k8up

Backup operator using restic. Helm chart Application at wave 2. Namespace: `k8up`.

## Application sync configuration

`application-k8up.yaml` has two non-default settings that deviate from the project defaults:

**`ServerSideApply=true` (syncOption):** k8up installs large CRDs that exceed the 256 KB annotation size limit used by ArgoCD's default client-side apply. Without SSA, ArgoCD fails to apply the CRDs with an annotation overflow error.

**`argocd.argoproj.io/compare-options: ServerSideDiff=true` (annotation):** ArgoCD 2.13 uses its local Kubernetes schema for comparison diffs. Kubernetes 1.33 added `ReplicaSet.status.terminatingReplicas`, which ArgoCD 2.13's bundled schema does not declare. With SSA enabled, this causes every diff check to fail with "field not declared in schema". Setting `ServerSideDiff=true` delegates comparison to the cluster via a dry-run SSA request, so the cluster's own schema is used instead of ArgoCD's local copy.

## Adding backups to an application namespace

Work happens inside the `wieseschwarm-applications` private submodule (`kubernetes/02_applications/apps/`).

Each application namespace uses its own dedicated B2 Application Key and its own restic repo password. Credentials are never shared between namespaces and are not mirrored via Reflector — the `k8up` namespace holds no shared backup credentials.

**Prerequisites (one-time per namespace):**

1. In Backblaze B2, create a dedicated Application Key restricted to that application's backup bucket with capabilities: `readFiles`, `writeFiles`, `deleteFiles`, `listBuckets`, `listFiles`, `readBucketEncryption`. One key per bucket — do not reuse a key across applications.
2. Note the Key ID and Application Key (shown once). The `b2` backend talks to the B2 native API directly — no S3 endpoint is needed.
3. Generate a unique restic repo password: `openssl rand -base64 32`. Store it safely — backups cannot be restored without it. Do not reuse the same password across namespaces.

**Per namespace: create `sopssecret-k8up-b2.yaml`**

Create unencrypted, then run `sops -e -i`:

```yaml
---
apiVersion: isindir.github.com/v1alpha3
kind: SopsSecret
metadata:
  name: k8up-b2-sops
  namespace: <target-namespace>
spec:
  secretTemplates:
    - name: k8up-b2
      stringData:
        repository-password: <restic-repo-encryption-password>
        account-id: <b2-application-key-id>
        account-key: <b2-application-key>
```

**Per namespace: create `schedule.yaml`**

```yaml
---
apiVersion: k8up.io/v1
kind: Schedule
metadata:
  name: k8up-schedule
  namespace: <target-namespace>
spec:
  backend:
    repoPasswordSecretRef:
      name: k8up-b2
      key: repository-password
    # Use the native b2 backend, not s3: restic's S3 backend does not work
    # against B2's S3-compatible API in this setup (verified with the demo
    # app — backups failed until switched to b2).
    b2:
      bucket: <b2-bucket-name>
      accountIDSecretRef:
        name: k8up-b2
        key: account-id
      accountKeySecretRef:
        name: k8up-b2
        key: account-key
  backup:
    schedule: "0 2 * * *"
    failedJobsHistoryLimit: 3
    successfulJobsHistoryLimit: 1
  prune:
    schedule: "0 3 * * 0"
    retention:
      keepLast: 5
      keepDaily: 14
      keepWeekly: 4
  check:
    schedule: "0 4 * * 1"
```

`backup` nightly 02:00, `prune` Sunday 03:00 (enforces retention), `check` Monday 04:00 (repo integrity).

**Add both to the application's `kustomization.yaml`:**

```yaml
resources:
  - sopssecret-k8up-b2.yaml
  - schedule.yaml
```

By default, k8up backs up every PVC in the namespace. To exclude a pod's volumes: annotate the pod with `k8up.io/backup: "false"`.
