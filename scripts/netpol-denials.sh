#!/usr/bin/env bash
# Attributes kube-network-policies denials to source/destination pods, per node.
# A "Packet denied" line carries only an id; ids repeat within a log window, so each
# denial is paired with the most recent Ingress evaluation of the same id.
# Usage: scripts/netpol-denials.sh [SINCE_TIME]   e.g. scripts/netpol-denials.sh 2026-10-25T06:00:00Z
set -euo pipefail

since=()
[[ $# -ge 1 ]] && since=(--since-time="$1")

for p in $(kubectl -n kube-system get pods -l k8s-app=flannel -o name); do
  node=$(kubectl -n kube-system get "$p" -o jsonpath='{.spec.nodeName}')
  kubectl -n kube-system logs "$p" -c kube-network-policies "${since[@]}" | awk -v N="$node" '
    NR == 1 { first = $2 }
    /"Evaluating packet"/ && /direction="Ingress"/ {
      match($0, /id=[0-9]+/); id = substr($0, RSTART + 3, RLENGTH - 3)
      match($0, /srcPod="[^"]*"/); s = substr($0, RSTART, RLENGTH)
      match($0, /dstPod="[^"]*"/); last[id] = s " " substr($0, RSTART, RLENGTH)
      next
    }
    /Packet denied/ {
      match($0, /id=[0-9]+/); id = substr($0, RSTART + 3, RLENGTH - 3)
      c[last[id]]++; n++
    }
    END {
      print N ": " n + 0 " denials since " first
      for (k in c) print "  " c[k] "  " k
    }'
done
