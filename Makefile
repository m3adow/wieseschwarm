SHELL = /bin/bash
KUBECONFIG_DEV = /tmp/kubeconfig.dev
TALOSCONFIG_STATE_DIR = /tmp/talosconfig-dev
TALOSCONFIG_DEV = /tmp/talosconfig.dev
TALOSCTL_DEV = talosctl --talosconfig $(TALOSCONFIG_DEV)

.PHONY: apply
apply:
	# First apply may fail if CRDs are not being applied in time
	- kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets | kubectl apply -f-
	kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets | kubectl apply -f-

.PHONY: plan
plan:
	kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets

.PHONY: argocd-bootstrap
argocd-bootstrap:
	kubectl kustomize kubernetes/00_bootstrap/argocd | kubectl apply -f-
	kubectl -n argocd wait deployment argocd-server --for=condition=Available --timeout=300s
	$(MAKE) argocd-repo-configure
	@echo "ArgoCD ready. Initial admin password:"
	@kubectl -n argocd get secret argocd-initial-admin-secret \
		-o jsonpath='{.data.password}' | base64 -d && echo

.PHONY: argocd-repo-configure
argocd-repo-configure:
	kubectl create -n argocd secret generic repo-wieseschwarm \
		--from-literal=type=git \
		--from-literal=url=git@github.com:m3adow/wieseschwarm.git \
		--from-file=sshPrivateKey=kubernetes/secret/argocd-deploy-key \
		--save-config --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n argocd label secret repo-wieseschwarm \
		argocd.argoproj.io/secret-type=repository --overwrite

.PHONY: argocd-apps-bootstrap
argocd-apps-bootstrap:
	kubectl apply -f kubernetes/apps-of-apps.yaml
	@echo "Root App of Apps applied. ArgoCD will sync all child Applications."

.PHONY: argocd-password
argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret \
		-o jsonpath='{.data.password}' | base64 -d && echo

.PHONY: sops-bootstrap
sops-bootstrap:
	@AGE_KEY_FILE=$${SOPS_AGE_KEY_FILE:-$(HOME)/.config/sops/age/keys.txt}; \
	if [ ! -f "$$AGE_KEY_FILE" ]; then \
		echo "Error: age key not found at $$AGE_KEY_FILE. Set SOPS_AGE_KEY_FILE to override."; \
		exit 1; \
	fi; \
	kubectl create -n sops-secrets-operator secret generic sops-age-key \
		--from-file=keys.txt=$$AGE_KEY_FILE \
		--save-config --dry-run=client -o yaml | kubectl apply -f -


dev-talosctl dev-cluster-create dev-cluster-destroy dev-cluster-new: export KUBECONFIG := $(KUBECONFIG_DEV)

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
