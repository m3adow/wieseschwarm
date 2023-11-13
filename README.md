# Wieseschwarm

**Work in progress!**

Kubernetes manifests as well as some Talos Linux configuration manifests intended for a "home production ready" installation, including [Day 2 operations](https://codilime.com/blog/day-0-day-1-day-2-the-software-lifecycle-in-the-cloud-age/) tasks like Backups and keeping software, Helm Charts and images up to date.

For now, the project will use free SaaS offerings where applicable (e.g. for Metrics & Monitoring).

Intended infrastructure scope:

- [x] cert-manager
- [x] ~~Sealed Secrets~~ Replaced by SOPS
- [x] [OpenEBS Jiva](https://openebs.io/docs/concepts/jiva)
- [x] [ingress-nginx](https://kubernetes.github.io/ingress-nginx/)
- [x] MySQL Operator + Database _(may become MySQL Cluster in the future)_
- [x] [DB-Operator](https://github.com/kloeckner-i/db-operator)
- [x] [Composable Operator](https://github.com/composable-operator/composable)
- [x] [k8up](https://github.com/k8up-io/k8up)
- [x] [Flux](https://fluxcd.io/) _may be revisited in the future for image automation or notifications_
- [x] [Grafana-Agent](https://grafana.com/docs/grafana-cloud/kubernetes-monitoring/)
- [ ] [Renovate](https://docs.renovatebot.com/)

Extended infrastructure scope (applications considered for later):

- [ ] A better LB/VIP solution (may utilise MetalLB, PureLB, kube-vip or something like this)
- [ ] [External DNS](https://github.com/kubernetes-sigs/external-dns) _(Has to be tested more thoroughly later on)_
- [ ] [k8s_gateway](https://github.com/ori-edge/k8s_gateway)
- [ ] [Goldilocks](https://goldilocks.docs.fairwinds.com/)
- [ ] [Hajimari](https://github.com/toboshii/hajimari)
- [ ] [Reloader](https://github.com/stakater/Reloader)
- [ ] [Vertical Pod Autoscaler](https://github.com/kubernetes/autoscaler/tree/master/vertical-pod-autoscaler) & [Goldilocks](https://goldilocks.docs.fairwinds.com/#how-can-this-help-with-my-resource-settings)

Application scope (subject to change):

- [ ] [Nextcloud](https://nextcloud.com/)
- [ ] [Paperless NGX](https://github.com/paperless-ngx/paperless-ngx)
- [ ] [Vaultwarden](https://github.com/dani-garcia/vaultwarden)

If I ever have the time:

- [ ] [Firefox Sync](https://github.com/mozilla/fxa/)
      Other tasks:

- [x] Create Makefile for bootstrapping
- [ ] Add requests & limits to resources _(will be done later, potentially with VPA)_

## Bootstrapping & Development

The bootstrapping & development process have to be clean
