SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

# IDP toggle: set USE_KEYCLOAK=1 to fall back to Keycloak.
USE_KEYCLOAK ?= 0

CLUSTER_NAME ?= aauth-demo
PLATFORM_NS  := platform
IDP_NS       := idp
APPS_NS      := apps
GW_NS        := gateway

.PHONY: help demo kind-up radiantlogic keycloak idp platform gateway register apps run teardown clean revoke-agent revoke-mission console console-local

help:
	@echo "AAuth Mission Platform — KIND demo"
	@echo ""
	@echo "Targets:"
	@echo "  make demo                  Run the full demo (default IDP = RadiantLogic)"
	@echo "  make USE_KEYCLOAK=1 demo   Run the full demo with Keycloak as fallback IDP"
	@echo ""
	@echo "  make kind-up               00 — bring up the KIND cluster"
	@echo "  make idp                   01 or 02 — deploy the selected IDP"
	@echo "  make platform              03 — deploy mission + registry services"
	@echo "  make gateway               04 — deploy agentgateway"
	@echo "  make register              05 — operator pre-registers all agents"
	@echo "  make apps                  06 — deploy backend, SCA, MAA, frontend"
	@echo "  make run                   07 — drive an end-to-end mission and tail logs"
	@echo "  make teardown              99 — delete the KIND cluster"
	@echo ""
	@echo "  make revoke-agent ID=...   Revoke a registered agent at the registry"
	@echo "  make revoke-mission ID=... Revoke an in-flight mission"
	@echo ""
	@echo "  make console               Open the in-cluster operator console URL"
	@echo "  make console-local         Run the console locally against host-port-forwarded services (no K8s required)"

demo: kind-up idp platform gateway register apps run

kind-up:
	@scripts/00-kind-up.sh "$(CLUSTER_NAME)"

idp:
ifeq ($(USE_KEYCLOAK),1)
	@scripts/02-deploy-keycloak.sh
else
	@scripts/01-deploy-radiantlogic.sh
endif

radiantlogic:
	@scripts/01-deploy-radiantlogic.sh

keycloak:
	@scripts/02-deploy-keycloak.sh

platform:
	@scripts/03-deploy-platform.sh

gateway:
	@scripts/04-deploy-agentgateway.sh

register:
	@scripts/05-register-agents.sh

apps:
	@scripts/06-deploy-apps.sh

run:
	@scripts/07-run-demo.sh

teardown:
	@scripts/99-teardown.sh "$(CLUSTER_NAME)"

clean: teardown

revoke-agent:
	@test -n "$(ID)" || (echo "usage: make revoke-agent ID=<agent_id>"; exit 1)
	@kubectl -n $(PLATFORM_NS) exec deploy/registry-service -- curl -sf -X DELETE http://localhost:9000/v1/agents/$(ID)
	@echo "agent $(ID) revoked — agentgateway policy updated"

revoke-mission:
	@test -n "$(ID)" || (echo "usage: make revoke-mission ID=<mission_id>"; exit 1)
	@kubectl -n $(PLATFORM_NS) exec deploy/mission-service -- curl -sf -X POST http://localhost:9001/v1/missions/$(ID)/revoke
	@echo "mission $(ID) revoked — agentgateway will deny any in-flight call with this mission_id"

# Open the in-cluster operator console (mapped to host port 9002 by KIND).
console:
	@echo ""
	@echo "Operator console:    http://localhost:9002"
	@echo "Default credentials: operator / aauth-operator-demo (override with OPERATOR_USERNAME / OPERATOR_PASSWORD env on the platform services)"
	@command -v xdg-open >/dev/null && xdg-open http://localhost:9002 \
		|| command -v open >/dev/null && open http://localhost:9002 \
		|| true

# Run the console locally without K8s. Requires that registry-service and
# mission-service are reachable at localhost:9000 / 9001 — either via
# `kubectl port-forward`, or via the in-process standalone harness, or via
# both services running locally with `uvicorn`.
console-local:
	@cd platform/operator-console && python3 server.py
