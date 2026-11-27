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

## Upgrades

**Images are pinned in git.** `spec.image` on the `MariaDB` and `MaxScale` CRs must stay set. Left
out, the operator's webhook writes its default once at creation and nothing ever bumps it: the
server sat on 11.8.5 and MaxScale on 23.08.5 from June to November 2026, invisible to Renovate.
Pinned, Renovate auto-merges their minor and patch updates, and each merge rolls the pods.

**The data plane follows the operator.** Both charts float on `26.x`, so ArgoCD installs every 26.x
release without a PR. `updateStrategy.autoUpdateDataPlane: true` keeps the Galera pods' `init` and
`agent` containers on the operator's version, so every operator release also rolls the Galera pods
(replicas first, primary last). Without the flag they stayed on 26.3.0 while the operator reached
26.10.2, although the 26.10.0 upgrade guide required the update. Upstream's guides
(`docs/releases/UPGRADE_<version>.md` in `mariadb-operator/mariadb-operator`) can demand other
steps that an unattended upgrade skips; read them first when the operator misbehaves after one.

**Major server versions are manual.** Renovate opens them on `renovate/mariadb-cluster-*` branches,
kept apart from the demo app's `mariadb` client image by a `packageRule`. It proposes the newest
major, which may be a short-lived rolling release (13.0 was EOL after three months); stay on an LTS
line. Set `updateStrategy.mariadbAutoUpgradeEnabled: true` before or together with the image bump,
or the system schema is not migrated and queries on the migrated system tables fail, logical
backups included.

## Stale status after node drains

When a node drain evicts a Galera member, the `MariaDB` CR can stay at `Ready=False`
(`StatefulSetNotReady`) after the member has rejoined. The `MaxScale` CR may also keep listing that
server as `Down`. This was observed twice with operator 26.10.1 during the Talos rollout of
2026-10-18, and it persisted for hours. The database itself was healthy every time.

Before acting on that status, check Galera directly:

```bash
kubectl exec -n mariadb mariadb-cluster-0 -c mariadb -- sh -c \
  'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -e "SHOW STATUS LIKE \"wsrep_%\"" | grep -E "cluster_size|local_state_comment|cluster_status"'
```

If every member reports `Synced`, cluster size 3 and `Primary`, delete the `mariadb-operator` pod.
The new pod recomputes both statuses within seconds. Do this before draining another node, so
the operator never handles Galera recovery from a stale view.

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

**2. Create the user (`User` CR):**

The password Secret must already exist before applying this CR — the operator does not generate it (unlike `MariaDB.spec.rootPasswordSecretKeyRef`). Provide it via a `SopsSecret` labeled `k8s.mariadb.com/watch: ""` so the operator detects password rotations.

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
```

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

The application connects to MaxScale at `mariadb-cluster-maxscale.mariadb.svc.cluster.local.:3306`
using the credentials from the `my-app-mariadb-user` Secret.
