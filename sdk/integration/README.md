# Integration guides — patching the upstream agents

The agent code itself lives in `christian-posta/aauth-full-demo`. We don't fork it; instead, `scripts/06-deploy-apps.sh` shallow-clones it at build time and applies a small set of edits before building the Docker images.

This directory describes those edits, per agent. Each guide is hand-applyable (it shows you the file, the lines to replace, and what to put there) and also serves as documentation for what the SDK is doing.

## Why patches, not a fork

- The upstream demo is a moving target — Christian Posta evolves it actively. A fork would diverge.
- The edits are small (~10–30 lines per agent) and concentrated in two files per agent: the FastAPI entrypoint (`__main__.py` or `main.py`) and the AAuth interceptor (`aauth_interceptor.py`).
- The SDK replaces the interceptor entirely. The entrypoint gets two new imports and three new lines.

## Order of operations

1. [`backend.md`](backend.md) — the originator. Creates missions, calls downstream.
2. [`supply-chain-agent.md`](supply-chain-agent.md) — relay. Verifies inbound, exchanges token, calls downstream.
3. [`market-analysis-agent.md`](market-analysis-agent.md) — leaf. Verifies inbound only.

## Common pattern across all three

Every agent acquires one shared piece of boot wiring:

```python
# Top of the file, e.g. backend/app/main.py
from aauth_sdk import Agent, MissionMiddleware

agent = Agent.from_env()

# Inside the existing FastAPI app setup
app = FastAPI(...)
app.add_middleware(MissionMiddleware)
agent.mount_endpoints(app)

@app.on_event("startup")
async def _aauth_boot() -> None:
    await agent.enroll()
```

Everything else differs based on the agent's role.

## How `06-deploy-apps.sh` applies these

Each agent's Dockerfile gets prefixed with:

```dockerfile
COPY sdk/python /opt/aauth-sdk
RUN pip install /opt/aauth-sdk
```

…and a small `patch.py` is `COPY`-ed in that applies the edits to upstream files at build time. For the demo we keep that simple — three short Python scripts under `sdk/integration/patches/` that use `re.sub` to swap in the SDK calls.

In a production setting you'd vendor the patches into a long-lived fork. For a KubeCon demo, sed-style patches keep the upstream pristine and make the integration easy to walk an audience through.
