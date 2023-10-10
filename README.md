# Wieseschwarm

**Work in progress!**

Kubernetes manifests as well as some Talos Linux configuration manifests intended for a "home production ready" installation, including [Day 2 operations](https://codilime.com/blog/day-0-day-1-day-2-the-software-lifecycle-in-the-cloud-age/) tasks like Backups and keeping software, Helm Charts and images up to date.

For now, the project will use free SaaS offerings where applicable (e.g. for Metrics & Monitoring).

Intended infrastructure scope:

- [x] cert-manager
- [x] Sealed Secrets
- [ ] MySQL Operator + Database _(may become MySQL Cluster in the future)_
- [ ] [DB-Operator](https://github.com/kloeckner-i/db-operator)
- [x] [Composable Operator](https://github.com/composable-operator/composable)
- [ ] [k8up](https://github.com/k8up-io/k8up)
- [x] [Flux](https://fluxcd.io/) _may be revisited in the future for image automation or notifications_
- [ ] [External DNS](https://github.com/kubernetes-sigs/external-dns) _(Has to be tested more thoroughly lateron)_
- [ ] [Grafana-Agent-Operator](https://grafana.com/docs/grafana-cloud/kubernetes-monitoring/) (better alert config solution may be required in the future)
- [ ] [Renovate](https://docs.renovatebot.com/)

Extended infrastructure scope (applications considered for later):

- [ ] [ingress-nginx](https://kubernetes.github.io/ingress-nginx/)
- [ ] [k8s_gateway](https://github.com/ori-edge/k8s_gateway)
- [ ] [Goldilocks](https://goldilocks.docs.fairwinds.com/)
- [ ] [Hajimari](https://github.com/toboshii/hajimari)
- [ ] [Reloader](https://github.com/stakater/Reloader)
- [ ] Better Grafana Cloud alerting solution
- [ ] [Vertical Pod Autoscaler](https://github.com/kubernetes/autoscaler/tree/master/vertical-pod-autoscaler) & [Goldilocks](https://goldilocks.docs.fairwinds.com/#how-can-this-help-with-my-resource-settings)

Application scope (subject to change):

- [ ] [Nextcloud](https://nextcloud.com/) _(Basic installation stuff done, customization WIP)_
- [ ] [Nitter](https://github.com/zedeus/nitter)
- [ ] [Firefox Sync](https://github.com/mozilla/fxa/)
- [ ] [Paperless NGX](https://github.com/paperless-ngx/paperless-ngx)
- [ ] [Vaultwarden](https://github.com/dani-garcia/vaultwarden)

Other tasks:

- [x] Create Makefile for bootstrapping
- [ ] Add requests & limits to resources _(will be done later, potentially with VPA)_

## Development

Development is done via [k3d](https://k3d.io/). Persistent data (volumes) will be written to `${K3D_DIR}` if set or `/tmp/` otherwise. I also recommend to set ACLs for the `volumes` folder:

```bash
setfacl -Rdm ${USER}:rwx ${K3D_DIR}
```

This will prevent permission problems when `clean`ing the development environment.

- To create a new cluster on a fresh system, run `make develop` or `make new`
- To tear down the development cluster, run `make clean`
- To recreate a cluster run `make new`
- Starting/Stopping clusters can be done with `make start`/`make stop`
