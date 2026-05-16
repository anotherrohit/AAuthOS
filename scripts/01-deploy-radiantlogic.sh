#!/usr/bin/env bash
# 01 — Deploy RadiantLogic IDM as the primary IDP.
#
# Requires a RadiantLogic IDM v8.1 license + container image. See
# manifests/radiantlogic/README.md for license retrieval steps.
#
# This script:
#   1. creates the radiantlogic pull secret if RL_REGISTRY_USER/PASS are set
#   2. installs the RL Helm chart with the demo values
#   3. waits for the OIDC discovery endpoint to come up
#   4. applies the federated-IdP + RFC 8693 token-exchange config
#   5. writes the platform-config ConfigMap with IDP_ISSUER_URL set to RL
#
# Adapted from anotherrohit/spiffe-radiantlogic/scripts/02-deploy-radiantlogic.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${IDP_NS:-idp}"
RL_CHART="${RL_CHART:-radiantlogic/idm}"
RL_VERSION="${RL_VERSION:-8.1.4}"
RL_VALUES="${ROOT}/manifests/radiantlogic/01-radiantlogic-values.yaml"
RL_REALM_CFG="${ROOT}/manifests/radiantlogic/02-token-exchange-config.yaml"

echo "==> [01] Deploying RadiantLogic IDM into ns/${NS}"

# (Optional) image pull secret for RL's private registry.
if [[ -n "${RL_REGISTRY_USER:-}" && -n "${RL_REGISTRY_PASS:-}" ]]; then
  kubectl -n "${NS}" create secret docker-registry rl-registry \
    --docker-server="${RL_REGISTRY_SERVER:-registry.radiantlogic.com}" \
    --docker-username="${RL_REGISTRY_USER}" \
    --docker-password="${RL_REGISTRY_PASS}" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "    (no RL_REGISTRY_USER/PASS set — assuming image is already in-cluster or anonymous)"
fi

helm repo add radiantlogic https://helm.radiantlogic.com 2>/dev/null || true
helm repo update radiantlogic >/dev/null

echo "==> Installing/upgrading RL Helm release"
helm upgrade --install radiantlogic "${RL_CHART}" \
  --namespace "${NS}" \
  --version "${RL_VERSION}" \
  --values "${RL_VALUES}" \
  --wait \
  --timeout 10m

echo "==> Waiting for RL OIDC discovery endpoint"
kubectl -n "${NS}" wait --for=condition=Available deployment/radiantlogic --timeout=10m

# Port-forward briefly to validate discovery; this catches misconfig early.
echo "==> Validating /.well-known/openid-configuration"
kubectl -n "${NS}" port-forward svc/radiantlogic 18080:8080 >/dev/null 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 3
curl -sf http://127.0.0.1:18080/.well-known/openid-configuration >/dev/null || {
  echo "ERROR: RL OIDC discovery is not reachable. Check pod logs:"
  echo "  kubectl -n ${NS} logs deploy/radiantlogic"
  exit 1
}

echo "==> Applying RL realm + token-exchange config"
kubectl -n "${NS}" apply -f "${RL_REALM_CFG}"

# Trigger RL to reload the federated-IdP config. In a real deploy this is an
# admin-console action; in the demo we POST to the management endpoint.
kubectl -n "${NS}" exec deploy/radiantlogic -- /opt/radiantlogic/bin/reload-config.sh \
  || echo "    (reload script not present in this image — apply config via admin console)"

echo "==> Writing platform-config ConfigMap (IDP = RadiantLogic)"
kubectl -n "${PLATFORM_NS:-platform}" create configmap platform-config \
  --from-literal=IDP_FLAVOR=radiantlogic \
  --from-literal=IDP_ISSUER_URL=https://radiantlogic.idp.svc.cluster.local:8443 \
  --from-literal=IDP_TOKEN_EXCHANGE_URL=https://radiantlogic.idp.svc.cluster.local:8443/oauth2/token \
  --from-literal=IDP_JWKS_URL=https://radiantlogic.idp.svc.cluster.local:8443/oauth2/jwks \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> RadiantLogic deployed. Next: scripts/03-deploy-platform.sh"
