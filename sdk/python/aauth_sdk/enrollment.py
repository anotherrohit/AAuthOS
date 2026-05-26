"""
First-boot enrollment client.

Flow (Phase 2 of Figure 1 in the PRD):
  1. Read bootstrap_token + agent_id_url from config
  2. Load or generate the Ed25519 keypair (keys.py)
  3. Build the enroll payload: JWKS + a PoP signature signed with the new
     private key over a canonical challenge string
  4. POST /v1/agents/{slug}/enroll on the registry — bearer token = bootstrap
  5. On 200, transition to active; on 401 (expired or wrong token) bail out
     with a clear error so the operator knows to re-issue a fresh token.

Idempotent: if the agent has already enrolled (registry reports state=active
and the JWKS thumbprint matches what we'd publish), this is a no-op.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

from .config import AgentConfig
from .keys import Keypair

log = structlog.get_logger()


class EnrollmentError(RuntimeError):
    pass


def _slug_for(agent_id_url: str) -> str:
    return agent_id_url.rstrip("/").rsplit("/", 1)[-1]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _pop_challenge(*, agent_id_url: str, jwks: dict[str, Any]) -> bytes:
    """
    Canonical bytes the agent signs as proof-of-possession. Includes the
    agent's identity and its declared JWKS thumbprint so the signature is
    bound to *this* enrollment specifically.
    """
    keys = jwks.get("keys") or []
    primary = keys[0] if keys else {}
    payload = f"aauth-enroll v1\n{agent_id_url}\n{primary.get('x', '')}\n{primary.get('kid', '')}"
    return payload.encode("utf-8")


async def enroll(
    *,
    cfg: AgentConfig,
    keypair: Keypair,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Run the enrollment flow. Returns the registry's response on success.
    Raises EnrollmentError on any failure — the caller (Agent.enroll) should
    let the process exit so the K8s liveness probe surfaces the problem.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    slug = _slug_for(cfg.agent_id_url)

    try:
        # Idempotency check — if we're already active and our JWKS is on
        # file, skip the network call.
        try:
            current = (await client.get(f"{cfg.registry_url}/v1/agents/{slug}")).json()
            if current.get("lifecycle_state") == "active":
                published = keypair.to_public_jwk()["x"]
             on_file = ((current.get("jwks_json") or {}).get("keys") or [{}])[0].get("x")
                if published == on_file:
                    log.info("enrollment idempotent — agent already active", agent_id=slug)
                    return {"state": "active", "agent_id": slug, "idempotent": True}
                log.warning("agent already active with a different JWKS; skipping demo re-enroll", agent_id=slug)
                return {"state": "active", "agent_id": slug, "idempotent": True, "jwks_mismatch": True}
        except Exception as e:  # noqa: BLE001 — registry may be unreachable yet
            log.debug("idempotency probe failed", error=str(e))

        jwks = keypair.to_jwks()
        challenge = _pop_challenge(agent_id_url=cfg.agent_id_url, jwks=jwks)
        pop_sig = _b64(keypair.sign(challenge))

        body = {
            "bootstrap_token": cfg.bootstrap_token,
            "jwks": jwks,
            "pop_signature": pop_sig,
        }
        resp = await client.post(f"{cfg.registry_url}/v1/agents/{slug}/enroll", json=body)
        if resp.status_code == 401:
            raise EnrollmentError(
                "registry rejected bootstrap_token (expired or invalid). "
                "Ask the operator to re-register this agent and supply a fresh token."
            )
        if resp.status_code >= 400:
            raise EnrollmentError(f"enroll failed: {resp.status_code} {resp.text}")

        out = resp.json()
        log.info("agent enrolled", agent_id=slug, thumbprint=out.get("jwks_thumbprint"))
        return out

    finally:
        if owns_client:
            await client.aclose()
