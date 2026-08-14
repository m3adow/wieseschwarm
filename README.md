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

## Grafana Cloud requirements

Metrics, logs and alert evaluation run in a free-tier [Grafana Cloud](https://grafana.com/products/cloud/) stack. None of this can be managed from this repo, so a fresh install needs it set up by hand:

| Requirement                                             | Used for                                                                   |
| ------------------------------------------------------- | -------------------------------------------------------------------------- |
| A stack with Prometheus (Mimir) and Loki endpoints      | Alloy pushes metrics, logs and cluster events                              |
| Access policy token with `metrics:write` + `logs:write` | the push credentials, stored as the `grafana-cloud-credentials` SopsSecret |
| Access policy token with `alerts:read` + `alerts:write` | writing the Cloud Alertmanager config                                      |
| A configured **Cloud Alertmanager**                     | notification delivery — alert rules on their own deliver nothing           |

Alert rules themselves _are_ git-managed, in `kubernetes/01_infrastructure/grafana-alloy/cluster-rules/`. Alloy syncs them to the Mimir ruler, which evaluates them server-side so alerts keep working during a full cluster outage.

### Wiring up notifications

The ruler dispatches firing alerts to the **Cloud Alertmanager**, which is configured separately from everything else:

1. Create a Telegram bot with `@BotFather` and note the token. Send the bot a message, then read the numeric chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates` (only retains updates for 24h, so the message must be recent).
2. In the Grafana Cloud portal, create the `alerts:read` + `alerts:write` access policy token.
3. Write an Alertmanager YAML config with your receivers and route tree, then upload it:

   ```bash
   curl -s -X POST -u "<alertmanager-instance-id>:$AM_TOKEN" \
     --data-binary @am-config.yaml \
     https://<alertmanager-endpoint>/api/v1/alerts
   ```

4. Verify with a `GET` on the same URL. `alertmanager_config: ""` means nothing is configured and Mimir silently discards every alert.

> **Do not configure contact points under Alerting → Contact points in the Grafana UI.** That is a second, independent Alertmanager which never receives these alerts, and its **Test** button passes anyway because it bypasses Alertmanager entirely. This combination is very easy to lose hours to.

This cluster's concrete endpoint, instance ID and current routing are recorded in [`kubernetes/01_infrastructure/grafana-alloy/CLAUDE.md`](kubernetes/01_infrastructure/grafana-alloy/CLAUDE.md).
