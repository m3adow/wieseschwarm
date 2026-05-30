# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What lives here

This directory contains only **patches** applied to a generic Talos control plane manifest. Full machine configs contain sensitive data and are gitignored — they live in `talos/secret/` which is also gitignored (except for its non-sensitive markdown and patch files).

## Patch placement rules

| Patch type                                                     | Location                 |
| -------------------------------------------------------------- | ------------------------ |
| Shared non-sensitive (e.g. kernel modules, storage extensions) | `talos/`                 |
| Device-specific non-sensitive (install disk selector per node) | `talos/device-specific/` |
| Sensitive (secrets, IPs, cluster-specific config)              | `talos/secret/`          |
| Dev cluster overrides                                          | `talos/dev/`             |

## Generating control plane configs

See `talos/CLAUDE.local.md` for the environment variable values.

**Always use `talosctl gen config`, never `talosctl machineconfig patch`.**

`machineconfig patch` appends list fields on every run, causing duplicate kernel modules and extensions.

```bash
cd talos/secret/
talosctl gen config \
  --output-types controlplane \
  --with-secrets secrets.yaml \
  $CLUSTER_NAME https://$YOUR_ENDPOINT:6443 \
  --force \
  --config-patch @../wieseschwarm-all-patch.yaml \
  --config-patch @wieseschwarm-all-patch.yaml \
  --config-patch @../piraeus-patch.yaml
```

Then apply per node — see `talos/CLAUDE.local.md` for the apply commands.

## Regenerating talosconfig

The `talosconfig` client config only needs re-generation when cluster secrets or the API endpoint change. Patches are not needed.

```bash
cd talos/secret/
talosctl gen config \
  --output-types controlplane \
  --with-secrets secrets.yaml \
  $CLUSTER_NAME https://$YOUR_ENDPOINT:6443 \
  --force -o controlplane.yaml \
  --config-patch @wieseschwarm-all-patch.yaml \
  --config-patch @../piraeus-patch.yaml \
  --config-patch @../wieseschwarm-all-patch.yaml \
  --with-docs=false --with-examples=false
```

## Dev cluster

```bash
make dev-cluster-create   # Docker-based Talos dev cluster (uses talos/dev/controlplane-patch.yaml + piraeus-patch.yaml)
make dev-cluster-destroy
make dev-cluster-new      # destroy + create in one step
```

Set env vars printed after creation: `export TALOSCONFIG=/tmp/talosconfig.dev KUBECONFIG=/tmp/kubeconfig.dev`

## Architecture decisions

- **3 mixed-role control plane nodes** — all nodes are schedulable, no dedicated workers.
- **Talos built-in L2 VIP** for the Kubernetes API endpoint. Do **not** use Talos VIP for the Talos API — manage Talos directly via node IPs on port 50000.
- **kube-vip service mode only** for app-facing `LoadBalancer` services (e.g. Traefik VIP).
- **Piraeus Datastore** (DRBD + LVM_THIN) for replicated storage. `piraeus-patch.yaml` sets the Talos Image Factory installer with the `siderolabs/drbd` extension and loads `drbd`, `drbd_transport_tcp`, and `dm-thin-pool` kernel modules.
- **Traefik + Gateway API** for ingress, not `ingress-nginx`.

## Key constraints

- `wieseschwarm-all-patch.yaml` intentionally sets an impossible install disk (`/dev/doesnotexist`) so Talos refuses to install without an explicit device-specific patch — this prevents accidental disk selection on the wrong node.
- Nodes 1 and 3 have Kingston SSDs for the OS install (2 TB NVMe reserved for Kubernetes storage). Node 2 uses its Samsung NVMe as the install disk (no separate large storage disk).
- All nodes are on the same L2 VLAN (`192.168.10.0/24`), which is required for both the Talos built-in VIP and Piraeus DRBD replication.
- Piraeus schematic: `e048aaf4...`, Talos v1.13.2 (update `piraeus-patch.yaml` when upgrading Talos or changing extensions).
