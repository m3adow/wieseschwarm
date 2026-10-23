#!/usr/bin/env bash
# Lists namespaces running pod-network pods without a default-deny-ingress policy.
set -euo pipefail

exempt='^(kube-system|default|argocd|netpol-probe|netpol-storage-check)$'

with_pods=$(kubectl get pods -A -o json \
  | jq -r '.items[] | select(.spec.hostNetwork != true) | .metadata.namespace' | sort -u)
covered=$(kubectl get networkpolicy -A -o json \
  | jq -r '.items[] | select(.metadata.name == "default-deny-ingress") | .metadata.namespace' | sort -u)

missing=$(comm -23 <(echo "$with_pods") <(echo "$covered") | grep -vE "$exempt" || true)
if [[ -n "$missing" ]]; then
  echo "Namespaces without default-deny-ingress:"
  echo "$missing"
  exit 1
fi
echo "All namespaces covered."
