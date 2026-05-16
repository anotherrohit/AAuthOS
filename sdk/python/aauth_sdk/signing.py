"""
RFC 9421 HTTP Message Signatures — minimal implementation for AAuth.

Covers only the subset the AAuth spec requires:
  - Ed25519 key, alg = "ed25519"
  - Covered components: @method, @authority, @path, @query, content-digest
  - Signature-Input header per § 4.3
  - Signature header per § 4.2
  - Signature-Key header per AAuth spec (HWK or JWKS scheme)

Outbound signing is exposed via `sign_request(...)` which mutates the headers
of an httpx.Request in place. Inbound verification is in verification.py.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Iterable

from httpx import Request as HttpxRequest

from .keys import Keypair

# Components we always cover, in order. RFC 9421 §2.2 derived components plus
# content-digest for body integrity.
_COVERED_COMPONENTS: tuple[str, ...] = (
    "@method",
    "@authority",
    "@path",
    "@query",
    "content-digest",
)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _content_digest(body: bytes) -> str:
    """RFC 9530 sha-256 content digest."""
    h = hashlib.sha256(body).digest()
    return f"sha-256=:{_b64(h)}:"


def _build_signature_base(
    *,
    method: str,
    authority: str,
    path: str,
    query: str,
    content_digest: str,
    sig_params: str,
) -> bytes:
    lines = [
        f'"@method": {method.upper()}',
        f'"@authority": {authority}',
        f'"@path": {path}',
        f'"@query": {query or "?"}',
        f'"content-digest": {content_digest}',
        f'"@signature-params": {sig_params}',
    ]
    return "\n".join(lines).encode("utf-8")


@dataclass(slots=True)
class SignatureFields:
    signature_input: str
    signature: str
    signature_key: str
    content_digest: str


def sign_request(
    request: HttpxRequest,
    *,
    keypair: Keypair,
    scheme: str,                  # 'hwk' | 'jwks' | 'jwt'
    agent_id_url: str | None = None,
    sig_label: str = "aauth",
) -> SignatureFields:
    """
    Sign `request` per RFC 9421 and AAuth spec §10. Mutates request.headers
    in place to add `content-digest`, `signature-input`, `signature`, and
    `signature-key`. Returns the four header values for logging.
    """
    body = request.content or b""
    digest = _content_digest(body)
    request.headers["content-digest"] = digest

    url = request.url
    authority = f"{url.host}:{url.port}" if url.port else url.host
    path = url.path or "/"
    query = url.query.decode() if isinstance(url.query, bytes) else (url.query or "")

    created = int(time.time())
    nonce = secrets.token_urlsafe(12)
    covered = " ".join(f'"{c}"' for c in _COVERED_COMPONENTS)
    sig_params = f'({covered});created={created};keyid="{keypair.kid}";alg="ed25519";nonce="{nonce}"'

    base = _build_signature_base(
        method=request.method,
        authority=authority,
        path=path,
        query=query,
        content_digest=digest,
        sig_params=sig_params,
    )
    sig = keypair.sign(base)
    sig_b64 = _b64(sig)

    request.headers["signature-input"] = f"{sig_label}={sig_params}"
    request.headers["signature"] = f"{sig_label}=:{sig_b64}:"

    if scheme == "hwk":
        jwk = keypair.to_public_jwk()
        sig_key = (
            f'scheme=hwk kty="{jwk["kty"]}" crv="{jwk["crv"]}" '
            f'x="{jwk["x"]}" kid="{jwk["kid"]}"'
        )
    elif scheme == "jwks":
        if not agent_id_url:
            raise ValueError("agent_id_url required for jwks scheme")
        sig_key = f'scheme=jwks id="{agent_id_url}" kid="{keypair.kid}"'
    elif scheme == "jwt":
        sig_key = "scheme=jwt"  # token carried in Authorization, not here
    else:
        raise ValueError(f"unknown signature scheme: {scheme}")

    request.headers["signature-key"] = sig_key

    return SignatureFields(
        signature_input=request.headers["signature-input"],
        signature=request.headers["signature"],
        signature_key=sig_key,
        content_digest=digest,
    )
