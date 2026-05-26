# AAuth Mission Platform — KIND Demo

A runnable, KIND-based demo of the **AAuth Mission Platform**: a productization of [christian-posta/aauth-full-demo](https://github.com/christian-posta/aauth-full-demo) with first-class agent registration, mission tracking, and token-exchange-based AAuth, brokered by **RadiantLogic IDM** (RFC 8693) with **Keycloak** as a co-equal fallback.

The integration shape — RFC 8693 token exchange at RadiantLogic, federated against a JWKS source — is lifted from the prior PoC at [anotherrohit/spiffe-radiantlogic](https://github.com/anotherrohit/spiffe-radiantlogic). SPIRE-attested identity is **out of scope for v1** (deferred to P1 per the PRD); v1 uses a registry-issued bootstrap-token join flow.

See [the PRD](../aauth-platform-prd.md) for the product framing. This repo is the v1 scaffold.

## What's in here

```
aauth-mission-platform-demo/
├── README.md                       ← you are here
├── ARCHITECTURE.md                 ← sequence + threat model summary
├── Makefile                        ← one-command demo
├── .gitignore
├── kind/
│   └── kind-cluster.yaml           ← KIND config with port mappings
├── manifests/
│   ├── 00-namespaces.yaml
│   ├── radiantlogic/               ← RadiantLogic IDM + RFC 8693 config
│   ├── keycloak/                   ← Keycloak fallback realm
│   ├── platform/                   ← mission + registry services
│   ├── agentgateway/               ← edge enforcement
│   └── workloads/                  ← backend, SCA, MAA, frontend
├── platform/
│   ├── mission-service/            ← FastAPI: missions API + state machine
│   └── registry-service/           ← FastAPI: agent registry + JWKS aggregator
├── scripts/
│   ├── 00-kind-up.sh
│   ├── 01-deploy-radiantlogic.sh
│   ├── 02-deploy-keycloak.sh
│   ├── 03-deploy-platform.sh
│   ├── 04-deploy-agentgateway.sh
│   ├── 05-register-agents.sh
│   ├── 06-deploy-apps.sh
│   ├── 07-run-demo.sh
│   └── 99-teardown.sh
└── docs/
    ├── INSTALL.md
    ├── MISSION_LIFECYCLE.md
    └── IDP_TOGGLE.md
```

## What's actually implemented vs. stubbed

This is a **runnable scaffold**, not a finished product:

| Component | Status |
|---|---|
| KIND cluster + namespaces | Full |
| Numbered deploy scripts | Full (idempotent, with toggles) |
| RadiantLogic Helm values + RFC 8693 config | Full (requires RL license + image) |
| Keycloak fallback realm + token-exchange | Full |
| Platform mission service (FastAPI) | Full — mission CRUD, hop + token ledger, kill-switch, audit feed, operator basic-auth |
| Platform registry service (FastAPI) | Full — register / enroll / rotate / revoke, JWKS aggregator, policy renderer, IDP config, operator basic-auth |
| **Operator console (HTML/JS SPA)** | **Full — login, agent CRUD, mission detail with hop chain, token ledger, gateway + IDP view, audit feed** |
| agentgateway deployment + policy generation | Manifest + ConfigMap; policy generator is a script |
| Backend / Supply Chain Agent / Market Analysis Agent | References upstream `aauth-full-demo` — patched env + Dockerfiles, with `aauth_sdk` wired in |
| Frontend (supply-chain-ui) | References upstream as-is |

The agent code itself comes from the upstream demo — this repo's job is the deployment substrate, the platform services, and the IDP integration.

## Prerequisites

- Docker + [kind](https://kind.sigs.k8s.io/) ≥ 0.22
- `kubectl`, `helm` ≥ 3.12
- `make`, `jq`, `curl`
- A RadiantLogic IDM v8.1 license + container access ([self-managed install](https://developer.radiantlogic.com/idm/v8.1/installation/self-managed/)) **OR** use the Keycloak fallback (no license required)

## Run the demo

```bash
# Default path — RadiantLogic as IDP
make demo

# OSS fallback — Keycloak as IDP, no license required
make USE_KEYCLOAK=1 demo

# Open the operator console after demo finishes:
#   http://localhost:9002    user: operator   pass: aauth-operator-demo
```

The operator console is the platform-operator UI. It runs in-cluster on host port `9002` and talks directly to `registry-service:9000` and `mission-service:9001` (CORS-cleared, HTTP Basic auth). See [docs/OPERATOR_CONSOLE.md](docs/OPERATOR_CONSOLE.md).

`make demo` runs each numbered script in order. To run them individually:

```bash
make kind-up           # 00 — bring up KIND cluster, install ingress
make radiantlogic      # 01 — deploy RadiantLogic IDM + RFC 8693 config
# OR: make keycloak    # 02 — deploy Keycloak fallback
make platform          # 03 — deploy mission + registry services
make gateway           # 04 — deploy agentgateway with the empty starter policy
make register          # 05 — operator pre-registers backend, SCA, MAA
make apps              # 06 — deploy the four workloads
make run               # 07 — drive a user request end-to-end, watch the mission
make teardown          # 99 — delete the KIND cluster
```

## What the demo shows

After `make demo` you can:

1. **See registered agents** — `kubectl exec` into the registry pod or hit `http://localhost:9000/v1/agents` to see backend, supply-chain-agent, and market-analysis-agent in `state=active`.
2. **Initiate a mission** — open `http://localhost:3000`, log in (RadiantLogic or Keycloak), submit a supply-chain optimization request. The backend creates a mission with the platform.
3. **Watch the act-chain in flight** — `tail` the mission service logs to see the `mission_id` propagate through `backend → SCA → MAA`, with the platform's `/token` endpoint extending the `act` claim at each hop.
4. **Revoke a mission mid-flight** — `curl -X POST http://localhost:9000/v1/missions/{id}/revoke` and watch agentgateway start returning 401 for any in-flight call carrying that mission_id, within 5 seconds.
5. **Revoke an agent** — `make revoke-agent ID=...` to kill an agent at the registry level. Subsequent signed calls are rejected at the gateway.

See [`docs/MISSION_LIFECYCLE.md`](docs/MISSION_LIFECYCLE.md) for the full walkthrough with curl commands.

## IDP toggle

Both `make radiantlogic` and `make keycloak` deploy a working IDP in the same `idp` namespace. The platform reads `IDP_ISSUER_URL` and `IDP_TOKEN_EXCHANGE_URL` from a ConfigMap; switching IDPs is a matter of pointing those values at the right service. See [`docs/IDP_TOGGLE.md`](docs/IDP_TOGGLE.md).

The agent code paths are **identical** across the two. Only the IDP-side config differs.

## Identity model (v1, no SPIRE)

| Component | Identity |
|---|---|
| Backend agent | `https://platform.aauth.local/agents/backend` |
| Supply chain agent | `https://platform.aauth.local/agents/supply-chain` |
| Market analysis agent | `https://platform.aauth.local/agents/market-analysis` |
| Mission service issuer | `https://platform.aauth.local` |
| RadiantLogic issuer | `https://radiantlogic.aauth.local` |
| Aggregate JWKS for federation | `https://platform.aauth.local/v1/agents/jwks.json` |

Agent registration produces a stable agent ID URL per AAuth SPEC §10.3.1. Each agent's JWKS is published by the agent and aggregated by the platform's `/v1/agents/jwks.json` endpoint, which is what RadiantLogic and Keycloak federate against for RFC 8693 subject-token validation.

## Cleanup

```bash
make teardown   # deletes the KIND cluster entirely
```

## Further reading

- [PRD: AAuth Mission Platform](../aauth-platform-prd.md)
- [AAuth specification (christian-posta/aauth-full-demo)](https://github.com/christian-posta/aauth-full-demo/blob/main/SPEC.md)
- [SPIRE × RadiantLogic PoC (prior art)](https://github.com/anotherrohit/spiffe-radiantlogic)
- [RFC 8693 OAuth 2.0 Token Exchange](https://datatracker.ietf.org/doc/html/rfc8693)
- [RFC 9421 HTTP Message Signatures](https://datatracker.ietf.org/doc/html/rfc9421)
