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

echo "==> Submitting mission via backend"
RESP="$(curl -sf -X POST http://localhost:8000/optimization/start \
  -H "Content-Type: application/json" \
  -d '{"scenario":"laptop_supply_chain","custom_prompt":"optimize supply chain","parameters":{"sku":"WIDGET-1","region":"us-east"}}')"

REQUEST_ID="$(echo "${RESP}" | jq -r '.request_id')"
echo "    request_id=${REQUEST_ID}"

echo ""
echo "==> Optimization state (from backend):"
for _ in $(seq 1 30); do
  STATE="$(curl -sf "http://localhost:8000/optimization/progress/${REQUEST_ID}")"
  STATUS="$(echo "${STATE}" | jq -r '.status')"
  if [[ "${STATUS}" == "completed" || "${STATUS}" == "failed" ]]; then
    echo "${STATE}" | jq .
    break
  fi
  sleep 2
done

echo ""
echo "==> Registered agents:"
kubectl -n "${PLATFORM_NS}" exec deploy/registry-service -- \
  curl -sf "http://localhost:9000/v1/agents" | jq .

echo ""
echo "==> Tail backend logs (Ctrl-C to exit)"
kubectl -n "${APPS_NS}" logs -f deploy/backend --tail=30
