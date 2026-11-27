# CLAUDE.md — Piraeus / LINSTOR

## Overview

Piraeus Datastore provides replicated block storage via DRBD + LVM_THIN. It is deployed as a
Helm-based ArgoCD Application (`application-piraeus.yaml`) with config resources applied in the
same wave (no separate config Application — all files are in this directory).

## Storage pool layout

All three nodes contribute a pool named `pool1` (the name the `piraeus-replicated` StorageClass
references). All three share one disk layout and one `LinstorSatelliteConfiguration`.

| Node           | OS disk    | Storage disk   | Pool config                      |
| -------------- | ---------- | -------------- | -------------------------------- |
| wieseschwarm-1 | `/dev/sda` | `/dev/nvme0n1` | `storage-nodes` (label selector) |
| wieseschwarm-2 | `/dev/sda` | `/dev/nvme0n1` | `storage-nodes` (label selector) |
| wieseschwarm-3 | `/dev/sda` | `/dev/nvme0n1` | `storage-nodes` (label selector) |

wieseschwarm-2 used to be inverted (OS on its NVMe, pool on its SATA SSD) and had its own
hostname-selected config. It was moved to SATA because its firmware cannot boot Talos from that
NVMe. Its pool is smaller than the other two, which does not matter while it only holds diskless
tiebreakers.

See `talos/CLAUDE.local.md` for exact disk models and sizes.

## LinstorSatelliteConfiguration

`linstorsatelliteconfiguration-piraeus.yaml` contains two documents:

**`talos-loader-override`** (all nodes) — removes the upstream DRBD init containers
(`drbd-shutdown-guard`, `drbd-module-loader`) that are incompatible with Talos, and remaps the
LVM config host paths to Talos's non-standard locations (`/var/etc/lvm/…`).

**`storage-nodes`** (nodes with `node-role.kubernetes.io/storage: "true"`, i.e. all three; the
label is set by the Talos device-specific patches) — creates `pool1` as an LVM thin pool on
`/dev/nvme0n1`. The `source.hostDevices` field tells Piraeus to prepare the raw device
(`pvcreate` + `vgcreate linstor_thinpool` + `lvcreate thinpool`) before registering the pool with
LINSTOR.

## StorageClass

`piraeus-replicated` is the cluster default (`is-default-class: true`):

- `storagePool: pool1` — references the pool name configured above
- `placementCount: "2"` — each volume gets two DRBD replicas across distinct nodes. This is a
  deliberate capacity/durability tradeoff: only two nodes contribute storage, and DRBD adds a
  diskless tiebreaker on the third node so quorum survives a single node failure. For data that
  must survive rebuilding a storage node, add a second StorageClass with `placementCount: "3"`
  once a third storage node exists.
- `volumeBindingMode: WaitForFirstConsumer` — PVC binding is deferred until a pod is scheduled,
  so LINSTOR can co-locate the primary replica with the consumer pod

## Adding storage capacity

Do **not** add new nodes to `storage-nodes` if their OS disk is `/dev/nvme0n1`. Create a
separate `LinstorSatelliteConfiguration` with a hostname (or custom label) selector and a
`source.hostDevices` entry pointing to the correct storage disk for that node, to avoid
clobbering the OS disk.

## After a node reboot

Observed during the Talos v1.14.7 rollout on 2026-11-27. Neither needs action unless it lasts
longer than stated.

- **The node shows `OFFLINE` in LINSTOR for up to about 2 minutes.** The satellite pod comes back
  with a new pod IP, and the controller keeps dialling the old one
  (`Establishing connection to node '<node>' via /<old-ip>:3366`) until the operator writes the
  new address into the LINSTOR node. The `linstor-wait-node-online` init containers of the CSI
  node plugin and the NFS server on that node wait for the same thing.
- **Volume attaches to that node fail for 1 to 2 minutes**, first with
  `CSINode <node> does not contain driver linstor.csi.linbit.com`, then with
  `ControllerPublishVolume … NotFound`, until the CSI node plugin has registered and LINSTOR has
  loaded the node. Kubernetes retries the attach, and it succeeds by itself.

### Hung resync

A resync can stop halfway, with the target waiting for a bitmap the source never sends. This
happened once, on a 1 GiB volume: the source ran DRBD 9.3.4, the target 9.3.5, and the diskless
Primary started a new data generation during the resync. The target logged
`Peer was unstable during resync` and then `[resync-again]`.

Symptom: the target's own row in `linstor resource list` stays at the same `SyncTarget(x%)`
across checks a minute apart, while the source's row shows `UpToDate`.

```bash
kubectl -n piraeus-datastore exec deploy/linstor-controller -- linstor resource list --faulty
```

Confirm on both ends:

```bash
kubectl -n piraeus-datastore exec <satellite-pod> -c linstor-satellite -- \
  drbdsetup status <resource> --verbose --statistics
```

A hung resync shows `disk:Inconsistent`, `blocked:upper` and `replication:WFBitMapT` toward the
source on the target, and `replication:Established` with `out-of-sync:0` toward the target on the
source.

The volume stays usable, but it has a single good copy until the hang is cleared, so leave the
source node alone. Rebooting the target node cleared it. A lighter fix, not yet tried here, is a
new handshake from the target's satellite:

```bash
kubectl -n piraeus-datastore exec <target-satellite-pod> -c linstor-satellite -- \
  drbdadm disconnect <resource>:<source-node>
kubectl -n piraeus-datastore exec <target-satellite-pod> -c linstor-satellite -- \
  drbdadm adjust <resource>
```
