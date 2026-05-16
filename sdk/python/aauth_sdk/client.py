"""
Outbound httpx wrapper — the one knob the agent code reaches for.

A `SignedClient` looks like a regular httpx.AsyncClient but on every
outbound request it:

  1. Exchanges the upstream credential at the IDP for a fresh access token
     scoped to the downstream `audience` (RFC 8693). The `act` chain
     extends by one hop on the IDP side.
  2. Adds `Authorization: Bearer <new_token>` and `X-Mission-ID` from the
     current contextvar.
  3. Signs the request per RFC 9421 with the agent's keypair, including a
     `content-digest` per RFC 9530.
  4. Posts a hop log to the mission service so the platform can render the
     end-to-end chain for the mission view and the kill switch.

The class accepts an `upstream_token_provider` callable so the originator
(Backend) can plug in "give me the user's OIDC access token" while
intermediate agents (SCA) plug in "give me whatever bearer arrived on the
inbound request that triggered this call." Same code path, different source.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import structlog

from .config import AgentConfig
from .keys import Keypair
from .mission import current_mission_id, MISSION_HEADER
from .signing import sign_request
from .token_exchange import ExchangedToken, TokenExchangeClient

log = structlog.get_logger()


UpstreamTokenProvider = Callable[[], Awaitable[str | None]]


class SignedClient:
    """
    Per-call wrapper. Construct once per outbound conversation; the SDK
    re-uses the underlying httpx pool. `audience` is the downstream
    agent_id_url that the issued token will be valid for.
    """

    def __init__(
        self,
        *,
        cfg: AgentConfig,
        keypair: Keypair,
        token_exchange: TokenExchangeClient,
        upstream_token_provider: UpstreamTokenProvider,
        mission_logger: "MissionHopLogger | None" = None,
        audience: str,
        base_url: str | None = None,
    ) -> None:
        self._cfg = cfg
        self._keypair = keypair
        self._tx = token_exchange
        self._upstream = upstream_token_provider
        self._audience = audience
        self._mission_logger = mission_logger
        self._http = httpx.AsyncClient(base_url=base_url or "", timeout=10.0)

    async def __aenter__(self) -> "SignedClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        mission_id = current_mission_id()

        # 1. Get the upstream credential (user token, or inbound act token).
        upstream = await self._upstream()
        if not upstream:
            raise RuntimeError(
                "no upstream credential available — caller must run inside an "
                "inbound request that carried a Bearer token, or supply one explicitly"
            )

        # 2. Exchange it for a downstream-audience token via RFC 8693.
        exchanged: ExchangedToken = await self._tx.exchange(
            subject_token=upstream,
            audience=self._audience,
            mission_id=mission_id,
        )

        # 3. Build the httpx Request, attach Authorization and X-Mission-ID.
        req = self._http.build_request(method, url, **kwargs)  # type: ignore[arg-type]
        req.headers["authorization"] = f"Bearer {exchanged.access_token}"
        if mission_id:
            req.headers[MISSION_HEADER] = mission_id

        # 4. Sign per RFC 9421 (mutates req.headers).
        sign_request(
            req,
            keypair=self._keypair,
            scheme=self._cfg.signature_scheme,
            agent_id_url=self._cfg.agent_id_url,
        )

        # 5. Send.
        resp = await self._http.send(req)

        # 6. Log the hop into the mission ledger (fire-and-forget; the
        #    mission has already moved on by the time this returns).
        if self._mission_logger and mission_id:
            try:
                await self._mission_logger.log_hop(
                    mission_id=mission_id,
                    from_agent_id=self._cfg.agent_id_url,
                    to_agent_id=self._audience,
                    act_chain=exchanged.act_chain,
                    token_jti=exchanged.jti,
                )
                await self._mission_logger.log_token(
                    mission_id=mission_id,
                    jti=exchanged.jti or "",
                    issuer=self._cfg.idp_issuer_url,
                    subject=self._cfg.agent_id_url,
                    audience=exchanged.audience,
                    act_chain=exchanged.act_chain,
                    expires_at=exchanged.expires_at,
                )
            except Exception as e:  # noqa: BLE001 — never break the request because the ledger blipped
                log.warning("mission hop log failed", error=str(e))

        return resp

    # Convenience wrappers ----------------------------------------------- #

    async def get(self, url: str, **kw: object) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: object) -> httpx.Response:
        return await self.request("POST", url, **kw)

    async def put(self, url: str, **kw: object) -> httpx.Response:
        return await self.request("PUT", url, **kw)

    async def delete(self, url: str, **kw: object) -> httpx.Response:
        return await self.request("DELETE", url, **kw)


class MissionHopLogger:
    """Thin async client over mission-service. Best-effort, never blocks."""

    def __init__(self, *, mission_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = mission_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=1.5)

    async def log_hop(
        self,
        *,
        mission_id: str,
        from_agent_id: str,
        to_agent_id: str,
        act_chain: list[str],
        token_jti: str | None = None,
        span_id: str | None = None,
    ) -> None:
        await self._client.post(
            f"{self._url}/v1/missions/{mission_id}/hop",
            json={
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
                "act_chain": act_chain,
                "token_jti": token_jti,
                "span_id": span_id,
            },
        )

    async def log_token(
        self,
        *,
        mission_id: str,
        jti: str,
        issuer: str,
        subject: str,
        audience: str,
        act_chain: list[str],
        expires_at: int,
    ) -> None:
        if not jti:
            return  # the IDP didn't include a jti — skip the ledger row
        await self._client.post(
            f"{self._url}/v1/missions/{mission_id}/tokens",
            json={
                "jti": jti,
                "issuer": issuer,
                "subject": subject,
                "audience": audience,
                "act_chain": act_chain,
                "expires_at": expires_at,
            },
        )
