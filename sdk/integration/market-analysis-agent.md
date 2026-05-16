# Market analysis agent integration

The market analysis agent is the **leaf**: it verifies inbound signed calls and never makes downstream calls. The simplest integration of the three.

Upstream layout:

```
market-analysis-agent/
├── __main__.py
├── agent_executor.py
├── aauth_interceptor.py        ← replaced by aauth_sdk (verification only)
├── http_headers_middleware.py
└── pyproject.toml
```

## Edit 1 — `market-analysis-agent/pyproject.toml`

```diff
 dependencies = [
   ...
+  "aauth-sdk",
 ]
```

## Edit 2 — `market-analysis-agent/__main__.py`

### Imports + Agent

```diff
-from aauth_interceptor import verify_inbound   # delete
+from aauth_sdk import Agent, MissionMiddleware
+
+agent = Agent.from_env()
```

### App wiring

```diff
 app = FastAPI(title="Market Analysis Agent", ...)
+
+app.add_middleware(MissionMiddleware)
+agent.mount_endpoints(app)
+
+@app.on_event("startup")
+async def _aauth_boot() -> None:
+    await agent.enroll()
```

### Inbound verification

Same pattern as the supply chain agent — gate every A2A endpoint behind signature verification:

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

app.include_router(a2a_router, dependencies=[Depends(_verify_inbound)])
```

There's nothing else to wire — no outbound calls, no token exchange. The leaf just confirms "the call I'm receiving came from a registered agent the platform vouches for, and it's tagged with a mission the platform is still treating as active."

## Edit 3 — delete `aauth_interceptor.py` and `http_headers_middleware.py`

```bash
rm market-analysis-agent/aauth_interceptor.py
rm market-analysis-agent/http_headers_middleware.py    # optional, see SCA guide
```

## Verifying

```bash
# MAA's JWKS endpoint:
kubectl -n apps exec deploy/market-analysis-agent -- curl -sf http://localhost:9998/jwks.json

# Try a *forged* call directly — it should bounce with 401:
kubectl -n apps run forge --rm -it --image=curlimages/curl --restart=Never -- \
  curl -sf -X POST http://market-analysis-agent.apps:9998/analyze \
  -H 'content-type: application/json' \
  -d '{"sku":"WIDGET-1"}'
# 401 invalid AAuth signature: missing one of: signature-key, signature-input, ...

# A real call (driven by `make run`) succeeds and shows up in the mission ledger.
```

## What this agent does NOT do

Worth saying explicitly because it's the small case but the easiest to over-engineer:

- Does not call any downstream service.
- Does not create or update missions — it only reads the `X-Mission-ID` header for logging context.
- Does not perform a token exchange — it accepts whatever bearer arrived.
- Does not contact the mission ledger — the upstream SCA logged the hop *to* it; that's enough.

The SDK supports all those operations from the same `Agent` object, but a leaf simply never invokes them.
