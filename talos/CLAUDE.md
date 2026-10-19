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

## Environment variables

Always source the environment variables in `talos/secret/.env` before running commands as they may be required implicitly.

## Generating control plane configs

See `talos/CLAUDE.local.md` for the environment variable values.

**Always use `talosctl gen config`, never `talosctl machineconfig patch`.**

`machineconfig patch` appends list fields on every run, causing duplicate kernel modules and extensions.

Pin both versions. `gen config` takes its defaults from the `talosctl` binary, not from the cluster:
without `--talos-version` a v1.14 client emits the multi-doc layout, and without
`--kubernetes-version` it writes its own default Kubernetes release into every control-plane image.
See "Key constraints" for why `v1.13` is temporary.

```bash
K8S_VERSION=$(kubectl version -o json | jq -r .serverVersion.gitVersion | sed 's/^v//')
cd talos/secret/
talosctl gen config \
  --output-types controlplane \
  --with-secrets secrets.yaml \
  $CLUSTER_NAME https://$CLUSTER_ENDPOINT:6443 \
  --force \
  --talos-version v1.13 --kubernetes-version "$K8S_VERSION" \
  --config-patch @../wieseschwarm-all-patch.yaml \
  --config-patch @wieseschwarm-all-patch.yaml \
  --config-patch @../piraeus-patch.yaml
```

Then apply per node — see `talos/CLAUDE.local.md` for the apply commands.

## Regenerating talosconfig

The `talosconfig` client config only needs re-generation when cluster secrets or the API endpoint change. Patches are not needed.

