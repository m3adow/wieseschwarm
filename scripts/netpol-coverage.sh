#!/usr/bin/env bash
# Lists namespaces with pod-network pods but no default-deny-ingress/-egress policy.
set -euo pipefail

exempt='^(kube-system|default|argocd|netpol-probe|netpol-storage-check)$'
# Infra namespaces stay egress-open; only app namespaces restrict egress.
egress_exempt='^(kube-system|default|argocd|cert-manager|cloudflared|external-dns|k8up|mariadb|mariadb-operator|metallb-system|metrics-server|monitoring|piraeus-datastore|reflector|reloader|sops-secrets-operator|traefik|vpa|netpol-probe|netpol-storage-check)$'

with_pods=$(kubectl get pods -A -o json \
  | jq -r '.items[] | select(.spec.hostNetwork != true) | .metadata.namespace' | sort -u)
policies=$(kubectl get networkpolicy -A -o json)

status=0
report() {
  local policy=$1 skip=$2 covered missing
  covered=$(jq -r --arg p "$policy" \
    '.items[] | select(.metadata.name == $p) | .metadata.namespace' <<<"$policies" | sort -u)
  missing=$(comm -23 <(echo "$with_pods") <(echo "$covered") | grep -vE "$skip" || true)
  if [[ -n "$missing" ]]; then
    echo "Namespaces without $policy:"
    echo "$missing"
    status=1
  fi
}

report default-deny-ingress "$exempt"
report default-deny-egress "$egress_exempt"
if [[ $status -eq 0 ]]; then
  echo "All namespaces covered."
fi
exit $status
