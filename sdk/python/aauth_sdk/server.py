"""
FastAPI integration — the routes every registered agent has to expose:

  GET /jwks.json                — public JWKS (consumed by other agents + the
                                  platform's aggregator + the IDP)
  GET /.well-known/aauth-agent  — agent metadata per AAuth spec §5
  GET /healthz                  — liveness

Mount these on the agent's own FastAPI app via `agent.mount_endpoints(app)`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from .config import AgentConfig
from .keys import Keypair


def make_router(*, cfg: AgentConfig, keypair: Keypair) -> APIRouter:
    router = APIRouter()

    @router.get(cfg.jwks_path)
    def jwks() -> dict[str, Any]:
        return keypair.to_jwks()

    @router.get(cfg.metadata_path)
    def metadata() -> dict[str, Any]:
        return {
            "agent": cfg.agent_id_url,
            "jwks_uri": f"{cfg.agent_id_url.rstrip('/')}{cfg.jwks_path}",
            "signature_schemes_supported": [cfg.signature_scheme],
            "signing_alg_values_supported": ["ed25519"],
            # Pointers back to the platform so verifiers know where to fetch
            # the aggregated JWKS / find the mission service.
            "registry_uri": cfg.registry_url,
            "token_exchange_uri": cfg.idp_token_exchange_url,
        }

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return router


def mount(app: FastAPI, *, cfg: AgentConfig, keypair: Keypair) -> None:
    app.include_router(make_router(cfg=cfg, keypair=keypair))