```bash
K8S_VERSION=$(kubectl version -o json | jq -r .serverVersion.gitVersion | sed 's/^v//')
cd talos/secret/
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
- **NetworkPolicy engine is Talos-managed.** `kubeNetworkPoliciesEnabled: true` makes Talos render a `kube-network-policies` container into the `kube-flannel` DaemonSet. Talos only applies that (and its version bumps) on `talosctl upgrade-k8s`, never on an OS upgrade or config apply. Metrics, alerts and behaviour: `kubernetes/01_infrastructure/kube-network-policies/CLAUDE.md`.

## Security defaults inherited from Talos

Talos ships CIS-Kubernetes-Benchmark-aligned defaults, so several hardening settings this cluster
relies on are **not** set by any patch in this directory. They are Talos defaults, which means they
survive `talosctl gen config` regeneration but are also invisible to `grep` over the git tree.

| Default                               | Effect                                                                                   |
| ------------------------------------- | ---------------------------------------------------------------------------------------- |
| `defaultRuntimeSeccompProfileEnabled` | kubelet applies `RuntimeDefault` seccomp to every pod that does not set `seccompProfile` |
| Pod Security Admission                | `baseline` enforced, `restricted` audited and warned                                     |
| Secrets encrypted at rest             | via `secretboxEncryptionSecret` generated at cluster creation                            |
| Audit logging                         | enabled at `Metadata` level                                                              |

Observable at `machine.kubelet.defaultRuntimeSeccompProfileEnabled: true` in the generated
`secret/controlplane.yaml` and `secret/worker.yaml` — but note `secret/` is gitignored, so those
files are not a shareable reference. See
[Talos default hardening and CIS compliance](https://docs.siderolabs.com/talos/v1.13/security/talos-default-hardening-and-cis-compliance)
for the authoritative list, and check it again on a Talos major upgrade in case a default changes.

What this means for manifests is documented in `kubernetes/CLAUDE.md` under "Non-root workloads".

## Kubernetes feature gates

Feature gates are configured in `talos/wieseschwarm-all-patch.yaml` under
`machine.kubelet.extraArgs.feature-gates` (kubelet feature gates) and under
`cluster.apiServer.extraArgs.feature-gates` / `cluster.scheduler.extraArgs.feature-gates`
(control-plane feature gates). All values must be comma-separated on a single line.

| Gate                        | Component                     | Status (K8s 1.33) | Purpose                                                                                              |
| --------------------------- | ----------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------- |
| `InPlacePodVerticalScaling` | kubelet, apiServer, scheduler | Beta              | Required for VPA `InPlaceOrRecreate` mode to resize containers without eviction                      |
| `RecursiveReadOnlyMounts`   | kubelet                       | Beta              | Enables `recursiveReadOnly: Enabled/IfPossible` on volume mounts; see `kubernetes/CLAUDE.md`         |
| `UserNamespacesSupport`     | kubelet                       | Beta              | Allows pods to run in a Linux user namespace via `spec.hostUsers: false`; see `kubernetes/CLAUDE.md` |

When a gate graduates to GA it becomes always-on and can be removed from this list. Verify
current status before upgrading Kubernetes.

**`UserNamespacesSupport` requires a matching sysctl, or the feature gate is silently non-functional.** Talos defaults `user.max_user_namespaces` to `0`; with the gate enabled but this unset, any pod setting `spec.hostUsers: false` fails pod sandbox creation with `failed to start noop process for unshare: fork/exec /proc/self/exe: no space left on device` — a Linux kernel `ENOSPC` quirk for hitting a `max_*_namespaces` ceiling, not an actual disk-space problem. `wieseschwarm-all-patch.yaml` sets `machine.sysctls.user.max_user_namespaces: "11255"` (Talos's own recommended value) alongside the gate. This bug can stay latent indefinitely — it only surfaces the first time a pod actually sets `hostUsers: false`.

## Upgrading Talos on this hardware

Use the `talos-upgrader` agent, one node at a time. The last rollout (v1.13.3 to v1.14.4, October 2026) established these rules:

- **Cold-boot every node after an upgrade or a fresh install.** Both end in a kexec, which never goes through the firmware, so a node can run fine while being unable to boot. That hid wieseschwarm-2's NVMe boot hang for months. Shut down, power on, then compare the boot source:

  ```bash
  G=4a67b082-0a4c-41cf-b6c7-440b29bb8c4f
  talosctl -n <ip> read /sys/firmware/efi/efivars/LoaderDevicePartUUID-$G | tail -c +5 | tr -d '\0'
  talosctl -n <ip> get discoveredvolumes <esp-partition> -o yaml | grep partition_uuid
  ```

  Matching UUIDs mean the firmware booted that disk. The variable survives a kexec, so the check only proves something right after a power cycle. Remove any USB stick before testing: the firmware may boot it first.

- **`--image` with the factory `metal-installer` URL is mandatory.** The default installer has no drbd extension.
- **Upgrade the diskless node first, then the storage nodes one at a time.** Wait for every LINSTOR resource to be `UpToDate` before the next node.
- **`gen config` takes its defaults from the client, not the cluster.** Any client whose default Kubernetes release differs from the running one changes the control-plane images: the v1.14.2 client writes 1.37.1. Applying such a config upgrades Kubernetes node by node, outside `upgrade-k8s`. Always pass `--talos-version` and the running `--kubernetes-version`, as the examples above do.
- **After the rollout, apply the regenerated config with `--mode=no-reboot`.** Upgrades change what runs, not the declared `machine.install.image`.

## Key constraints

- **`--talos-version` is the config contract, and `v1.13` is temporary.** The live config was generated under the 1.13 contract, as a single v1alpha1 document. Talos 1.14 deprecated those fields (`.cluster.network`, `.machine.kubelet` and more) in favour of typed documents, so the contract has to move before a release removes them. Migration: #263. After a Talos minor upgrade, bump it deliberately:
  1. Generate with the new contract.
  2. Compare the result semantically with the live config.
  3. Review the newly enabled defaults.
  4. Apply with `--dry-run` first.

  Never derive it from the running Talos version: the first regeneration after an upgrade would then silently switch contracts.

- `wieseschwarm-all-patch.yaml` intentionally sets an impossible install disk (`/dev/doesnotexist`) so Talos refuses to install without an explicit device-specific patch — this prevents accidental disk selection on the wrong node.
- All nodes install the OS to a SATA SSD and keep their NVMe for Kubernetes storage: nodes 1 and 3 to Kingston SSDs (2 TB NVMe), node 2 to its Crucial SSD (256 GB NVMe). Node 2 used to install to its Samsung NVMe, but its firmware cannot boot Talos from it: the boot hangs at the Talos splash. A kexec reboot hides this, because it never goes through the firmware.
- All nodes are on the same L2 VLAN (`192.168.10.0/24`), which is required for both the Talos built-in VIP and Piraeus DRBD replication.
- Piraeus schematic: `e048aaf4...`. The installer tag in `piraeus-patch.yaml` is tracked by a Renovate custom manager against `siderolabs/talos` releases, so version bumps arrive as PRs — but merging one only changes the declared tag. The rolling node upgrade is still manual, via the `talos-upgrader` agent. Change the schematic itself by hand when adding or removing system extensions.
