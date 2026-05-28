#!/usr/bin/env bash
# 07 — Drive a user-delegated mission end-to-end and tail mission logs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM_NS="${PLATFORM_NS:-platform}"
APPS_NS="${APPS_NS:-apps}"

echo "==> [07] Running end-to-end demo"
echo ""
echo "    The intended user flow is:"
echo "      1. Open http://localhost:3000 in a browser"
echo "      2. Log in with the demo user (see docs/INSTALL.md)"
echo "      3. Submit a 'optimize supply chain' request"
echo ""
echo "    We'll skip the browser and POST directly to the backend instead, then"
echo "    show the resulting mission state."
echo ""

# Backend exposes a dev-only token endpoint for the demo so we can skip OIDC.
# In production this path doesn't exist.
DEV_TOKEN="$(curl -sf -X POST http://localhost:8000/dev/login \
  -d '{"username":"demo-user"}' \
  -H 'Content-Type: application/json' \
  | jq -r '.access_token')"

echo "==> Submitting mission via backend"
RESP="$(curl -sf -X POST http://localhost:8000/v1/optimize \
  -H "Authorization: Bearer ${DEV_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"sku":"WIDGET-1","region":"us-east"}')"

MISSION_ID="$(echo "${RESP}" | jq -r '.mission_id')"
echo "    mission_id=${MISSION_ID}"

echo ""
echo "==> Mission state (from mission-service):"
kubectl -n "${PLATFORM_NS}" exec deploy/mission-service -- \
  curl -sf "http://localhost:9001/v1/missions/${MISSION_ID}" | jq .

echo ""
echo "==> Tail mission service logs (Ctrl-C to exit)"
echo "    Or try: make revoke-mission ID=${MISSION_ID}"
kubectl -n "${PLATFORM_NS}" logs -f deploy/mission-service --tail=30
