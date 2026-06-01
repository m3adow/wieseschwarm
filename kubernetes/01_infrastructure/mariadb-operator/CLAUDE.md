# CLAUDE.md — mariadb-operator

Galera HA cluster (3 replicas) with inline MaxScale (2 replicas). Namespace: `mariadb`.
Operator namespace: `mariadb-operator`.

## ArgoCD Applications

| Application               | Wave | Chart                   | Purpose                                          |
| ------------------------- | ---- | ----------------------- | ------------------------------------------------ |
| `mariadb-operator-crds`   | 0    | `mariadb-operator-crds` | CRDs only — must land before the operator starts |
| `mariadb-operator`        | 1    | `mariadb-operator`      | Controller, webhook, cert-controller             |
| `mariadb-operator-config` | 2    | (git path)              | MariaDB CR, Backup CR, SopsSecrets               |

**Why the separate CRD chart?** Starting with v25.x, mariadb-operator ships CRDs in a standalone
`mariadb-operator-crds` chart. The operator chart itself contains no CRDs. If only the operator
chart is installed, the controller crashes on startup because `k8s.mariadb.com/v1alpha1` does not
exist in the API server yet. Wave 0 ensures CRDs are registered before the wave-1 operator pod
starts.

## Database provisioning (native MariaDB CRDs)

Applications request MariaDB databases by deploying three CRs in their own namespace.
All three reference `mariadb-cluster` in the `mariadb` namespace via `spec.mariaDbRef.namespace`.

**1. Create the schema (`Database` CR):**

```yaml
---
apiVersion: k8s.mariadb.com/v1alpha1
kind: Database
metadata:
  name: my-app
  namespace: my-app-namespace
spec:
  mariaDbRef:
    name: mariadb-cluster
    namespace: mariadb
  characterSet: utf8mb4
  collate: utf8mb4_unicode_ci
```

**2. Create the user (`User` CR) — operator generates the password Secret:**

```yaml
---
apiVersion: k8s.mariadb.com/v1alpha1
kind: User
metadata:
  name: my-app
  namespace: my-app-namespace
spec:
  mariaDbRef:
    name: mariadb-cluster
    namespace: mariadb
  passwordSecretKeyRef:
    name: my-app-mariadb-user
    key: password
    generate: true
```

The operator creates a Secret named `my-app-mariadb-user` with key `password` in `my-app-namespace`.

**3. Grant privileges (`Grant` CR):**

```yaml
---
apiVersion: k8s.mariadb.com/v1alpha1
kind: Grant
metadata:
  name: my-app
  namespace: my-app-namespace
spec:
  mariaDbRef:
    name: mariadb-cluster
    namespace: mariadb
  privileges:
    - SELECT
    - INSERT
    - UPDATE
    - DELETE
    - CREATE
    - DROP
    - INDEX
    - ALTER
  database: my-app
  table: "*"
  username: my-app
```

The application connects to MaxScale at `mariadb-cluster-maxscale.mariadb.svc.cluster.local:3306`
using the credentials from the `my-app-mariadb-user` Secret.
