# CLAUDE.md

Multi-source Application at wave -2: the `csi-driver-rclone` chart plus `config/`. Namespace:
`csi-rclone` (privileged). StorageClass: `rclone`.

The driver mounts a folder of a remote storage into a pod as an ordinary volume. Its use here is
a folder on a WebDAV server. Linux has no kernel WebDAV client, so such a mount means FUSE,
and FUSE needs `CAP_SYS_ADMIN`. The driver's pods hold that privilege on every node, so the apps
that use a folder need none.

Statements marked **measured** were tested on this cluster on 2026-11-27 with chart 0.5.1 against
the real WebDAV server on Talos v1.14.7 (kernel 6.18). One exception: the two failing `fsGroup` cases were
measured the same day on Talos v1.14.4 with a local test remote. The rest comes from reading the
driver's code at that version.

## Using it from an app

Three objects in the app's namespace.

A SopsSecret with exactly these keys:

```yaml
remote: <namespace>-<pvc>
remotePath: <folder inside the account>
configData: |
  [<namespace>-<pvc>]
  type = webdav
  url = https://<webdav-host>/<path to the account's files>/
  vendor = <rclone's name for the server software>
  user = <user>
  pass = <app password, obscured>
```

`pass` is the output of `docker run --rm -i rclone/rclone obscure -`. Obscuring is not
encryption, hence the SopsSecret.

A PVC that names the Secret:

```yaml
metadata:
  annotations:
    rclone.csi.veloxpack.io/secret-name: <name of the Secret>
    k8up.io/backup: "false"
spec:
  storageClassName: rclone
  accessModes: [ReadWriteOncePod]
  resources:
    requests:
      storage: 1Mi # ignored by the driver
```

And the workload, with `fsGroup` equal to `runAsUser` and `strategy: Recreate`.

| Rule                                                                                                        | Why                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| The remote is named `<namespace>-<pvc>`, in the section header and in `remote`.                             | The driver keeps one rclone configuration per node process, keyed by remote name. It merges keys on load, deletes the section on unmount, and the cache path contains the name. Two volumes with one name on one node would share settings and cache. Distinct names: **measured**, side by side on one node.            |
| `fsGroup` equals `runAsUser`, always.                                                                       | The mount shows every file as owned by the `fsGroup` id with modes `0755`/`0644`, and the kernel enforces them. **Measured:** a different `fsGroup` reads but cannot write; without `fsGroup` the pod does not start.                                                                                                    |
| `hostUsers: false` stays the default.                                                                       | It works (**measured**) because the StorageClass selects rclone's `mount2` with `allow_idmap`. rclone's default mount cannot be id-mapped: such a pod fails with `failed to set MOUNT_ATTR_IDMAP`.                                                                                                                       |
| `ReadWriteOncePod` and `strategy: Recreate`. `ReadWriteMany` only if pods on several nodes need the folder. | Two pods with one volume on one node run two write caches in one directory, which rclone's docs warn can corrupt data. With `ReadWriteOncePod` the scheduler refuses the second pod (**measured**).                                                                                                                      |
| A second app that needs the same folder gets its own PVC, Secret and remote name.                           | Same reason: one cache directory per mount.                                                                                                                                                                                                                                                                              |
| No other keys in the Secret, in particular no `cache_dir` or `log_level`.                                   | They act on the whole node process, not on the one mount.                                                                                                                                                                                                                                                                |
| No `remote`, `remotePath` or `configData` in the StorageClass or in a PV.                                   | They would sit in plain text on the PV, and the driver logs PV attributes in full. Secret values are stripped from its log (**measured**); remote name and path appear there in clear.                                                                                                                                   |
| A liveness probe that reads the mount, for example `ls <mountPath>`.                                        | It cannot heal a stale mount, but it makes one visible. **Measured:** after a driver restart the kubelet stops the container and cannot start a new one on the dead mount, so the pod turns not Ready and `KubeDeploymentReplicasMismatch` stays on until the pod is replaced. Without the probe the app fails silently. |
| The PVC requests `1Mi`.                                                                                     | The API demands a size and the driver ignores it (**measured:** a 5 MiB file fit into a `1Mi` volume). A token value does not pretend to be a capacity.                                                                                                                                                                  |
| No egress NetworkPolicy for the mount.                                                                      | The traffic leaves from the driver on the host network, not from the app's pod.                                                                                                                                                                                                                                          |

Backups. The data lives on the server and is backed up there, so k8up must leave the volume
alone: the PVC carries the annotation `k8up.io/backup: "false"`. The ValidatingAdmissionPolicy
`rclone-pvc-no-k8up` enforces it (**measured**): a PVC of this class without the annotation is
rejected, and so is removing it later. A PVC in deletion is exempt, and the policy ignores its own
errors, so a broken policy lets PVCs through and never blocks them.

