# Supply chain agent integration

The supply chain agent is the **relay**: it verifies the inbound signed call from the backend, then makes a signed call to the market analysis agent. Its inbound bearer is the RFC 8693 token the backend exchanged from the user's OIDC token; its outbound bearer is a fresh RFC 8693 token exchanged from *that* inbound bearer.

Upstream layout:

```
supply-chain-agent/
├── __main__.py
├── agent_executor.py
├── aauth_interceptor.py        ← replaced by aauth_sdk
├── http_headers_middleware.py
├── pyproject.toml
└── README.md
```

## Edit 1 — `supply-chain-agent/pyproject.toml`

```diff
 dependencies = [
   "fastapi>=0.115",
   ...
+  "aauth-sdk",
 ]
```

## Edit 2 — `supply-chain-agent/__main__.py`

### Imports + Agent construction — top of the file

```diff
-from aauth_interceptor import sign_outbound, verify_inbound    # delete
+from aauth_sdk import Agent, MissionMiddleware
+
+agent = Agent.from_env()
```

### App wiring — wherever `app = FastAPI(...)` lives

```diff
 app = FastAPI(title="Supply Chain Agent", ...)
+
+app.add_middleware(MissionMiddleware)
+agent.mount_endpoints(app)
+
+@app.on_event("startup")
+async def _aauth_boot() -> None:
+    await agent.enroll()
```

### Inbound verification — gate the agent's RPC endpoint

The upstream typically has a JSON-RPC `/` endpoint handled by A2A SDK. Wrap it with a dependency that verifies the inbound signature:

```python
from fastapi import Depends, Request, HTTPException

async def _verify_inbound(request: Request) -> None:
    try:
        body = await request.body()
        await agent.verifier().verify(
            method=request.method,
            url=request.url,
            headers=dict(request.headers),
            body=body,
        )
    except Exception as e:
        raise HTTPException(401, f"invalid AAuth signature: {e}")

# Apply to the A2A handler:
app.include_router(a2a_router, dependencies=[Depends(_verify_inbound)])
```

If the upstream registers the A2A app via `app.mount("/", a2a_app)` instead of a router, use Starlette's middleware approach:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class _AAuthVerify(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Verify on the A2A endpoints, not on /jwks.json or /healthz
        if request.url.path in (agent.cfg.jwks_path, agent.cfg.metadata_path, "/healthz"):
            return await call_next(request)
        try:
            body = await request.body()
            await agent.verifier().verify(
                method=request.method, url=request.url,
                headers=dict(request.headers), body=body,
            )
        except Exception as e:
            from starlette.responses import JSONResponse
            return JSONResponse({"error": f"invalid AAuth signature: {e}"}, status_code=401)
        # Important: re-attach the body for the actual handler.
        async def _receive():
            return {"type": "http.request", "body": body, "more_body": False}
        request._receive = _receive
        return await call_next(request)

app.add_middleware(_AAuthVerify)
```

## Edit 3 — `supply-chain-agent/agent_executor.py`

Replace the hand-rolled outbound signing in whichever method calls the market-analysis-agent. Upstream looks roughly like:

```python
async def call_market_analysis(self, payload: dict, request_headers: dict) -> dict:
    headers = sign_outbound(method="POST", url=MAA_URL, body=payload, agent_id=SCA_AGENT_URL)
    async with httpx.AsyncClient() as c:
        r = await c.post(MAA_URL, json=payload, headers=headers)
    return r.json()
```

Replace with:

```python
async def call_market_analysis(self, payload: dict, request_headers: dict) -> dict:
    # The inbound Authorization header carries the bearer we'll exchange.
    inbound_bearer = request_headers.get("authorization", "")
    if not inbound_bearer.lower().startswith("bearer "):
        raise RuntimeError("supply-chain-agent received no inbound bearer to exchange")
    inbound_bearer = inbound_bearer.removeprefix("Bearer ").removeprefix("bearer ")

    async with agent.client(
        audience=os.environ["MARKET_ANALYSIS_AGENT_ID_URL"],
        upstream_token=inbound_bearer,
    ) as c:
        r = await c.post(MAA_URL, json=payload)
        r.raise_for_status()
        return r.json()
```

Two things are happening invisibly here:

1. The SDK calls the IDP's `/token` endpoint with `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `subject_token=<inbound_bearer>`, `audience=<MAA's agent_id_url>`. The IDP validates the inbound token via the federated JWKS source (the platform's `/v1/agents/jwks.json`), extends the `act` claim chain to `[backend, supply-chain]`, preserves the `mission_id` claim, and issues a new access token.
2. The SDK reads `current_mission_id()` from the contextvar that `MissionMiddleware` populated when the inbound request arrived, and adds it as `X-Mission-ID` on the outbound call.

## Edit 4 — delete `supply-chain-agent/aauth_interceptor.py`

```bash
rm supply-chain-agent/aauth_interceptor.py
```

## Edit 5 — keep `http_headers_middleware.py` or delete

The upstream `http_headers_middleware.py` captures inbound headers so the
hand-rolled interceptor can verify them. The SDK reads headers directly off
the `Request` object, so this middleware is no longer required. You can
delete it or leave it for any non-AAuth uses.

## Verifying

```bash
# SCA's JWKS:
kubectl -n apps exec deploy/supply-chain-agent -- curl -sf http://localhost:9999/jwks.json

# After `make run`, the mission ledger should show two hops:
kubectl -n platform exec deploy/mission-service -- \
  curl -sf "http://localhost:9001/v1/missions/<mission_id>" | jq '.hops'
# [
#   {"from": "...backend", "to": "...supply-chain", "act_chain": ["backend"]},
#   {"from": "...supply-chain", "to": "...market-analysis", "act_chain": ["backend", "supply-chain"]}
# ]
```
