#!/usr/bin/env bash
# 00 — Bring up a KIND cluster sized for the demo and install ingress-nginx.
set -euo pipefail

CLUSTER_NAME="${1:-aauth-demo}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if kind get clusters | grep -qx "${CLUSTER_NAME}"; then
  echo "==> KIND cluster '${CLUSTER_NAME}' already exists — skipping create"
else
  echo "==> Creating KIND cluster '${CLUSTER_NAME}'"
  kind create cluster --name "${CLUSTER_NAME}" --config "${ROOT}/kind/kind-cluster.yaml" --wait 120s
fi

kubectl config use-context "kind-${CLUSTER_NAME}"

echo "==> Installing ingress-nginx (kind preset)"
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/kind/deploy.yaml

echo "==> Waiting for ingress controller to be ready"
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=180s

echo "==> Creating namespaces"
kubectl apply -f "${ROOT}/manifests/00-namespaces.yaml"

echo "==> KIND cluster is up. Next: scripts/01-deploy-radiantlogic.sh (or 02-deploy-keycloak.sh)"
