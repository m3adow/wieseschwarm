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

Pin both versions. `gen config` takes its defaults from the `talosctl` binary, not from the cluster, and the v1.14 client has a newer config contract than the live config, which was generated under v1.13.

- **Without `--talos-version v1.13`** it emits the 1.14 multi-doc layout. The nodes accept it, but every regeneration then looks like a full restructuring and may enable unreviewed defaults. The `v1.13` pin is temporary: it holds the config at its current contract until the migration to the 1.14 format (#263). Bump it deliberately, never automatically.
- **Without `--kubernetes-version`** it writes its own default Kubernetes release (1.37.1 for v1.14.2) into every control-plane image. Applying that starts an unplanned minor upgrade node by node, bypassing `upgrade-k8s`'s checks, with a newer kubelet than some apiservers along the way. The command reads the version from the running cluster, so it follows upgrades by itself.

The patches don't overlap today, so their order doesn't change the output. It's kept identical everywhere so that a future overlapping key resolves the same way.

```bash
K8S_VERSION=$(kubectl version -o json | jq -r .serverVersion.gitVersion | sed 's/^v//')
cd talos/secret/ && \
source .env && \
talosctl gen config \
  --output-types controlplane \
  --with-secrets secrets.yaml \
  $CLUSTER_NAME https://$CLUSTER_ENDPOINT:6443 \
  --force -o controlplane.yaml \
  --talos-version v1.13 --kubernetes-version "$K8S_VERSION" \
  --config-patch @../wieseschwarm-all-patch.yaml \
  --config-patch @wieseschwarm-all-patch.yaml \
  --config-patch @../piraeus-patch.yaml \
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

Each node gets the base `controlplane.yaml`, its device-specific disk selector patch, and its
hostname patch. **All three are required — never drop the hostname patch** (see Notes).

**Node IPs and the exact ready-to-run per-node commands live in `talos/CLAUDE.local.md`**
(gitignored) under "Applying config to nodes" — read that file and run the commands from there.
This repository is public, so node addresses are kept out of it.

The general form is:

```bash
talosctl apply-config --nodes <node-ip> \
  --file talos/secret/controlplane.yaml \
  --config-patch @talos/device-specific/<disk-selector-patch>.yaml \
  --config-patch @talos/device-specific/<node>-hostname-patch.yaml
```

Apply nodes one at a time and wait for each to succeed before moving to the next.

Verify with `--dry-run` first — it prints the exact config diff without applying anything.

## Notes

- **Never apply config to a node without its `*-hostname-patch.yaml`.** The generated base config
  sets `HostnameConfig.auto: stable`, which invents a `talos-<generated>` hostname whenever no
  higher-priority source supplies one. On 2026-08-02 the DHCP server stopped sending option 12, all
  three nodes rebooted into generated hostnames and re-registered as **new** Kubernetes nodes,
  orphaning the LINSTOR satellites and a then-existing hostname-pinned satellite configuration.
  107 of 164 pods were left unhealthy. The hostname patches pin the name as a static
  hostname (highest priority, overrides DHCP), so applying without them re-arms the same failure.
- `TALOSCONFIG` must point to `talos/secret/talosconfig` (or be set already in the shell). If commands
  fail with auth errors, remind the user: `export TALOSCONFIG=$(pwd)/talos/secret/talosconfig`
- Never use `talosctl machineconfig patch` — it appends list fields on every run, causing duplicate
  kernel modules and extensions in the resulting config.
- The disk selector in `wieseschwarm-all-patch.yaml` is intentionally impossible (`/dev/doesnotexist`).
  This is deliberate — it forces Talos to refuse installation without the device-specific patch.
