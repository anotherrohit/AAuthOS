# aauth_sdk — agent-side client SDK

Drop-in Python package that the three demo agents (backend, supply-chain, market-analysis) use to talk to the AAuth Mission Platform. Replaces the hand-rolled `aauth_interceptor.py` from the upstream `aauth-full-demo` with a small, opinionated wiring layer.

## What the SDK does for you

| Concern                              | Without SDK                                   | With SDK                                  |
| ------------------------------------ | --------------------------------------------- | ----------------------------------------- |
| Generate + persist Ed25519 keypair   | Boilerplate in every agent                    | `Agent.from_env()`                        |
| Publish `/jwks.json` and metadata    | Hand-roll the FastAPI routes                  | `agent.mount_endpoints(app)`              |
| First-boot enrollment with registry  | Hand-roll bootstrap + PoP signature           | `await agent.enroll()`                    |
| Extract X-Mission-ID from inbound    | Read headers in every handler                 | `app.add_middleware(MissionMiddleware)`   |
| Sign outbound calls per RFC 9421     | 30+ lines of canonicalization per call        | `await c.post(...)` — automatic           |
| RFC 8693 token exchange for hops     | Hand-roll the form POST + caching             | Built into the outbound client            |
| Propagate X-Mission-ID downstream    | Easy to forget                                | Automatic                                 |
| Log hops + tokens to mission service | Often skipped                                 | Automatic, fire-and-forget                |

The SDK is deliberately thin — about 800 lines across nine modules. There's no magic global state; the `Agent` instance is the only thing you carry around.

## Install

```bash
# From this repo
pip install -e sdk/python
```

In a Docker build the agent images install it via:

```dockerfile
COPY sdk/python /opt/aauth-sdk
RUN pip install /opt/aauth-sdk
```

The integration guides under [`../integration/`](../integration/) cover the actual upstream-demo patches needed.

## Environment

Every agent needs these env vars set (the workload manifests in `manifests/workloads/` already wire them up):

```bash
AAUTH_BOOTSTRAP_TOKEN     # one-time token from operator pre-registration
AAUTH_AGENT_ID_URL        # https://platform.aauth.local/agents/<slug>
AAUTH_REGISTRY_URL        # http://registry-service.platform.svc.cluster.local:9000
AAUTH_MISSION_URL         # http://mission-service.platform.svc.cluster.local:9001
AAUTH_GATEWAY_URL         # https://agentgateway.gateway.svc.cluster.local:8443
IDP_ISSUER_URL            # from platform-config ConfigMap
IDP_TOKEN_EXCHANGE_URL    # from platform-config ConfigMap
IDP_JWKS_URL              # from platform-config ConfigMap
AAUTH_SIGNATURE_SCHEME    # 'jwks' (default) | 'hwk' | 'jwt'
```

## Minimal example — a leaf agent

```python
from fastapi import FastAPI, Request
from aauth_sdk import Agent, MissionMiddleware

agent = Agent.from_env()
app = FastAPI()

@app.on_event("startup")
async def _boot() -> None:
    await agent.enroll()

app.add_middleware(MissionMiddleware)
agent.mount_endpoints(app)

# Inbound signature verification — gate sensitive routes
@app.post("/analyze")
async def analyze(request: Request) -> dict:
    await agent.verifier().verify(
        method=request.method,
        url=request.url,
        headers=dict(request.headers),
        body=await request.body(),
    )
    return {"result": "..."}
```

## Minimal example — a relay agent (has both inbound and outbound)

```python
from fastapi import FastAPI, Request, HTTPException
from aauth_sdk import Agent, MissionMiddleware

agent = Agent.from_env()
app = FastAPI()

@app.on_event("startup")
async def _boot() -> None:
    await agent.enroll()

app.add_middleware(MissionMiddleware)
agent.mount_endpoints(app)

@app.post("/optimize")
async def optimize(request: Request) -> dict:
    # 1. Verify the inbound signed call.
    await agent.verifier().verify(
        method=request.method, url=request.url,
        headers=dict(request.headers), body=await request.body(),
    )

    # 2. Pull the inbound bearer — we'll exchange it for a token scoped
    #    to the downstream agent via RFC 8693.
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer")
    inbound_token = auth.removeprefix("Bearer ").removeprefix("bearer ")

    # 3. Call the downstream — the SDK handles exchange + signing + mission
    #    propagation + ledger logging.
    async with agent.client(
        audience="https://platform.aauth.local/agents/market-analysis",
        upstream_token=inbound_token,
    ) as c:
        r = await c.post("http://market-analysis-agent.apps.svc/analyze", json={...})
        r.raise_for_status()
        return r.json()
```

## Minimal example — an originator (Backend)

The originator is special: it doesn't receive a signed AAuth call — it
receives a user's OIDC token from the frontend. It needs to *create* the
mission too.

```python
import httpx
from fastapi import FastAPI, Request, Depends
from aauth_sdk import Agent, MissionMiddleware, set_mission_id

agent = Agent.from_env()
app = FastAPI()

@app.on_event("startup")
async def _boot() -> None:
    await agent.enroll()

app.add_middleware(MissionMiddleware)
agent.mount_endpoints(app)

async def _create_mission(user_sub: str, scope: str) -> str:
    async with httpx.AsyncClient(timeout=2.0) as c:
        r = await c.post(
            f"{agent.cfg.mission_url}/v1/missions",
            json={"user_subject": user_sub, "originator_agent_id": agent.cfg.agent_id_url,
                  "scope": scope, "ttl_seconds": 600},
        )
    return r.json()["mission_id"]

@app.post("/v1/optimize")
async def optimize(request: Request, payload: dict, user: dict = Depends(verify_oidc)):
    mid = await _create_mission(user_sub=user["sub"], scope="supply-chain-optimize")
    set_mission_id(mid)                       # seed the contextvar for downstream

    user_token = request.headers["authorization"].removeprefix("Bearer ")
    async with agent.client(
        audience="https://platform.aauth.local/agents/supply-chain",
        upstream_token=user_token,
    ) as c:
        r = await c.post("http://supply-chain-agent.apps.svc/optimize", json=payload)
    return {"mission_id": mid, **r.json()}
```

## What's still TODO in the SDK

Search for `TODO(...)` in the source:

- `verification.py` — strict RFC 9421 component selection + replay-protection nonce cache.
- `signing.py` — support for `@target-uri` and the `@request-target` derived component (we cover `@method`, `@authority`, `@path`, `@query`, `content-digest`).
- `enrollment.py` — exponential backoff on transient registry failures during boot.
- `token_exchange.py` — fallback to `client_secret_jwt` auth when the IDP requires it.

The TS port (P1-2) lives alongside this in `sdk/typescript/` (not in this scaffold yet).
