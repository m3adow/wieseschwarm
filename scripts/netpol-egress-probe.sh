#!/usr/bin/env bash
# Usage: scripts/netpol-egress-probe.sh <namespace> <pod-label-selector> <expectations-file>...
# Probes from inside a running pod via an ephemeral container, so the pod's own egress policies
# apply. Each run leaves that container in the pod spec until the pod restarts (harmless).
set -euo pipefail

ns=$1
selector=$2
shift 2

pod=$(kubectl -n "$ns" get pods -l "$selector" --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}')
[[ -n "$pod" ]] || { echo "no running pod matches $selector in $ns" >&2; exit 2; }

name="egress-probe-$(date +%s)"
custom=$(mktemp)
trap 'rm -f "$custom"' EXIT
echo '{"securityContext": {"runAsUser": 65534, "runAsGroup": 65534}}' > "$custom"
if ! err=$(kubectl debug -n "$ns" "$pod" -q --profile=restricted --custom="$custom" \
  --image=busybox:1.37 -c "$name" -- sleep 900 2>&1 >/dev/null); then
  echo "$err" >&2
  exit 2
fi

state=""
for _ in $(seq 1 30); do
  state=$(kubectl -n "$ns" get pod "$pod" \
    -o jsonpath="{.status.ephemeralContainerStatuses[?(@.name==\"$name\")].state}")
  [[ "$state" == *running* ]] && break
  sleep 2
done
[[ "$state" == *running* ]] || { echo "ephemeral container $name is not running: $state" >&2; exit 2; }

fail=0
while read -r expect host port _; do
  [[ -z "${expect:-}" || "$expect" == \#* ]] && continue
  got=DENY
  for _ in 1 2 3; do
    if kubectl -n "$ns" exec "$pod" -c "$name" -- nc -z -w 3 "$host" "$port" >/dev/null 2>&1; then
      got=ALLOW
      break
    fi
  done
  if [[ "$got" == "$expect" ]]; then
    echo "PASS $expect $host:$port"
  else
    echo "FAIL $expect $host:$port (got $got)"
    fail=1
  fi
done < <(cat "$@")

exit "$fail"
