---
name: talos-regen-apply
description: >
  Regenerates the Talos control plane machine config from patches and applies it to all cluster nodes.
  Use this skill whenever the user asks to regenerate, rebuild, or update the Talos config, apply
  Talos machine config changes to the cluster, push Talos patch changes to nodes, or run talosctl
  gen config followed by apply-config.
---

# Talos Config: Regenerate and Apply

This skill regenerates `talos/secret/controlplane.yaml` from all patches and applies the result to each node with its device-specific disk selector patch.

## Step 1 — Back up current config

```bash
cp talos/secret/controlplane.yaml /tmp/controlplane-before.yaml
```

## Step 2 — Regenerate controlplane.yaml

Run from `talos/secret/` (patches use relative paths):

```bash
cd talos/secret/ && \
source .env && \
talosctl gen config \
  --output-types controlplane \
  --with-secrets secrets.yaml \
  $CLUSTER_NAME https://$CLUSTER_ENDPOINT:6443 \
  --force -o controlplane.yaml \
  --config-patch @wieseschwarm-all-patch.yaml \
  --config-patch @../piraeus-patch.yaml \
  --config-patch @../wieseschwarm-all-patch.yaml \
  --with-docs=false --with-examples=false
```

## Step 3 — Show simplified diff

Filter out base64 certificate/key blobs (they come from `secrets.yaml` and don't carry config meaning):

```bash
diff -u /tmp/controlplane-before.yaml talos/secret/controlplane.yaml \
  | grep -Ev '^\s+[A-Za-z0-9+/]{40,}=*\s*$'
```

Present this diff to the user. If the diff is empty, say so explicitly — it means no patch values changed.

## Step 4 — Confirm before applying to live nodes

Applying config to running nodes is irreversible in the sense that it immediately affects the cluster.
Show the user what will be applied and ask for confirmation before proceeding.

## Step 5 — Apply per node (from repo root)

Each node gets the base `controlplane.yaml` plus its device-specific disk selector patch.

```bash
# wieseschwarm-1 — Kingston SSD
talosctl apply-config --nodes 192.168.10.11 \
  --file talos/secret/controlplane.yaml \
  --config-patch @talos/device-specific/wieseschwarm-1-and-3-patch.yaml

# wieseschwarm-3 — Kingston SSD
talosctl apply-config --nodes 192.168.10.13 \
  --file talos/secret/controlplane.yaml \
  --config-patch @talos/device-specific/wieseschwarm-1-and-3-patch.yaml

# wieseschwarm-2 — Samsung NVMe (no separate storage disk)
talosctl apply-config --nodes 192.168.10.12 \
  --file talos/secret/controlplane.yaml \
  --config-patch @talos/device-specific/wieseschwarm-2-patch.yaml
```

Apply nodes one at a time and wait for each to succeed before moving to the next.

## Notes

- `TALOSCONFIG` must point to `talos/secret/talosconfig` (or be set already in the shell). If commands
  fail with auth errors, remind the user: `export TALOSCONFIG=$(pwd)/talos/secret/talosconfig`
- Never use `talosctl machineconfig patch` — it appends list fields on every run, causing duplicate
  kernel modules and extensions in the resulting config.
- The disk selector in `wieseschwarm-all-patch.yaml` is intentionally impossible (`/dev/doesnotexist`).
  This is deliberate — it forces Talos to refuse installation without the device-specific patch.
