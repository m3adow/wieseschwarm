# Wieseschwarm

**Work in progress!**

Kubernetes manifests as well as some Talos Linux configuration manifests intended for a "home production ready" installation, including [Day 2 operations](https://codilime.com/blog/day-0-day-1-day-2-the-software-lifecycle-in-the-cloud-age/) tasks like Backups and keeping software, Helm Charts and images up to date.

For now, the project will use free SaaS offerings where applicable (e.g. for Metrics & Monitoring).

### Current Infrastructure

- [x] [ArgoCD](https://argo-cd.readthedocs.io/)
- [x] [Piraeus](https://piraeus.io/)
- [x] [sops-secret-operator](https://github.com/isindir/sops-secrets-operator)
- [x] [traefik Ingress](https://traefik.io/traefik/)
- [x] [MetalLB](https://metallb.io/)
- [x] [cert-manager](https://cert-manager.io/)
- [x] [k8up](https://github.com/k8up-io/k8up)
- [x] [MariaDB Operator](https://github.com/mariadb-operator/mariadb-operator) (Galera cluster + MaxScale)
- [x] [Grafana Alloy](https://grafana.com/docs/grafana-cloud/monitor-infrastructure/kubernetes-monitoring/) (Grafana Cloud monitoring)
- [x] [Metrics Server](https://github.com/kubernetes-sigs/metrics-server)
- [x] [Vertical Pod Autoscaler](https://github.com/kubernetes/autoscaler/tree/master/vertical-pod-autoscaler)
- [x] [Reloader](https://github.com/stakater/Reloader)
- [x] [Reflector](https://github.com/emberstack/kubernetes-reflector)
- [x] [cloudflared](https://github.com/cloudflare/cloudflared) (Cloudflare Tunnel for public exposure)
- [x] [External DNS](https://github.com/kubernetes-sigs/external-dns)
