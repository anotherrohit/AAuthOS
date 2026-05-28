# Operator console

A vanilla HTML/JS single-page app for the platform operator. Sign in with operator credentials → see every agent, mission, token, gateway policy, and IDP config in one place; register new agents and reveal their one-time bootstrap tokens; revoke or force-rotate agents; inspect or kill missions in flight.

## Architecture

```
                   ┌───────────────────────────┐
  Browser ────────►│  operator-console :9002   │   (static HTML/JS, stdlib server)
                   └────────────┬──────────────┘
                                │  fetch + Basic auth (operator creds)
                                ▼
       ┌─────────────────────────────────────────────┐
       │  registry-service :9000     mission-service :9001  │
       │   /v1/agents (CRUD, rotate, revoke)   /v1/missions │
       │   /v1/agents/jwks.json (PUBLIC)       /v1/tokens   │
       │   /v1/idp-config                      /v1/audit    │
       │   /v1/policy/render                   /v1/stats    │
       └─────────────────────────────────────────────┘
```

The console **never proxies through a backend** — it makes CORS-cleared requests directly from the browser to the platform services. The platform services authenticate every admin call with HTTP Basic auth (env-configured credentials). Browser stores the encoded credential in `sessionStorage` so closing the tab logs you out.

## Two ways to run it

### 1. As part of the full `make demo` flow (in-cluster)

`make platform` (script `03-deploy-platform.sh`) now builds the `aauth/operator-console:dev` image, `kind load`s it, and applies `manifests/platform/03-operator-console.yaml`. The KIND cluster config maps host port `9002` to the operator-console NodePort.

```bash
make demo   # or make USE_KEYCLOAK=1 demo for the OSS path
make console   # prints the URL and tries to open it
```

Then open <http://localhost:9002> and sign in with `operator` / `aauth-operator-demo`. Override the credentials by editing the `operator-auth` ConfigMap before `make platform`:

```bash
# Before make platform — or kubectl apply later and restart the platform pods
kubectl -n platform create configmap operator-auth \
  --from-literal=OPERATOR_USERNAME=alice \
  --from-literal=OPERATOR_PASSWORD='your-real-password-please' \
  --from-literal=CORS_ORIGINS='http://localhost:9002,http://127.0.0.1:9002' \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n platform rollout restart deploy/registry-service deploy/mission-service
```

### 2. Standalone against host-forwarded services (no K8s required)

If you don't want to spin up KIND but you do want to click around the UI, you can run the platform services locally (e.g. with `uvicorn`) and serve just the console:

```bash
# Terminal A — start registry-service
cd platform/registry-service
uv sync && uv run uvicorn main:app --port 9000

# Terminal B — start mission-service
cd platform/mission-service
uv sync && uv run uvicorn main:app --port 9001

# Terminal C — start the console
cd platform/operator-console
python3 server.py
# AAuth operator console serving at  http://localhost:9002
```

Or use the in-process harness (`platform/run_local_demo.py`) — note: it doesn't expose HTTP endpoints, only the in-process API. The console needs real services at `:9000` and `:9001`.

## What the console can do

| Tab | Capability | Endpoints used |
|---|---|---|
| Dashboard | Live stats — active/pending agents, active missions, tokens issued; recent activity feed | `GET /v1/stats`, `GET /v1/audit` |
| Agents | List · register (operator pre-registration → bootstrap token reveal) · force-rotate · revoke · view JWKS thumbprint and allowed-downstreams policy | `GET/POST/DELETE /v1/agents`, `POST /v1/agents/{id}/force-rotate` |
| Missions | List with state filter · detail view showing the full hop chain, act extension at each hop, and tokens issued; one-click revoke | `GET /v1/missions`, `GET /v1/missions/{id}`, `POST /v1/missions/{id}/revoke` |
| Tokens | Cross-mission token ledger; filter by revocation status; see jti, caller, audience, act chain, mission, expiry, revocation | `GET /v1/tokens` |
| Gateway & IDP | Current IDP wiring (RadiantLogic vs Keycloak), token-exchange URL, federated JWKS source; live agentgateway policy rendered from registry state | `GET /v1/idp-config`, `GET /v1/policy/render` |
| Audit | Reconstructed event feed from missions + hops + tokens — newest first | `GET /v1/audit` |

## What it deliberately does NOT do

- **Initiate A2A calls.** Agents make those between themselves; the console shows them happening (via hops and tokens) but doesn't impersonate an agent.
- **Mint tokens.** Token exchange is RFC 8693 at the IDP — the console doesn't have a private key.
- **Edit JWKS directly.** Rotation is agent-initiated (with a "force rotate" signal the operator can send); the operator can't mint a new keypair on the agent's behalf.
- **Connect to multiple platforms.** One platform per browser tab. The console reads `registryUrl` / `missionUrl` from `localStorage` if you want to override the defaults; otherwise it hits `:9000` and `:9001`.

## Security caveats (v1)

This is a demo console. Production hardening required before any real use:

- **HTTP Basic auth, no rate limit.** Brute-force protection is the operator's responsibility (network policy, WAF, etc.).
- **Credentials in env vars on the platform pods.** A secrets manager (Vault, SSM) integration is P0-2.
- **No CSRF protection on POST/DELETE.** Acceptable because the console uses Basic auth (browsers don't auto-attach Basic auth cross-origin without explicit JS), but a session-cookie scheme would need CSRF tokens.
- **CORS allowlist is per-port, not per-origin-tenant.** If you serve the console from a non-default origin, add it to `CORS_ORIGINS` on the platform services.
- **No audit log on the console-side actions.** The platform services log every admin call to the registry `audit` table, but a separate operator-facing audit (who clicked revoke when) would be valuable.

## How to demo it well

A 90-second walkthrough that shows everything working:

1. **Dashboard** — point at the `Active agents: 3` / `Active missions: 0` headline. "These are real registered agents from the platform registry."
2. **Agents tab** — register a new agent called `forecaster`, owning team `ml`. The bootstrap-token reveal modal pops. "This is the one-time secret the operator hands to the developer. The platform never stores or re-issues it."
3. **Drive a mission** in another terminal (`make run`). Switch back to the console, hit **Missions** → click the new active mission. "Two hops, act chain extended at each one — same shape as Figure 4 in the PRD."
4. **Tokens tab** — point at the two RFC 8693 tokens with their `act` chains and expiry. "Every exchange the IDP issued is in this ledger."
5. **Gateway & IDP** — show the IDP config (issuer URL, token endpoint, JWKS federation target). "This is what RadiantLogic uses to validate agent-signed tokens."
6. **Missions → Revoke** the in-flight mission. Audit feed pops a `mission_revoked` event; token ledger flips both tokens to `revoked`. "Within five seconds at the gateway."
