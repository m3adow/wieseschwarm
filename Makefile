SHELL = /bin/bash

.PHONY: apply
apply:
	# First apply may fail if CRDs are not being applied in time
	- kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets | kubectl apply -f-
	kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets | kubectl apply -f-

.PHONY: plan
plan:
	kubectl kustomize --enable-helm kubernetes/00_init/overlays/init-secrets
