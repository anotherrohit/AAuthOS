"""
aauth_sdk — client SDK for the AAuth Mission Platform.

Typical usage in an agent:

    from aauth_sdk import Agent, MissionMiddleware

    agent = Agent.from_env()
    await agent.enroll()                          # self-enroll on boot

    app = FastAPI()
    app.add_middleware(MissionMiddleware)         # extracts X-Mission-ID
    agent.mount_endpoints(app)                    # /jwks.json + /.well-known/aauth-agent

    async with agent.client(audience="...") as c: # outbound: signs + exchanges + propagates
        await c.post("/foo", json={...})
"""

from .agent import Agent
from .config import AgentConfig
from .mission import MissionMiddleware, current_mission_id, set_mission_id
from .token_exchange import TokenExchangeError

__all__ = [
    "Agent",
    "AgentConfig",
    "MissionMiddleware",
    "current_mission_id",
    "set_mission_id",
    "TokenExchangeError",
]
