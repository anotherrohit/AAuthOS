#!/usr/bin/env bash
# 02 — Deploy Keycloak as the fallback IDP (USE_KEYCLOAK=1 path).
#
# Same shape as 01-deploy-radiantlogic.sh — different image, same federation
# target (the platform's /v1/agents/jwks.json) and same RFC 8693 endpoint
# semantics.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${IDP_NS:-idp}"
KC_CHART="${KC_CHART:-bitnami/keycloak}"
KC_VERSION="${KC_VERSION:-21.6.1}"
KC_VALUES="${ROOT}/manifests/keycloak/01-keycloak-values.yaml"
KC_REALM_IMPORT="${ROOT}/manifests/keycloak/02-keycloak-realm-import.yaml"

echo "==> [02] Deploying Keycloak into ns/${NS}"

helm repo add bitnami https://charts.bitnami.com/bitnami 2>/dev/null || true
helm repo update bitnami >/dev/null

# Import the demo realm as a ConfigMap so Keycloak can pick it up at startup.
kubectl -n "${NS}" apply -f "${KC_REALM_IMPORT}"

helm upgrade --install keycloak "${KC_CHART}" \
  --namespace "${NS}" \
  --version "${KC_VERSION}" \
  --values "${KC_VALUES}" \
  --wait \
  --timeout 10m

echo "==> Waiting for Keycloak to be ready"
kubectl -n "${NS}" rollout status statefulset/keycloak --timeout=10m


# Sanity-check the realm import.
kubectl -n "${NS}" port-forward svc/keycloak 18080:80 >/dev/null 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 3
curl -sf http://127.0.0.1:18080/realms/aauth/.well-known/openid-configuration >/dev/null || {
  echo "ERROR: Keycloak realm 'aauth' not found. Check the realm import ConfigMap."
  exit 1
}

echo "==> Writing platform-config ConfigMap (IDP = Keycloak)"
kubectl -n "${PLATFORM_NS:-platform}" create configmap platform-config \
  --from-literal=IDP_FLAVOR=keycloak \
  --from-literal=IDP_ISSUER_URL=http://keycloak.idp.svc.cluster.local/realms/aauth \
  --from-literal=IDP_TOKEN_EXCHANGE_URL=http://keycloak.idp.svc.cluster.local/realms/aauth/protocol/openid-connect/token \
  --from-literal=IDP_JWKS_URL=http://keycloak.idp.svc.cluster.local/realms/aauth/protocol/openid-connect/certs \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Keycloak deployed. Next: scripts/03-deploy-platform.sh"
