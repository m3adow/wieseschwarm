---
name: csi-rclone-upgrade
description: Use when a Renovate PR bumps the csi-driver-rclone chart, or when asked to upgrade, roll back or deliberately restart the rclone CSI driver (namespace csi-rclone) in this cluster.
---

# Upgrading the rclone CSI driver

Every chart bump replaces the driver pod on all three nodes, and each replacement kills the
mounts of the consumers on that node while their pods keep showing Ready. An upgrade is therefore
a supervised sequence with the user present, never a merge and walk away.

The facts and their reasons live in `kubernetes/01_infrastructure/csi-rclone/CLAUDE.md` (below:
the runbook). Read it before step 1; this skill only fixes the order.

**The user merges the PR. You never merge it and never enable auto-merge.** A patch release and
green CI change nothing: CI exercises none of this. If the user has no 30 minutes now, the PR
waits; nothing breaks while it is open.

## Sequence

### 1. Versions

```bash
A=kubernetes/01_infrastructure/csi-rclone/application-csi-rclone.yaml
yq '.spec.sources[0].targetRevision' $A
gh pr diff <pr>
```

The first prints the running chart version. The PR's diff shows the target; expect exactly that
one line changed.

### 2. Release notes

Read every release between the two versions, and the code that changed:

```bash
gh release view v<new> -R veloxpack/csi-driver-rclone
gh api repos/veloxpack/csi-driver-rclone/compare/v<old>...v<new> \
  --jq '.files[].filename | select(startswith("vendor/") | not)'
```

A release that touches any of these is a finding. Finish steps 3 and 4, then put it into the
step 5 summary together with the rule it affects; the upgrade goes no further until the user has
decided on that finding explicitly. Each one carries a rule of the runbook:

| Change in                                               | What depends on it here                            |
| ------------------------------------------------------- | -------------------------------------------------- |
| `DeleteVolume`, reclaim behaviour (upstream #64)        | `Retain` protects the data; say so anyway          |
| Recovery or remount after a driver restart              | This whole sequence                                |
| `fsGroup`, volume mount group                           | The `fsGroup` equals `runAsUser` rule              |
| Cache directory handling                                | The `cache-dir` mount option                       |
| `mount2`, `allow_idmap`, the bundled rclone version     | Consumers with `hostUsers: false`                  |
| How remotes from `configData` are stored                | The remote naming rule                             |
| Metric names                                            | The ServiceMonitor's keep-list and three alerts    |
| Chart values the Application sets, CSIDriver spec, RBAC | The Application itself; the CSIDriver is immutable |

Then compare with the runbook's "Upstream" list. If a release fixes one of those points, tell the
user; removing the workaround is a separate change, not part of the upgrade.

### 3. Rendered diff

```bash
for v in <old> <new>; do
  helm template csi-rclone oci://ghcr.io/veloxpack/charts/csi-driver-rclone --version $v \
    -n csi-rclone -f <(yq '.spec.sources[0].helm.values' $A) > <scratch>/$v.yaml
done
diff -u <scratch>/<old>.yaml <scratch>/<new>.yaml
```

Expected: image tags and version labels only. Anything else is a finding for step 5.

### 4. Pre-flight

All of these hold, or the upgrade waits:

```bash
kubectl -n argocd get application csi-rclone
kubectl get nodes
kubectl -n csi-rclone get ds csi-rclone-node
kubectl get sc rclone -o jsonpath='{.reclaimPolicy}{"\n"}'
for p in $(kubectl -n csi-rclone get pods -l app=csi-rclone-node -o name); do
  kubectl get --raw "/api/v1/namespaces/csi-rclone/pods/${p#pod/}:5572/proxy/metrics" \
    | grep -E '^(csi_driver_vfs_uploads_queued_total|rclone_http_status_code\{code="4(01|29)")'
done
```

Expected, in this order: `Synced` and `Healthy`; three nodes `Ready`, and the user confirms that
no Talos or Kubernetes upgrade is running; 3 driver pods ready; `Retain`; every
`uploads_queued` value 0. A driver pod without mounts prints nothing, and an error from
`kubectl get --raw` means that pod's metrics endpoint is down.

A 401 or 429 line is a counter over that driver pod's whole life, so one old refusal keeps it
there. It blocks the upgrade only if it grows when the loop runs again a minute later.

Then save the consumer list with the listing command from the runbook ("Driver restarts and
upgrades") into a scratch file: pods and their nodes, as they are before the rollout.

### 5. Go or no-go

Give the user a short summary: versions, findings of steps 2 and 3, pre-flight result, and the
pods you will delete, each of which is down for as long as its restart takes. Wait for an explicit
go. The user merges the PR and says so.

### 6. Rollout

Argo CD picks the merge up within a few minutes. Until then `rollout status` reports the old
revision as complete, so wait for the new image first:

```bash
kubectl -n csi-rclone get ds csi-rclone-node \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="rclone")].image}{"\n"}'
kubectl -n csi-rclone rollout status ds/csi-rclone-node --timeout=10m
```

A rollout that stalls goes to step 9.

### 7. Consumers

Only after the rollout is complete on all three nodes: run the listing command again and delete
every pod it shows, then wait until each replacement is Ready. The fresh listing also catches a
consumer that was replaced since step 4; the saved list is kept for the node comparison in step 8.
All at once is fine; short interruptions are accepted here.

### 8. Verify

- Each consumer lists its folder: `kubectl -n <ns> exec <pod> -- ls <mountPath>`. For a pod the
  user has barred from `exec`, ask the user to check the app instead.
- Each replacement runs on the node its predecessor ran on (compare with the saved list). A pod
  that moved may have left an unfinished upload in its old node's cache. Check that node with the
  command under "Write cache" in the runbook, report what it lists, and drain it as described
  there if the user agrees.
- The step 4 loop again: every driver pod answers, no queued upload stays, no 401 or 429 counter
  grows.
- `kubectl -n csi-rclone logs -l app=csi-rclone-node -c rclone --since=15m --tail=-1 --prefix`
  shows no errors. Without `--tail=-1` a label selector prints only ten lines per pod.

Report the result, and remind the user to look at the three alerts the next day.

### 9. Rollback

A PR that puts the previous chart version back. The user merges it; then steps 6 to 8 again.

## Restart without an upgrade

After `RcloneDriverRestarted`, or a driver pod deleted by hand: steps 7 and 8 for the consumers of
that node.

## Mistakes this sequence prevents

| Mistake                                           | Consequence                                                          |
| ------------------------------------------------- | -------------------------------------------------------------------- |
| Merging because it is "only a patch"              | Consumers fail with nobody watching; their pods still show Ready     |
| Trusting `rollout status` right after the merge   | It reports the old revision; consumers get restarted too early       |
| Restarting consumers before the last node is done | They mount on a driver that is about to be replaced, and break again |
| Listing consumers after the rollout only          | No record of which node each pod ran on                              |
| Fixing an upstream workaround in the same PR      | Two changes at once; a failure cannot be attributed                  |