Why the annotation and not the access mode, read from k8up v2.16.1's code and not tested: k8up
takes every bound PVC that is `ReadWriteOnce` or `ReadWriteMany` and skips the rest. A
`ReadWriteOncePod` volume is therefore skipped anyway, while a `ReadWriteMany` one would be read
through the mount into the app's backup. k8up has no setting per StorageClass. Confirm in the
first consumer that the namespace's backup still runs.

A pod with two writing uids. **Assumption, untested:** the extra Secret key `umask: "002"` should
make that mount group-writable (`0775`/`0664`), and the kubelet puts every container of the pod
into the `fsGroup` group, so a second uid could then write. It follows from how rclone computes
modes; nobody has run it. `umask` is a per-mount option, unlike `cache_dir`. Whoever needs it
first tests it and replaces this paragraph with the result.

## Credentials

A password usually opens the whole account on the server, not one folder. How to scope it is each
consumer's decision. What is specific to the server used here (address format, account options,
lockout commands) is in the applications submodule's `CLAUDE.md`.

**Rotate new-first:** create the new app password, re-encrypt the Secret, restart the consumer
pod (the Secret is read at mount time), and only then revoke the old password.

Why the order matters, **measured**: a refused password makes the driver send failed logins, and
rclone retries each operation up to ten times. The server's brute-force protection counted 11
attempts and then answered every login from that node with `429 Too Many Requests`, the new valid
password included. The driver reaches the server over IPv6, and the server counts per IPv6 subnet,
which the three nodes share, so the lockout covers the driver on all of them: a second node was
already delayed by 25 seconds per request, the third was not tried. Pods are IPv4-only and were
not affected. A typo in a new consumer's Secret has the same effect.

Recovery:

1. Fix the Secret.
2. Look up one node's global IPv6 address: `talosctl -n <node> get addresses`. Do not write it
   down anywhere; its prefix is not public knowledge.
3. On the server, look up and clear the lockout for that address; the commands are in the
   applications submodule's `CLAUDE.md`. Checking the nodes' IPv4 addresses shows nothing and
   resets nothing.
4. Restart the consumer pod.

What a consumer sees when its credentials are refused, **measured:** reads fail with
`Input/output error` within seconds, creating and opening files about 20 seconds later.
Directory listings keep working from the cache. While the password was merely refused, no call
took longer than about ten seconds. Once the lockout had set in, a read took 25 seconds before
failing and a new pod stayed in `ContainerCreating`, its mount refused. An unreachable server
was not tested.

## Reclaim policy

The StorageClass says `Retain`. Today the driver's `DeleteVolume` does nothing, so `Delete` would
be harmless, but upstream issue #64 calls that a bug. If a release ever makes it delete, `Delete`
would wipe the remote folder, the only copy, as soon as its PVC goes. With `Retain` no driver
version can do that.

The price: a removed consumer leaves a `Released` PV behind. Deleting it is safe for the same
reason (**measured:** folder and files stayed):

```bash
kubectl get pv -o json | jq -r '.items[]
  | select(.spec.storageClassName == "rclone" and .status.phase == "Released")
  | .metadata.name' | xargs -r kubectl delete pv
```

## Driver restarts and upgrades

All mounts of a node live inside the driver's one process on that node. When it stops, they die.
**Measured:** the consumer gets `Transport endpoint is not connected` on every read and write
while Kubernetes still shows its pod Running and Ready with no restart. Nothing heals this:
a restart of the consumer's container does not remount (**measured:** the kubelet could not even
create the new container). Only a new consumer pod does.

After any restart of a driver pod, delete the consumer pods on that node, then check that each
replacement runs on the same node as before. One that moved may have left an upload behind; see
"Write cache". This lists the consumer pods:

```bash
pvcs=$(kubectl get pvc -A -o json | jq -c '[.items[]
  | select(.spec.storageClassName == "rclone")
  | "\(.metadata.namespace)/\(.metadata.name)"]')
kubectl get pods -A -o json | jq -r --argjson pvcs "$pvcs" '.items[] | . as $p
  | select(any(.spec.volumes[]?; .persistentVolumeClaim.claimName as $c
      | $c != null and ($pvcs | index("\($p.metadata.namespace)/\($c)")) != null))
  | "\(.metadata.namespace)/\(.metadata.name) \(.spec.nodeName)"'
```

Upgrades therefore never merge unattended. Renovate opens the PR and leaves it; the
`csi-rclone-upgrade` skill supervises the rest. The chart version is pinned exactly: an `x`
range would let Argo CD upgrade the driver without any PR.

Talos upgrades and node reboots restart driver and consumers together and need nothing extra.

## Write cache

Writes land in a cache on the node's disk and reach the server about five seconds after the file
is closed. The cache is the hostPath `/var/lib/rclone-cache` on the node's EPHEMERAL partition,
beside images and logs. rclone trims each mount's cache to 2 GiB, but by its documentation never
evicts a file that is open or not yet uploaded, so a mount whose uploads fail can grow past that.

Two settings put it there, and both are needed: the chart values `node.cache.*` only mount the
hostPath into the driver; the StorageClass option `cache-dir` makes rclone use it. Without the
option the cache lives in the container and dies with it.

