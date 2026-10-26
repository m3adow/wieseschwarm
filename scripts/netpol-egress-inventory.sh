#!/usr/bin/env bash
# Usage: TALOSCONFIG=<file> scripts/netpol-egress-inventory.sh <namespace-regex> <minutes> <outfile>
# Samples every node's conntrack table once a minute and appends each connection that a pod in
# a matching namespace opened, as "epoch src proto class dst-ip dport"; prints counts at the end.
set -euo pipefail

regex=$1
minutes=$2
out=$3
: "${TALOSCONFIG:?set TALOSCONFIG to the cluster talosconfig}"
nodes=$(kubectl get nodes -o jsonpath='{.items[*].status.addresses[?(@.type=="InternalIP")].address}')
podcidrs=$(kubectl get nodes -o jsonpath='{.items[*].spec.podCIDR}')
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for ((i = 1; i <= minutes; i++)); do
  round=$SECONDS
  kubectl get pods -A -o json | jq -r '.items[]
    | select(.status.podIP and .spec.hostNetwork != true)
    | [.status.podIP, .metadata.namespace + "/" + (.metadata.labels["app.kubernetes.io/name"]
        // .metadata.labels.app
        // (if .metadata.labels.k8upjob then "k8up-job" else .metadata.name end))]
    | @tsv' > "$tmp/pods"
  kubectl get svc -A -o json | jq -r '.items[]
    | ((select(.spec.clusterIP != null and .spec.clusterIP != "None")
        | [.spec.clusterIP, "svc:" + .metadata.namespace + "/" + .metadata.name]),
       (.status.loadBalancer.ingress[]?.ip as $ip
        | [$ip, "lb:" + .metadata.namespace + "/" + .metadata.name]))
    | @tsv' > "$tmp/svcs"
  : > "$tmp/ct"
  for n in $nodes; do
    talosctl -n "$n" read /proc/net/nf_conntrack >> "$tmp/ct" 2>/dev/null \
      || echo "warning: no conntrack from $n" >&2
  done
  awk -v now="$(date +%s)" -v re="$regex" -v nodes="$nodes" -v podcidrs="$podcidrs" \
    -v pods="$tmp/pods" -v svcs="$tmp/svcs" '
    function n4(ip, o) { split(ip, o, "."); return ((o[1] * 256 + o[2]) * 256 + o[3]) * 256 + o[4] }
    function inside(ip, cidr, c) {
      split(cidr, c, "/")
      return n4(ip) >= n4(c[1]) && n4(ip) < n4(c[1]) + 2 ^ (32 - c[2])
    }
    BEGIN {
      FS = "\t"
      while ((getline l < pods) > 0) { split(l, a, "\t"); pod[a[1]] = a[2] }
      while ((getline l < svcs) > 0) { split(l, a, "\t"); svc[a[1]] = a[2] }
      split(nodes, nl, " "); for (i in nl) node[nl[i]] = 1
      np = split(podcidrs, pc, " ")
      npriv = split("10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16", pr, " ")
      FS = " "
    }
    {
      src = dst = ""; dport = "-"
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^src=/ && src == "") src = substr($i, 5)
        else if ($i ~ /^dst=/ && dst == "") dst = substr($i, 5)
        else if ($i ~ /^dport=/ && dport == "-") dport = substr($i, 7)
      }
      if (!(src in pod)) next
      split(pod[src], s, "/"); if (s[1] !~ re) next
      if (dst in pod) cls = "pod:" pod[dst]
      else if (dst in svc) cls = svc[dst]
      else if (dst in node) cls = "node"
      else {
        cls = "external"
        for (i = 1; i <= np; i++) if (inside(dst, pc[i])) cls = "podnet"
        if (cls == "external") for (i = 1; i <= npriv; i++) if (inside(dst, pr[i])) cls = "private"
      }
      print now, pod[src], $3, cls, dst, dport
    }' "$tmp/ct" >> "$out"
  wait_s=$((round + 60 - SECONDS))
  if ((i < minutes && wait_s > 0)); then sleep "$wait_s"; fi
done

cut -d' ' -f2- "$out" | sort | uniq -c | sort -rn
