#!/usr/bin/env bash
# 99 — Delete the KIND cluster and clean local state.
set -euo pipefail

CLUSTER_NAME="${1:-aauth-demo}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if kind get clusters | grep -qx "${CLUSTER_NAME}"; then
  echo "==> Deleting KIND cluster '${CLUSTER_NAME}'"
  kind delete cluster --name "${CLUSTER_NAME}"
else
  echo "==> No cluster named '${CLUSTER_NAME}' — nothing to delete"
fi

# Don't auto-remove bootstrap tokens — operators may want them for forensics.
echo "==> (.bootstrap-tokens/ left in place; rm -rf manually if you want a clean slate)"
