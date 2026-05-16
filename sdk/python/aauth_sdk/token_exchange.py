"""
RFC 8693 token exchange client.

When an agent needs to call a downstream agent, it asks the IDP (RadiantLogic
or Keycloak) to exchange its current credential for a new access token whose
`act` chain has been extended by one hop and whose audience is the
downstream agent's ID URL. The mission_id passthrough claim is preserved.

The "current credential" the agent presents is one of two things:

  - Originator path (Backend): the upstream OIDC access token the user
    issued during the consent flow. The IDP knows how to validate this
    natively (it issued it).
  - Multi-hop path (SCA, MAA): the upstream RFC 8693 token the *previous*
    agent issued for this agent. The IDP validates it via the federated
    JWKS source (the platform's /v1/agents/jwks.json).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
import structlog

log = structlog.get_logger()


GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"
ISSUED_TOKEN_TYPE_AT = "urn:ietf:params:oauth:token-type:access_token"


class TokenExchangeError(RuntimeError):
    pass


@dataclass(slots=True)
class ExchangedToken:
    access_token: str
    issued_token_type: str
    expires_in: int
    mission_id: str | None
    act_chain: list[str]
    jti: str | None
    audience: str

    @property
    def expires_at(self) -> int:
        return int(time.time()) + self.expires_in


class TokenExchangeClient:
    """One per agent. Caches issued tokens by (audience, mission_id)."""

    def __init__(
        self,
        *,
        token_endpoint: str,
        client_id: str = "platform-token-exchange",
        client_secret: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._cache: dict[tuple[str, str | None], ExchangedToken] = {}

    async def exchange(
        self,
        *,
        subject_token: str,
        audience: str,
        mission_id: str | None,
        scope: str | None = None,
    ) -> ExchangedToken:
        cache_key = (audience, mission_id)
        cached = self._cache.get(cache_key)
        # Re-use cached tokens that still have 30s of life — avoids hammering
        # the IDP on every hop in a tight retry loop.
        if cached and cached.expires_at - int(time.time()) > 30:
            return cached

        form = {
            "grant_type": GRANT_TYPE,
            "subject_token": subject_token,
            "subject_token_type": SUBJECT_TOKEN_TYPE_JWT,
            "audience": audience,
        }
        if scope:
            form["scope"] = scope
        if mission_id:
            # Both RL and Keycloak realm configs accept this as a passthrough
            # claim in the issued token (see manifests/.../02-*.yaml).
            form["mission_id"] = mission_id

        auth = None
        if self._client_secret:
            auth = (self._client_id, self._client_secret)

        resp = await self._client.post(self._endpoint, data=form, auth=auth)
        if resp.status_code >= 400:
            log.warning(
                "token exchange failed",
                status=resp.status_code,
                body=resp.text[:300],
                audience=audience,
            )
            raise TokenExchangeError(
                f"token exchange failed ({resp.status_code}): {resp.text[:200]}"
            )

        body = resp.json()
        access_token = body["access_token"]
        # Parse claims without verifying — verification happens at the
        # downstream agent + at agentgateway. We just need the structured
        # fields for logging into the mission ledger.
        try:
            claims = jwt.decode(access_token, options={"verify_signature": False})
        except Exception:  # noqa: BLE001
            claims = {}

        out = ExchangedToken(
            access_token=access_token,
            issued_token_type=body.get("issued_token_type", ISSUED_TOKEN_TYPE_AT),
            expires_in=int(body.get("expires_in", 300)),
            mission_id=claims.get("mission_id") or mission_id,
            act_chain=claims.get("act", []) if isinstance(claims.get("act"), list) else [],
            jti=claims.get("jti"),
            audience=audience,
        )
        self._cache[cache_key] = out

        log.info(
            "token exchanged",
            audience=audience,
            mission_id=out.mission_id,
            act_chain_len=len(out.act_chain),
            expires_in=out.expires_in,
        )
        return out

    def invalidate(self, *, audience: str | None = None, mission_id: str | None = None) -> None:
        """Drop cached tokens. Call on auth errors so the next call re-exchanges."""
        if audience is None and mission_id is None:
            self._cache.clear()
            return
        for key in list(self._cache.keys()):
            aud, mid = key
            if (audience is None or audience == aud) and (mission_id is None or mission_id == mid):
                self._cache.pop(key, None)
