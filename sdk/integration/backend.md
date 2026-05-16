# Backend integration

The backend is the **originator**: a human user logs in via OIDC, hits the backend, and the backend creates a mission and signs the first agent-to-agent call.

Upstream layout (after `git clone https://github.com/christian-posta/aauth-full-demo`):

```
backend/
├── app/
│   ├── main.py
│   ├── services/
│   │   └── aauth_interceptor.py        ← replaced by aauth_sdk
│   └── ...
├── env.example
├── pyproject.toml
└── README.md
```

## Edit 1 — `backend/pyproject.toml`

Add `aauth-sdk` as a dependency. (`scripts/06-deploy-apps.sh` builds against a local path, but for editor support you'll want the dep listed.)

```diff
 dependencies = [
   "fastapi>=0.115",
   "httpx>=0.27",
   ...
+  "aauth-sdk",
 ]
```

## Edit 2 — `backend/app/main.py`

Three regions to change: imports, app setup, and the route that initiates a downstream A2A call.

### Imports — top of the file

Replace the `aauth_interceptor` import:

```diff
-from app.services.aauth_interceptor import sign_outbound, verify_inbound  # delete
+from aauth_sdk import Agent, MissionMiddleware, set_mission_id
+
+agent = Agent.from_env()
```

### App setup — wherever the `app = FastAPI(...)` line lives

Add three lines right after the FastAPI() call:

```diff
 app = FastAPI(title="AAuth Backend", ...)
+
+app.add_middleware(MissionMiddleware)
+agent.mount_endpoints(app)
+
+@app.on_event("startup")
+async def _aauth_boot() -> None:
+    await agent.enroll()
```

### The `/v1/optimize` route — replace the hand-rolled signing

The upstream code looks roughly like:

```python
@app.post("/v1/optimize")
async def optimize(payload: dict, request: Request, user = Depends(verify_oidc)):
    headers = sign_outbound(method="POST", url=SUPPLY_CHAIN_URL, body=payload, agent_id=BACKEND_AGENT_URL)
    async with httpx.AsyncClient() as c:
        r = await c.post(SUPPLY_CHAIN_URL, json=payload, headers=headers)
    return r.json()
```

Replace with:

```python
import httpx
from fastapi import Depends, Request

SUPPLY_CHAIN_AGENT_ID_URL = os.environ["SUPPLY_CHAIN_AGENT_ID_URL"]
SUPPLY_CHAIN_URL = "http://supply-chain-agent.apps.svc.cluster.local:9999/optimize"

async def _create_mission(user_sub: str, scope: str) -> str:
    async with httpx.AsyncClient(timeout=2.0) as c:
        r = await c.post(
            f"{agent.cfg.mission_url}/v1/missions",
            json={
                "user_subject": user_sub,
                "originator_agent_id": agent.cfg.agent_id_url,
                "scope": scope,
                "ttl_seconds": 600,
            },
        )
        r.raise_for_status()
        return r.json()["mission_id"]

@app.post("/v1/optimize")
async def optimize(payload: dict, request: Request, user = Depends(verify_oidc)) -> dict:
    # 1. Create the mission.
    mid = await _create_mission(user_sub=user["sub"], scope="supply-chain-optimize")
    set_mission_id(mid)

    # 2. The user's OIDC access token is the upstream credential we'll exchange.
    user_token = request.headers.get("authorization", "").removeprefix("Bearer ").removeprefix("bearer ")

    # 3. Outbound — SDK exchanges, signs, propagates X-Mission-ID, logs the hop.
    async with agent.client(
        audience=SUPPLY_CHAIN_AGENT_ID_URL,
        upstream_token=user_token,
    ) as c:
        r = await c.post(SUPPLY_CHAIN_URL, json=payload)
        r.raise_for_status()

    # 4. Mark the mission completed.
    async with httpx.AsyncClient(timeout=2.0) as c:
        await c.patch(
            f"{agent.cfg.mission_url}/v1/missions/{mid}",
            json={"state": "completed"},
        )

    return {"mission_id": mid, **r.json()}
```

Key behaviors:

- `set_mission_id(mid)` seeds the contextvar so the SDK's outbound client picks it up automatically. Without this, the originator has no inbound request to extract the mission from.
- The user's OIDC access token (issued by RL or Keycloak) is what RL/Keycloak knows how to validate natively. It becomes the `subject_token` in the RFC 8693 exchange at the IDP.

## Edit 3 — delete `backend/app/services/aauth_interceptor.py`

The SDK replaces it entirely. Leaving it in place is harmless but confusing.

```bash
rm backend/app/services/aauth_interceptor.py
```

If other modules import from it, update them to import from `aauth_sdk` instead (typically only `main.py` does).

## Edit 4 — optional: add a `/dev/login` endpoint for the demo script

`scripts/07-run-demo.sh` calls a `/dev/login` endpoint to skip the OIDC flow. If you want that to work, add this somewhere in `main.py`:

```python
import jwt as pyjwt, time

DEV_SIGNING_KEY = os.environ.get("BACKEND_DEV_SIGNING_KEY", "demo-only-key-not-secure")

@app.post("/dev/login")
async def dev_login(body: dict) -> dict:
    """DEV ONLY — issues a fake user token for the demo script."""
    if os.environ.get("ENABLE_DEV_LOGIN") != "1":
        raise HTTPException(404)
    token = pyjwt.encode(
        {"sub": body["username"], "iss": "dev", "iat": int(time.time()), "exp": int(time.time()) + 3600},
        DEV_SIGNING_KEY, algorithm="HS256",
    )
    return {"access_token": token}
```

Set `ENABLE_DEV_LOGIN=1` in the backend's env (`manifests/workloads/01-backend.yaml`) to enable.

## Verifying the integration

After `make demo`:

```bash
# Backend's JWKS should be reachable from the cluster:
kubectl -n apps exec deploy/backend -- curl -sf http://localhost:8000/jwks.json
# {"keys":[{"kty":"OKP", "crv":"Ed25519", "x":"...", "kid":"v1", ...}]}

# Backend should appear active in the registry:
kubectl -n platform exec deploy/registry-service -- \
  curl -sf http://localhost:9000/v1/agents/backend | jq .lifecycle_state
# "active"
```

Then drive a mission with `make run` and watch the mission-service log a hop from `backend → supply-chain` with an act-chain containing `[backend]`.
