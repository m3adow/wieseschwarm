# CLAUDE.md

Ingress NetworkPolicies for infrastructure namespaces. Enforced by the Talos-managed
`kube-network-policies` (see `../kube-network-policies/CLAUDE.md` for engine behaviour).

## Model

- Ingress only. Egress is unrestricted.
- The receiving namespace owns its policies. Infra namespaces live here, one directory each;
  application namespaces keep theirs next to the app.
- Every covered namespace pulls in `components/baseline`: `default-deny-ingress`,
  `allow-same-namespace` and `allow-own-node`. Then one extra policy per cross-namespace or host
  source it needs.
- Not covered on purpose:
  - `kube-system`: hostNetwork control plane; CoreDNS serves every pod.
  - `default`: no workloads.
  - `argocd`: no baseline, and its upstream policies are left untouched. `argocd/` only adds
    `allow-own-node`, scoped to the pods those policies already isolate. Without it, the probe
    teardown on `argocd-notifications-controller:9001` (`tcpSocket`) was dropped: about hourly
    on wieseschwarm-1, but often enough on wieseschwarm-3 to fire `KubeNetworkPoliciesDropping`.

## Rules

- **Pod selectors inside a NetworkPolicy use `matchExpressions`, never `matchLabels`.**
  The kustomize `labels:` transformer with `includeSelectors: true` (used by every app)
  rewrites `podSelector.matchLabels` inside `from`/`to` peers to the app's own name, silently
  pointing the rule at the wrong pods. It does not touch `matchExpressions`.
- **Every kustomization that pulls in `components/baseline` sets `namespace:`.** The component's
  policies carry no namespace of their own. Without the field, ArgoCD still places them in the
  Application's destination namespace, but a `kubectl apply` of the rendered output puts them into
  `default`.
- **A pod's own node is always allowed** (`allow-own-node`: the three node IPs and the three cni0
  addresses `10.244.X.1`, all ports). That is the documented NetworkPolicy semantics: an isolated
  pod still accepts connections from its own node. kube-network-policies only exempts packets from
  uid-0 sockets, which misses kernel-generated packets. Kubelet probes that close a connection with
  unread data (a large `/metrics` response, `tcpSocket` probes) end with a RST that has no owning
  socket, and without this policy each one was dropped, firing `KubeNetworkPoliciesDropping`.
  Other nodes are not covered: their traffic arrives as their flannel.1 address `10.244.X.0`.
  The address set exists twice, in `components/baseline` and `argocd/`. A node change updates
  both: `git grep -l '10.244.0.1/32' kubernetes/`.
- **Rules match pod ports, not Service ports** (the engine evaluates after DNAT).
- **Exposed LoadBalancer ports get a rule without `from`.** All LB Services use
  `externalTrafficPolicy: Cluster`, so client IPs are partly masqueraded away.
- **Apps that need a shared infra service opt in with a namespace label**, so this public
  directory never names private apps. Today: `netpol.wieseschwarm/mariadb-client: "true"`
  → MaxScale 3306.
- **Same-namespace traffic is never restricted.** Galera replication, DRBD between LINSTOR
  satellites (pod network, not hostNetwork) and Alloy clustering depend on it.

## Host-source set (kube-apiserver → webhooks, aggregated APIs, hostNetwork clients)

kube-apiserver and hostNetwork pods are not pods to the engine. Allow them with exactly these
`ipBlock`s, and only on the port that needs them:

| CIDR               | What                                                         |
| ------------------ | ------------------------------------------------------------ |
| `192.168.10.11/32` | wieseschwarm-1 node IP (same-node apiserver, pre-masquerade) |
| `192.168.10.12/32` | wieseschwarm-2 node IP                                       |
| `192.168.10.13/32` | wieseschwarm-3 node IP                                       |
| `10.244.0.0/31`    | wieseschwarm-1 flannel.1 (.0) + cni0 (.1)                    |
| `10.244.1.0/31`    | wieseschwarm-3 flannel.1 + cni0                              |
| `10.244.2.0/31`    | wieseschwarm-2 flannel.1 + cni0                              |

Never widen to `10.244.0.0/16` (every pod) or `192.168.10.0/24` (the whole server VLAN).
**Adding a node or changing a podCIDR requires updating every file that contains this set:**
`git grep -l '10.244.0.0/31' kubernetes/`. A Talos upgrade that changes flannel's masquerade
behaviour can also change it. Re-check with `talosctl netstat -k -t` on a webhook pod's node.

## Adding a namespace

1. `mkdir <ns>`. Add a `kustomization.yaml` with `namespace: <ns>`,
   `components: [../components/baseline]`, and any `networkpolicy-allow-*.yaml`.
2. Add a `DENY` line per listener to `scripts/netpol-expectations.txt`. Run
   `scripts/netpol-probe.sh` and confirm the lines are red **before** applying.
3. `kubectl apply -k <ns>`, run the functional checks, then the probe again (green).
4. Add `- <ns>` to `kustomization.yaml`.
