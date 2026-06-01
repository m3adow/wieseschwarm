# CLAUDE.md — Piraeus / LINSTOR

## Overview

Piraeus Datastore provides replicated block storage via DRBD + LVM_THIN. It is deployed as a
Helm-based ArgoCD Application (`application-piraeus.yaml`) with config resources applied in the
same wave (no separate config Application — all files are in this directory).

## Storage pool layout

All three nodes contribute a pool named `pool1` (the name the `piraeus-replicated` StorageClass
references). Because wieseschwarm-2's disk layout is inverted relative to nodes 1 and 3, it uses
a separate `LinstorSatelliteConfiguration`.

| Node           | OS disk        | Storage disk   | Pool config                                       |
| -------------- | -------------- | -------------- | ------------------------------------------------- |
| wieseschwarm-1 | `/dev/sda`     | `/dev/nvme0n1` | `storage-nodes` (label selector)                  |
| wieseschwarm-2 | `/dev/nvme0n1` | `/dev/sda`     | `storage-node-wieseschwarm-2` (hostname selector) |
| wieseschwarm-3 | `/dev/sda`     | `/dev/nvme0n1` | `storage-nodes` (label selector)                  |

See `talos/CLAUDE.local.md` for exact disk models and sizes.

## LinstorSatelliteConfiguration

`linstorsatelliteconfiguration-piraeus.yaml` contains three documents:

**`talos-loader-override`** (all nodes) — removes the upstream DRBD init containers
(`drbd-shutdown-guard`, `drbd-module-loader`) that are incompatible with Talos, and remaps the
LVM config host paths to Talos's non-standard locations (`/var/etc/lvm/…`).

**`storage-nodes`** (nodes with `node-role.kubernetes.io/storage: "true"`, i.e. wieseschwarm-1
and wieseschwarm-3) — creates `pool1` as an LVM thin pool on `/dev/nvme0n1`. The
`source.hostDevices` field tells Piraeus to prepare the raw device (`pvcreate` +
`vgcreate linstor_thinpool` + `lvcreate thinpool`) before registering the pool with LINSTOR.

**`storage-node-wieseschwarm-2`** (hostname selector `kubernetes.io/hostname: wieseschwarm-2`) —
same as above but targets `/dev/sda` (the Crucial 500G SATA SSD). A hostname selector is used
instead of the `storage: true` label because adding that label to wieseschwarm-2 would cause
`storage-nodes` to also match it and attempt to claim `/dev/nvme0n1` — the OS disk.

## StorageClass

`piraeus-replicated` is the cluster default (`is-default-class: true`):

- `storagePool: pool1` — references the pool name configured above
- `placementCount: "2"` — each volume gets two DRBD replicas across distinct nodes
- `volumeBindingMode: WaitForFirstConsumer` — PVC binding is deferred until a pod is scheduled,
  so LINSTOR can co-locate the primary replica with the consumer pod

## Adding storage capacity

Do **not** add new nodes to `storage-nodes` if their OS disk is `/dev/nvme0n1`. Create a
separate `LinstorSatelliteConfiguration` with a hostname (or custom label) selector and a
`source.hostDevices` entry pointing to the correct storage disk for that node, to avoid
clobbering the OS disk.
