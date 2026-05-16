"""
Ed25519 keypair management for AAuth agents.

Keys are generated on first start, persisted to disk (`key_state_dir`), and
re-loaded on restart so an agent doesn't churn JWKS thumbprints across
crashes. Persistence path defaults to /var/lib/aauth — point this at a
Secret-backed mount in production.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class Keypair:
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    kid: str

    def to_public_jwk(self) -> dict[str, str]:
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": _b64url(raw),
            "kid": self.kid,
            "alg": "EdDSA",
            "use": "sig",
        }

    def to_jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self.to_public_jwk()]}

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)


def load_or_generate(*, state_dir: str, kid: str = "v1") -> Keypair:
    """
    Load an existing keypair from `state_dir/agent.key` or generate a fresh
    one and persist it. The kid is stable across restarts.
    """
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    key_path = Path(state_dir) / "agent.key"

    if key_path.exists():
        pem = key_path.read_bytes()
        priv = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            raise RuntimeError(f"key at {key_path} is not Ed25519")
    else:
        priv = Ed25519PrivateKey.generate()
        key_path.write_bytes(
            priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        os.chmod(key_path, 0o600)

    return Keypair(private_key=priv, public_key=priv.public_key(), kid=kid)
