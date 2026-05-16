#!/usr/bin/env bash
# 03 — Build and deploy the platform services (registry, mission).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${PLATFORM_NS:-platform}"
CLUSTER_NAME="${CLUSTER_NAME:-aauth-demo}"

echo "==> [03] Building platform images"
docker build -t aauth/registry-service:dev "${ROOT}/platform/registry-service"
docker build -t aauth/mission-service:dev  "${ROOT}/platform/mission-service"

echo "==> Loading images into KIND"
kind load docker-image aauth/registry-service:dev --name "${CLUSTER_NAME}"
kind load docker-image aauth/mission-service:dev  --name "${CLUSTER_NAME}"

echo "==> Applying platform manifests"
kubectl apply -f "${ROOT}/manifests/platform/"

echo "==> Waiting for platform services"
kubectl -n "${NS}" rollout status deploy/registry-service --timeout=180s
kubectl -n "${NS}" rollout status deploy/mission-service  --timeout=180s

# Confirm the platform-config ConfigMap from step 01/02 is mounted.
kubectl -n "${NS}" exec deploy/registry-service -- env | grep -E '^IDP_' || {
  echo "ERROR: platform-config ConfigMap missing — run 01-deploy-radiantlogic.sh or 02-deploy-keycloak.sh first."
  exit 1
}

echo "==> Platform up. Next: scripts/04-deploy-agentgateway.sh"
echo "    registry-service: http://localhost:9000"
echo "    mission-service:  http://localhost:9001"
