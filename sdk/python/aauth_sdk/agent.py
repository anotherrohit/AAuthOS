"""
The Agent class wires the SDK together. One Agent per process — typically
constructed at startup, used everywhere via dependency injection.

Usage:

    from aauth_sdk import Agent, MissionMiddleware
    from fastapi import FastAPI

    agent = Agent.from_env()
    app = FastAPI()

    @app.on_event("startup")
    async def _boot() -> None:
        await agent.enroll()

    app.add_middleware(MissionMiddleware)
    agent.mount_endpoints(app)

    # On any outbound call:
    async with agent.client(audience="https://platform.aauth.local/agents/market-analysis",
                            upstream_token=inbound_bearer) as c:
        r = await c.post("https://maa.apps.svc/foo", json={"x": 1})
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import structlog
from fastapi import FastAPI

from . import server
from .client import MissionHopLogger, SignedClient, UpstreamTokenProvider
from .config import AgentConfig
from .enrollment import enroll
from .keys import Keypair, load_or_generate
from .token_exchange import TokenExchangeClient
from .verification import SignatureVerifier

log = structlog.get_logger()


class Agent:
    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.keypair: Keypair = load_or_generate(state_dir=cfg.key_state_dir)
        self._tx = TokenExchangeClient(token_endpoint=cfg.idp_token_exchange_url)
        self._verifier: SignatureVerifier | None = None
        self._mission_logger: MissionHopLogger | None = (
            MissionHopLogger(mission_url=cfg.mission_url) if cfg.mission_url else None
        )

    # ---- factory ------------------------------------------------------- #

    @classmethod
    def from_env(cls) -> "Agent":
        return cls(AgentConfig.from_env())

    # ---- lifecycle ----------------------------------------------------- #

    async def enroll(self) -> None:
        """Run the first-boot enrollment. Idempotent. Raises on hard failures."""
        await enroll(cfg=self.cfg, keypair=self.keypair)

    # ---- server side --------------------------------------------------- #

    def mount_endpoints(self, app: FastAPI) -> None:
        """Add /jwks.json + /.well-known/aauth-agent + /healthz."""
        server.mount(app, cfg=self.cfg, keypair=self.keypair)

    def verifier(self) -> SignatureVerifier:
        """Lazily build the inbound-signature verifier."""
        if self._verifier is None:
            self._verifier = SignatureVerifier(registry_url=self.cfg.registry_url)
        return self._verifier

    # ---- client side --------------------------------------------------- #

    def client(
        self,
        *,
        audience: str,
        upstream_token: str | None = None,
        upstream_token_provider: UpstreamTokenProvider | None = None,
        base_url: str | None = None,
    ) -> SignedClient:
        """
        Build an outbound client scoped to a downstream audience.

        Supply *either*:
          - upstream_token  — a static string (e.g. the user's OIDC token in
                              an originator agent), or
          - upstream_token_provider — an async callable that fetches the
                              relevant inbound token on demand (e.g. read
                              the Authorization header off the current
                              FastAPI Request).

        The Agent will exchange that token at the IDP via RFC 8693 before
        making the outbound call.
        """
        if upstream_token and upstream_token_provider:
            raise ValueError("supply upstream_token OR upstream_token_provider, not both")
        if not upstream_token and not upstream_token_provider:
            raise ValueError("upstream credential required (token or provider)")

        async def _provider() -> str | None:
            if upstream_token is not None:
                return upstream_token
            return await upstream_token_provider()  # type: ignore[misc]

        return SignedClient(
            cfg=self.cfg,
            keypair=self.keypair,
            token_exchange=self._tx,
            upstream_token_provider=_provider,
            mission_logger=self._mission_logger,
            audience=audience,
            base_url=base_url,
        )
