#!/usr/bin/env bash
# Attributes kube-network-policies denials to source/destination pods, per node.
# A "Packet denied" line carries only an id; ids repeat within a log window, so each denial is
# paired with the most recent evaluation of the same id in its direction (ingress or egress).
# Usage: scripts/netpol-denials.sh [SINCE_TIME]   e.g. scripts/netpol-denials.sh 2026-10-25T06:00:00Z
set -euo pipefail

since=()
[[ $# -ge 1 ]] && since=(--since-time="$1")

for p in $(kubectl -n kube-system get pods -l k8s-app=flannel -o name); do
  node=$(kubectl -n kube-system get "$p" -o jsonpath='{.spec.nodeName}')
  kubectl -n kube-system logs "$p" -c kube-network-policies "${since[@]}" | awk -v N="$node" '
    NR == 1 { first = $2 }
    /"Evaluating packet"/ {
      match($0, /id=[0-9]+/); id = substr($0, RSTART + 3, RLENGTH - 3)
      match($0, /direction="[A-Za-z]+"/); dir = tolower(substr($0, RSTART + 11, RLENGTH - 12))
      match($0, /srcPod="[^"]*"/); s = substr($0, RSTART, RLENGTH)
      match($0, /dstPod="[^"]*"/); ev[id, dir] = s " " substr($0, RSTART, RLENGTH)
      pending = id SUBSEP dir
      next
    }
    pending != "" && /^[ \t]*\[[0-9]+\]/ { split($0, f, " "); dst[pending] = f[3]; pending = ""; next }
    /Packet denied by (ingress|egress) policy/ {
      match($0, /id=[0-9]+/); id = substr($0, RSTART + 3, RLENGTH - 3)
      dir = ($0 ~ /by egress policy/) ? "egress" : "ingress"
      k = dir " " ev[id, dir]
      if (dir == "egress") k = k " dst=" dst[id, dir]
      c[k]++; n++
    }
    END {
      print N ": " n + 0 " denials since " first
      for (k in c) print "  " c[k] "  " k
    }'
done
