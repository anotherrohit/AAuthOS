#!/usr/bin/env bash
# 05 — Operator-side: pre-register backend, supply-chain, market-analysis.
#
# Output is a tokens file (gitignored) that the next script consumes when
# building agent images. In a real deploy these would be hand-delivered to
# each developer; here we automate it because there's only one human.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="${PLATFORM_NS:-platform}"
TOKENS_DIR="${ROOT}/.bootstrap-tokens"
mkdir -p "${TOKENS_DIR}"

register() {
  local name="$1"
  local team="$2"
  local allows="$3"
  local depth="$4"

  echo "==> Registering ${name}"
  local body
  body="$(jq -n --arg n "${name}" --arg t "${team}" --arg a "${allows}" --argjson d "${depth}" '{
    display_name: $n,
    owning_team:  $t,
    allowed_downstream_agents: ($a | split(",") | map(select(. != ""))),
    max_delegation_depth: $d,
    allowed_signature_schemes: ["jwks"]
  }')"

  local resp
  resp="$(kubectl -n "${NS}" exec deploy/registry-service -- \
    curl -sf -X POST http://localhost:9000/v1/agents \
    -H 'Content-Type: application/json' \
    -d "${body}")"

   echo "${resp}" | jq -r '.bootstrap_token' | tr -d '\r' > "${TOKENS_DIR}/${name}.token"
  echo "${resp}" | jq -r '.agent_id_url'    | tr -d '\r' > "${TOKENS_DIR}/${name}.id_url"
  echo "    ${name} → $(cat "${TOKENS_DIR}/${name}.id_url")"
}

# Note: registration order matters — downstream agents must be registered
# before upstream agents that reference them, so that allowed_downstream_agents
# resolves cleanly (see P0-1 acceptance criteria in the PRD).
register "market-analysis" "commerce" ""                              1
register "supply-chain"    "commerce" "market-analysis"               2
register "backend"         "platform" "supply-chain"                  3

echo "==> All agents pre-registered."
echo "    Bootstrap tokens written to ${TOKENS_DIR} (gitignored)."
echo "    Next: scripts/06-deploy-apps.sh"
