#!/usr/bin/env bash
# 04 — Deploy agentgateway and seed its policy from the registry.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${GW_NS:-gateway}"

echo "==> [04] Applying agentgateway manifests"
kubectl apply -f "${ROOT}/manifests/agentgateway/"

echo "==> Waiting for agentgateway"
kubectl -n "${NS}" rollout status deploy/agentgateway --timeout=180s

echo "==> Seeding initial policy from the platform registry"
# The registry exposes a /v1/policy/render endpoint that returns the current
# agentgateway-flavored policy. We patch it into the policy ConfigMap.
RENDERED="$(mktemp)"
kubectl -n "${PLATFORM_NS:-platform}" exec deploy/registry-service -- \
  curl -sf http://localhost:9000/v1/policy/render > "${RENDERED}"

kubectl -n "${NS}" create configmap agentgateway-policy \
  --from-file=policy.yaml="${RENDERED}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Trigger reload (agentgateway watches the ConfigMap; restart for safety in v1).
kubectl -n "${NS}" rollout restart deploy/agentgateway
kubectl -n "${NS}" rollout status  deploy/agentgateway --timeout=120s

rm -f "${RENDERED}"
echo "==> agentgateway up. Next: scripts/05-register-agents.sh"
