"""
Inbound signature verification — accepts incoming AAuth-signed requests.

Reads the signature-key header to discover which agent signed the request,
fetches that agent's JWKS from the registry (cached), then validates the
RFC 9421 signature against the signature-base reconstructed from the
request.

For the demo this only covers the JWKS scheme — the HWK scheme works the
same way but pulls the JWK directly out of the signature-key header rather
than fetching from the registry.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .signing import _build_signature_base  # reuse the canonicalizer

log = structlog.get_logger()

_TTL_SECONDS = 30


@dataclass(slots=True)
class _CachedJwks:
    keys: list[dict[str, Any]]
    fetched_at: float


class SignatureVerifier:
    """
    Reusable verifier — caches per-agent JWKS lookups against the platform
    registry for `_TTL_SECONDS`. One instance per process is enough.
    """

    def __init__(self, *, registry_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._registry_url = registry_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=2.0)
        self._cache: dict[str, _CachedJwks] = {}

    async def verify(self, *, method: str, url: httpx.URL, headers: dict[str, str], body: bytes) -> dict[str, str]:
        """
        Verify the inbound request. Returns the parsed signature-key fields
        (notably {"agent_id_url", "kid", ...}) on success. Raises
        InvalidSignature otherwise.
        """
        sig_key = headers.get("signature-key", "")
        sig_input = headers.get("signature-input", "")
        sig_hdr = headers.get("signature", "")
        digest = headers.get("content-digest", "")

        if not (sig_key and sig_input and sig_hdr and digest):
            raise InvalidSignature("missing one of: signature-key, signature-input, signature, content-digest")

        fields = _parse_signature_key(sig_key)
        if fields.get("scheme") != "jwks":
            raise InvalidSignature(f"only jwks scheme supported in this demo; got {fields.get('scheme')}")

        agent_id = fields["id"]
        kid = fields["kid"]
        jwk = await self._lookup_key(agent_id, kid)
        if not jwk:
            raise InvalidSignature(f"no JWK for agent={agent_id} kid={kid}")

        pub = Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"]))

        # Reconstruct the signature base from the actual request.
        sig_params = _extract_sig_params(sig_input)
        authority = f"{url.host}:{url.port}" if url.port else url.host
        base = _build_signature_base(
            method=method,
            authority=authority,
            path=url.path or "/",
            query=(url.query.decode() if isinstance(url.query, bytes) else (url.query or "")),
            content_digest=digest,
            sig_params=sig_params,
        )

        sig_bytes = _decode_signature(sig_hdr)
        try:
            pub.verify(sig_bytes, base)
        except InvalidSignature:
            log.warning("signature verification failed", agent=agent_id, kid=kid)
            raise

        return fields

    # ------------------------------------------------------------------ #

    async def _lookup_key(self, agent_id_url: str, kid: str) -> dict[str, Any] | None:
        slug = agent_id_url.rsplit("/", 1)[-1]
        cached = self._cache.get(slug)
        now = time.time()
        if cached and (now - cached.fetched_at) < _TTL_SECONDS:
            return _select_jwk(cached.keys, kid)

        resp = await self._client.get(f"{self._registry_url}/v1/agents/{slug}")
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("lifecycle_state") != "active":
            log.warning("agent not active", agent=agent_id_url, state=body.get("lifecycle_state"))
            return None
        keys = (body.get("jwks_json") or {}).get("keys", [])
        self._cache[slug] = _CachedJwks(keys=keys, fetched_at=now)
        return _select_jwk(keys, kid)


def _select_jwk(keys: list[dict[str, Any]], kid: str) -> dict[str, Any] | None:
    for k in keys:
        if k.get("kid") == kid:
            return k
    # Fallback to the first key — agents may not include kid for HWK calls.
    return keys[0] if keys else None


def _parse_signature_key(hdr: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in re.split(r"\s+", hdr.strip()):
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v.strip('"')
    return out


def _extract_sig_params(sig_input: str) -> str:
    # sig_input looks like:  aauth=(...);created=...;keyid="..."
    _, _, params = sig_input.partition("=")
    return params


def _decode_signature(sig_hdr: str) -> bytes:
    # sig_hdr looks like:  aauth=:<base64>:
    _, _, val = sig_hdr.partition("=")
    val = val.strip(": ")
    return base64.b64decode(val)


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)
