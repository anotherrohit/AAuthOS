"""
Environment-driven config. Same env vars as the workload manifests so a
developer can run an agent locally with the same secrets the cluster injects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentConfig:
    # Identity (issued by the registry on pre-registration)
    bootstrap_token: str
    agent_id_url: str

    # Platform control plane
    registry_url: str
    mission_url: str
    gateway_url: str

    # IDP (RadiantLogic or Keycloak — both are flavored the same here)
    idp_issuer_url: str
    idp_token_exchange_url: str
    idp_jwks_url: str
    idp_flavor: str = "radiantlogic"

    # Signing
    signature_scheme: str = "jwks"      # 'hwk' | 'jwks' | 'jwt'
    signing_alg: str = "EdDSA"
    key_size_bits: int = 256            # Ed25519 is fixed at 256

    # Server side
    jwks_path: str = "/jwks.json"
    metadata_path: str = "/.well-known/aauth-agent"

    # Where the SDK persists keys across restarts (a Secret-backed volume
    # mount in K8s, or /tmp/aauth-keys for local dev). The SDK never logs
    # private keys.
    key_state_dir: str = "/var/lib/aauth"

    @classmethod
    def from_env(cls) -> "AgentConfig":
        def clean(value: str) -> str:
            return value.strip()

        def req(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                raise RuntimeError(f"missing required env var: {name}")
            return clean(v)

        def opt(name: str, default: str = "") -> str:
            return clean(os.environ.get(name, default))

        return cls(
            bootstrap_token=req("AAUTH_BOOTSTRAP_TOKEN"),
            agent_id_url=req("AAUTH_AGENT_ID_URL"),
            registry_url=req("AAUTH_REGISTRY_URL"),
            mission_url=opt("AAUTH_MISSION_URL"),
            gateway_url=opt("AAUTH_GATEWAY_URL"),
            idp_issuer_url=req("IDP_ISSUER_URL"),
            idp_token_exchange_url=req("IDP_TOKEN_EXCHANGE_URL"),
            idp_jwks_url=req("IDP_JWKS_URL"),
            idp_flavor=opt("IDP_FLAVOR", "radiantlogic"),
            signature_scheme=opt("AAUTH_SIGNATURE_SCHEME", "jwks"),
            key_state_dir=opt("AAUTH_KEY_STATE_DIR", "/var/lib/aauth"),
        )
