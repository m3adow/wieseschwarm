SHELL = /bin/bash
KUBECONFIG_DEV = /tmp/kubeconfig.dev
TALOSCONFIG_STATE_DIR = /tmp/talosconfig-dev
TALOSCONFIG_DEV = /tmp/talosconfig.dev
TALOSCTL_DEV = talosctl --talosconfig $(TALOSCONFIG_DEV)

export KUBECONFIG := $(KUBECONFIG_DEV)

.PHONY: apply
apply:
	# First apply may fail if CRDs are not being applied in time
	- kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets | kubectl apply -f-
	kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets | kubectl apply -f-

.PHONY: plan
plan:
	kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets


.PHONY: dev-talosctl
dev-talosctl:
	@echo "$(TALOSCTL_DEV)"

.PHONY: dev-cluster-create
dev-cluster-create:
	@if ! lsmod | grep -q br_netfilter; then \
		echo "br_netfilter module has to be loaded..."; \
		sudo modprobe br_netfilter; \
	fi
	talosctl cluster create docker \
		--talosconfig-destination $(TALOSCONFIG_DEV) \
		--state $(TALOSCONFIG_STATE_DIR) \
		--config-patch-controlplanes @talos/dev/controlplane-patch.yaml \
		--config-patch @talos/piraeus-patch.yaml \
	&& NODE_IP=$$(talosctl cluster show --state $(TALOSCONFIG_STATE_DIR) 2>/dev/null | awk '/controlplane/{print $$3; exit}') \
	&& printf "Dev cluster up.\nNode IP: $$NODE_IP\nUse: $(TALOSCTL_DEV) --nodes=$$NODE_IP <command>\nOr export: TALOSCONFIG=$(TALOSCONFIG_DEV) KUBECONFIG=$(KUBECONFIG_DEV)\n"

.PHONY: dev-cluster-destroy
dev-cluster-destroy:
	- talosctl cluster destroy \
		--state $(TALOSCONFIG_STATE_DIR)
	docker rm -f talos-default-worker-1 talos-default-controlplane-1
	rm -f $(KUBECONFIG_DEV) $(TALOSCONFIG_DEV)
	rm -rf $(TALOSCONFIG_STATE_DIR)

.PHONY: dev-cluster-new
dev-cluster-new: dev-cluster-destroy dev-cluster-create
