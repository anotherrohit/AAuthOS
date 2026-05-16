# Mission lifecycle — walkthrough

This is the practical companion to Figure 4 in the PRD. It steps through a single user-delegated mission from creation to either completion or revocation, with the exact curl/kubectl commands you'd run against the running cluster.

For the diagram view, see [`../../aauth-platform-prd.md#figure-4--mission-lifecycle`](../../aauth-platform-prd.md).

## Prereqs

`make demo` has finished and the spot-checks in [INSTALL.md §4](INSTALL.md) pass. Set a couple of shell aliases for brevity:

```bash
alias k='kubectl'
PLATFORM=platform
APPS=apps
```

## 1. Create a mission via the backend

The backend creates a mission when it receives a user request. For the demo we use a dev-only login endpoint to skip OIDC:

```bash
TOKEN=$(curl -sf -X POST http://localhost:8000/dev/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo-user"}' | jq -r '.access_token')

RESP=$(curl -sf -X POST http://localhost:8000/v1/optimize \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sku":"WIDGET-1","region":"us-east"}')

MISSION_ID=$(echo "$RESP" | jq -r '.mission_id')
echo "mission_id=$MISSION_ID"
```

What just happened:

1. The backend called `POST /v1/missions` on the mission-service with `{user, originator: backend, scope, ttl}`.
2. The mission-service returned `mission_id` and `state=active`.
3. The backend made a signed A2A call to `supply-chain-agent` carrying `X-Mission-ID: <mission_id>`.

## 2. Inspect the mission

```bash
k -n $PLATFORM exec deploy/mission-service -- \
  curl -sf http://localhost:9001/v1/missions/$MISSION_ID | jq .
```

You should see:

- `state: "active"` (or `completed` if the request already finished)
- `hops: [...]` — entries logged by each agent as the call propagated
- `tokens: [...]` — RFC 8693 exchange tokens issued during the fan-out

## 3. Watch propagation in real time

In another terminal, tail the mission service logs *before* you make a request so you can see the hops as they happen:

```bash
k -n $PLATFORM logs -f deploy/mission-service | grep -E 'mission|hop'
```

Then in the first terminal kick off another mission via the script:

```bash
make run
```

You should see structured log lines like:

```
mission created       mission_id=abc-... user=demo-user originator=backend
hop logged            mission_id=abc-... from=backend       to=supply-chain
token issued          jti=... mission_id=abc-... act=[backend, supply-chain]
hop logged            mission_id=abc-... from=supply-chain  to=market-analysis
mission state updated mission_id=abc-... state=completed
```

The `act` chain extends at each hop — that's the platform's token-exchange broker (RadiantLogic or Keycloak) doing the work of issuing a fresh token with `act` extended, while preserving `mission_id` as a passthrough claim.

## 4. Revoke a mission mid-flight

This is the kill-switch path from Figure 4, Phase B.

In one terminal, start a long-running mission and capture its id:

```bash
TOKEN=$(curl -sf -X POST http://localhost:8000/dev/login -H 'Content-Type: application/json' -d '{"username":"demo-user"}' | jq -r .access_token)
MISSION_ID=$(curl -sf -X POST http://localhost:8000/v1/optimize-slow \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"sku":"WIDGET-1","region":"us-east","delay_ms":15000}' | jq -r .mission_id)
echo "long mission: $MISSION_ID"
```

In a second terminal, revoke it:

```bash
make revoke-mission ID=$MISSION_ID
```

Watch the gateway logs:

```bash
k -n gateway logs -f deploy/agentgateway | grep "$MISSION_ID"
```

You should see the next signed call carrying that `mission_id` rejected with `401 mission revoked`, within 5 seconds of the revoke call.

The mission state will show:

```bash
k -n $PLATFORM exec deploy/mission-service -- \
  curl -sf http://localhost:9001/v1/missions/$MISSION_ID | jq '.state, .revoked_by'
# "revoked"
# "operator"
```

And the corresponding tokens in the ledger will be marked `revoked: true`.

## 5. Revoke a registered agent

This is the rotation/revocation flow from Figure 3, Flow B.

```bash
make revoke-agent ID=market-analysis
```

What this does:

1. `DELETE /v1/agents/market-analysis` on registry-service → marks the agent `revoked`.
2. The mission service hears about it via the policy update (next `/v1/policy/render`) and any active missions touching that agent are tainted.
3. agentgateway's policy ConfigMap is refreshed; the next signed call from `market-analysis` (or destined to it) is rejected with `401 agent revoked`.

You can confirm the registry state:

```bash
k -n $PLATFORM exec deploy/registry-service -- \
  curl -sf http://localhost:9000/v1/agents/market-analysis | jq '.lifecycle_state'
# "revoked"
```

To bring it back up, re-register and re-enroll:

```bash
make register     # re-registers all three agents (idempotent on conflict — see script for behavior)
make apps         # re-deploys workloads picking up new bootstrap tokens
```

## 6. List all missions

The mission service exposes a list endpoint suitable for the eventual "my missions" UI (P1-3 in the PRD):

```bash
# All missions for a given user
k -n $PLATFORM exec deploy/mission-service -- \
  curl -sf "http://localhost:9001/v1/missions?user=demo-user&limit=20" | jq .

# Only active missions
k -n $PLATFORM exec deploy/mission-service -- \
  curl -sf "http://localhost:9001/v1/missions?state=active" | jq .
```

## What's wired and what's stubbed

| Behavior in the diagram                       | In this demo                                                  |
| --------------------------------------------- | ------------------------------------------------------------- |
| Mission creation + state machine              | Real (mission-service)                                        |
| `act` chain extended at each RFC 8693 hop     | Configured at RL / Keycloak; depends on protocol-mapper       |
| `mission_id` passthrough claim                | Configured in both IDPs' realm import                         |
| OTel spans tagged with `mission_id`           | Backend + agents include it; collector not deployed by default |
| Token ledger with revocation                  | Real (mission-service `tokens` table)                          |
| agentgateway sub-5s enforcement               | Best-effort via policy ConfigMap refresh; depends on gateway   |
| User-facing "my missions" view                | Not in v1 — listed as P1-3 in the PRD                          |
