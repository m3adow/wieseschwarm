# CLAUDE.md

kube-network-policies is the cluster's NetworkPolicy engine. Flannel itself enforces nothing.
The engine is **Talos-managed**: `kubeNetworkPoliciesEnabled: true` (see `talos/`) adds a
`kube-network-policies` container to the `kube-system/kube-flannel` DaemonSet. This directory
only adds what Talos does not: a metrics Service + ServiceMonitor. Alerts live in
`grafana-alloy/cluster-rules/prometheusrule-kube-network-policies.yaml`.

## Lifecycle

- **Talos only applies the container (and version bumps) on `talosctl upgrade-k8s`.** That
  includes every Kubernetes upgrade. A Talos OS upgrade or config apply alone changes nothing
  live.
- **The image version is pinned by the Talos release** (`KubeNetworkPoliciesVersion` in Talos's
  `constants.go`). Check it before a Talos upgrade:
  `talosctl get manifests 05-flannel -o yaml | grep kube-network-policies:`.
- **Talos exposes no flags.** `--v=2` (one log line per evaluated packet) and no memory limit are
  fixed. Log volume is filtered in Alloy, if at all.
- **The container declares no ports.** That is why metrics go through a selector-only headless
  Service: a PodMonitor needs a declared container port and would find nothing.

## Behaviour you need to know

- **Fail-open.** If the agent is down, traffic is unfiltered, never blackholed.
- **Evaluated after DNAT.** Policies match pod IPs and container ports, not Service ports.
- **Strict mode.** Tightening a policy cuts established connections within about 30 s.
- **Kubelet probes are always allowed** (uid-0 host traffic is exempt).
- **kube-apiserver is not exempt.** Webhooks and aggregated APIs need the host-source `ipBlock`
  set documented in `kubernetes/01_infrastructure/network-policies/CLAUDE.md`.
- **hostNetwork pods are external.** Only `ipBlock` matches them as sources, and policies do not
  protect them as destinations.
- **No NRI** (Talos mounts no socket). A brand-new pod's first packets can miss peer-selector
  matches. TCP retries hide it.
- **There is no audit mode.** Roll out policies per namespace with `scripts/netpol-probe.sh`.

## Debugging a suspected drop

```bash
kubectl -n kube-system logs ds/kube-flannel -c kube-network-policies --since=10m | grep -i denied
```

Metric `kube_network_policies_packet_count{verdict}` counts verdicts per node.

## Emergency: a policy is breaking production

ArgoCD self-heals both the policies and the child Application specs, so pause the automation
first:

1. `kubectl -n argocd patch application apps-of-apps --type json -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]'`
2. `kubectl -n argocd patch application network-policies --type json -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]'`
   (or the affected app's Application, for app-namespace policies)
3. `kubectl -n <ns> delete networkpolicy <name>` (or `--all`)
4. Fix it in git via PR, then restore the automation with `make argocd-apps-bootstrap`.
   The root re-syncs every child Application spec, which restores their `automated` blocks.

To stop the engine itself, remove the container from the DaemonSet. This restarts the flannel
pods one at a time, the engine fails open, and Talos re-adds it on the next `upgrade-k8s`
unless the flag is turned off first:

```bash
i=$(kubectl -n kube-system get ds kube-flannel -o json | jq '[.spec.template.spec.containers[].name] | index("kube-network-policies")')
kubectl -n kube-system patch ds kube-flannel --type json -p "[{\"op\":\"remove\",\"path\":\"/spec/template/spec/containers/$i\"}]"
```
