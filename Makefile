.PHONY: up down demo build test lint help
SHELL := /usr/bin/env bash

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n",$$1,$$2}'

up: ## Bring up the whole platform on a local kind cluster
	./scripts/bootstrap.sh

down: ## Tear down the cluster and registry
	./scripts/teardown.sh

build: ## Build + push the app images to the local registry
	./scripts/build-load-images.sh

demo: ## Print the URLs
	@echo "Dashboard : http://dashboard.127.0.0.1.nip.io"
	@echo "ArgoCD    : http://argocd.127.0.0.1.nip.io"
	@echo "Grafana   : http://grafana.127.0.0.1.nip.io"
	@echo "Gitea     : http://gitea.127.0.0.1.nip.io"

test: ## Run the core-loop test + helm lint + frontend build
	python3 -m pytest tests/ -q
	helm lint charts/ai-service
	helm template charts/ai-service >/dev/null
	cd apps/platform-frontend && npm run build

lint: ## Lint the helm chart
	helm lint charts/ai-service
