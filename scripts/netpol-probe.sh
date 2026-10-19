#!/usr/bin/env bash
# Usage: scripts/netpol-probe.sh <expectations-file>...
set -euo pipefail

ns=netpol-probe
fail=0

# A previous run's namespace may still be terminating; pods cannot be created in it.
kubectl wait --for=delete "namespace/$ns" --timeout=120s >/dev/null 2>&1 || true
kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
trap 'kubectl delete namespace "$ns" --wait=false >/dev/null' EXIT

kubectl apply -f - >/dev/null <<EOF
---
apiVersion: v1
kind: Pod
metadata:
  name: probe
  namespace: $ns
spec:
  restartPolicy: Never
  # sleep as PID 1 ignores SIGTERM; without this the namespace takes 30 s to go away.
  terminationGracePeriodSeconds: 0
  securityContext:
    runAsNonRoot: true
    runAsUser: 65534
    runAsGroup: 65534
  containers:
    - name: probe
      image: busybox:1.37
      command: ["sleep", "3600"]
      resources:
        requests: {cpu: 10m, memory: 16Mi}
        limits: {memory: 32Mi}
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: {drop: ["ALL"]}
EOF
kubectl -n "$ns" wait --for=condition=Ready pod/probe --timeout=90s >/dev/null

while read -r expect host port _; do
  [[ -z "${expect:-}" || "$expect" == \#* ]] && continue
  got=DENY
  # Without NRI the engine can miss a fresh pod's first SYN; retry before calling it DENY.
  for _ in 1 2 3; do
    if kubectl -n "$ns" exec probe -- nc -z -w 3 "$host" "$port" >/dev/null 2>&1; then
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