**Measured:** 20 MiB were written and the driver killed a second later. The data stayed in the
cache, the server did not have the file, and after the consumer pod's restart it arrived complete
with an identical checksum. A pending upload resumes at the next mount of the same remote name
on the same node, so a consumer that moves to another node leaves it behind until it returns.

That case, **measured:** with the consumer restarted on another node, the server never got the
file. No alert sees it, because the driver's metrics only cover live mounts. The cache's metadata
does: this lists a node's files that were never uploaded, as `<remote>/<folder>/<file>`:

```bash
kubectl -n csi-rclone exec <driver pod on that node> -c rclone -- \
  grep -rlE '"Dirty": *true' /var/lib/rclone-cache/vfsMeta
```

To drain it, let a pod mount the same PVC on that node once: cordon the other nodes, delete the
consumer pod, wait until the list is empty, uncordon. Measured: the file was uploaded intact five
seconds after the mount. The upload carries the file as it was when it got stuck, so expect it to
overwrite a newer version written elsewhere in the meantime (not tested).

On unmount the driver waits up to about two minutes for queued uploads.

## Alerts and metrics

| Alert                   | Means                                                                                                 | Do                                                                                  |
| ----------------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `RcloneDriverRestarted` | The `rclone` container of a driver pod restarted (crash, OOM kill). Every mount on its node is stale. | Delete the consumer pods on that node (command above).                              |
| `RcloneUploadsStuck`    | A mount has had queued or failed uploads, or a full cache, for 30 minutes.                            | Check the server and the driver's log. Restarting the consumer retries the uploads. |
| `RcloneLoginRefused`    | The server answered the driver with 401 or 429.                                                       | Find the consumer with the bad Secret, then follow "Credentials", Recovery.         |

`RcloneDriverRestarted` counts container restarts. A driver pod that is replaced (upgrade,
deletion, eviction) starts at zero and raises nothing; upgrades are covered by the skill, an
eviction by nothing. The alert also clears by itself after ten minutes although the mounts stay
dead. A consumer with a liveness probe on its mount closes both gaps: it turns not Ready and
keeps `KubeDeploymentReplicasMismatch` on until it is replaced. `RcloneLoginRefused` shortens the search but cannot prevent the lockout. It
is proven by a rule test only: firing it for real would repeat the lockout.

The ServiceMonitor keeps three `csi_driver_vfs_*` series per mounted volume and
`rclone_http_status_code` for codes 401, 403 and 429, plus the five automatic series per node.
The endpoint offers about 76 series per node. Never keep `csi_driver_mount_healthy`: it carries
the mount's age as a label and so creates a new series with every scrape.

## Network path

Driver and controller pods run on the host network. NetworkPolicies do not apply to them; the
namespace carries the baseline policies by convention only. Connections to the server leave with
the node's own addresses, today over IPv6 (**measured**). If the server moves or its address
changes, repeat the acceptance test.

**Port 5572 is open on every node on purpose.** The driver's metrics endpoint listens there on all
of the node's addresses, plain HTTP, no authentication. It serves `/metrics` only, but that names
the server's hostname and, while a volume is mounted, its `<namespace>-<pvc>`. Anyone on the nodes'
network, or on a network routed to it, can read it. The router forwards nothing from the internet
to the nodes; that was read from its configuration on 2026-11-27, not probed from outside. The
chart's `node.metrics.addr` cannot narrow the listener: the driver is a Go program, and a Go
listener on `0.0.0.0` still opens a dual-stack socket (tried).

## Security

A privileged, host-network DaemonSet on every node, running third-party code. Accepted with this
known gap: the driver mounts whatever a Secret describes, and its image carries every rclone
backend. A Secret with `type = local` reaches the node's kubelet directory, so whoever can create
a Secret and a PVC in any namespace can read every pod's volumes on a node. Every remote is also
dialled from the node's address, outside all NetworkPolicies. This is acceptable because only git
through Argo CD creates such objects here and app pods carry no service account token. Revisit it
if that changes. The driver has no backend allowlist. Its remote-control API stays off.

## Limits

- Every consumer depends on the WebDAV server being up.
- WebDAV over FUSE is slower than a local disk and has no file locking across mounts. Apps that
  need locking or many small random writes are a poor fit.
- All files of a mount have one owner.

## Upstream

Weaknesses found here, to be reported upstream; links follow once they exist.

- The chart's cache values mount the hostPath but do not make rclone use it.
- Pods with `hostUsers: false` need `mount2` with `allow_idmap`; undocumented.
- The DaemonSet's update strategy is fixed to `RollingUpdate`.
- Remotes of different volumes share one process-wide configuration by name.
- `csi_driver_mount_healthy` carries a changing label, and reports `mount_type="mount"` for a
  `mount2` mount.
- The `log_level` parameter ignores its value and changes the whole process.
- Issue #64: if `DeleteVolume` starts deleting, it should be opt-in.
