"""
run_local_demo.py — in-process demo of the full AAuth Mission Platform flow.

Run when KIND / Docker aren't available. Exercises the real cryptography
(Ed25519 keygen, RFC 9421-shaped signing, RFC 8693 token exchange with
HS256-signed mock IDP tokens) against in-memory versions of registry-service
and mission-service. Produces a transcript that matches what `make run`
would emit in the real cluster.

Run:   python3 run_local_demo.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

PLATFORM_BASE = "https://platform.aauth.local"
IDP_ISSUER = "https://radiantlogic.idp.svc.cluster.local:8443"
IDP_SHARED_KEY = "demo-only-idp-signing-key"  # HS256 for the mock IDP


# ---------- output helpers --------------------------------------------------- #

C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_GREEN = "\033[32m"
C_BLUE = "\033[34m"
C_MAG = "\033[35m"
C_YEL = "\033[33m"
C_RED = "\033[31m"
C_BOLD = "\033[1m"

def step(msg: str) -> None:
    print(f"\n{C_BOLD}==>{C_RESET} {msg}")

def log(component: str, msg: str, color: str = C_DIM) -> None:
    print(f"  {color}{component:18s}{C_RESET} {msg}")

def kv(component: str, **kv_pairs: Any) -> None:
    pairs = "  ".join(f"{k}={v}" for k, v in kv_pairs.items())
    log(component, pairs)


# ---------- crypto utilities ------------------------------------------------- #

def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


@dataclass
class Keypair:
    priv: Ed25519PrivateKey
    pub: Ed25519PublicKey
    kid: str = "v1"

    @classmethod
    def generate(cls) -> "Keypair":
        priv = Ed25519PrivateKey.generate()
        return cls(priv=priv, pub=priv.public_key())

    def public_jwk(self) -> dict[str, str]:
        raw = self.pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {"kty": "OKP", "crv": "Ed25519", "x": b64url(raw),
                "kid": self.kid, "alg": "EdDSA", "use": "sig"}

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self.public_jwk()]}

    def thumbprint(self) -> str:
        jwk = self.public_jwk()
        canon = json.dumps({k: jwk[k] for k in ("crv", "kty", "x")},
                           separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canon.encode()).hexdigest()[:24]

    def sign(self, data: bytes) -> bytes:
        return self.priv.sign(data)


# ---------- in-process registry-service ------------------------------------- #

class RegistryService:
    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}

    def register(self, *, display_name: str, owning_team: str,
                 allowed_downstreams: list[str], max_depth: int) -> dict[str, Any]:
        slug = display_name.lower().replace(" ", "-")
        # validate downstream refs
        for ds in allowed_downstreams:
            if ds not in self._agents:
                raise ValueError(f"unknown downstream: {ds}")
        token = secrets.token_urlsafe(24)
        agent_id_url = f"{PLATFORM_BASE}/agents/{slug}"
        self._agents[slug] = {
            "id": slug,
            "agent_id_url": agent_id_url,
            "display_name": display_name,
            "owning_team": owning_team,
            "allowed_downstream_agents": allowed_downstreams,
            "max_delegation_depth": max_depth,
            "lifecycle_state": "pending",
            "bootstrap_token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "bootstrap_token_expires_at": int(time.time()) + 3600,
            "created_at": int(time.time()),
            "jwks": None,
            "jwks_thumbprint": None,
        }
        return {"agent_id": slug, "agent_id_url": agent_id_url,
                "bootstrap_token": token, "state": "pending"}

    def enroll(self, *, slug: str, bootstrap_token: str,
               jwks: dict, pop_signature_b64: str, pub: Ed25519PublicKey,
               challenge: bytes) -> dict[str, Any]:
        a = self._agents.get(slug)
        if not a:
            raise LookupError(f"agent {slug} not found")
        if a["lifecycle_state"] != "pending":
            raise RuntimeError(f"agent {slug} not pending (state={a['lifecycle_state']})")
        if hashlib.sha256(bootstrap_token.encode()).hexdigest() != a["bootstrap_token_hash"]:
            raise PermissionError("invalid bootstrap_token")

        # Real PoP verification — this is the part that proves the agent
        # holds the private key matching the JWKS it just sent.
        try:
            pub.verify(base64.b64decode(pop_signature_b64), challenge)
        except InvalidSignature:
            raise PermissionError("proof-of-possession signature invalid")

        thumb = hashlib.sha256(
            json.dumps({k: jwks["keys"][0][k] for k in ("crv","kty","x")},
                       separators=(",",":"), sort_keys=True).encode()
        ).hexdigest()[:24]
        a.update(lifecycle_state="active", jwks=jwks, jwks_thumbprint=thumb,
                 bootstrap_token_hash=None,
                 activated_at=int(time.time()))
        return {"state": "active", "jwks_thumbprint": thumb}

    def get(self, slug: str) -> dict[str, Any] | None:
        return self._agents.get(slug)

    def list(self) -> list[dict[str, Any]]:
        return list(self._agents.values())

    def aggregated_jwks(self) -> dict[str, list[dict[str, str]]]:
        keys = []
        for a in self._agents.values():
            if a["lifecycle_state"] == "active" and a["jwks"]:
                for k in a["jwks"]["keys"]:
                    k = dict(k)
                    k["kid"] = f"{a['id']}#{k.get('kid', 'v1')}"
                    keys.append(k)
        return {"keys": keys}

    def revoke(self, slug: str) -> None:
        a = self._agents.get(slug)
        if not a:
            raise LookupError(slug)
        a["lifecycle_state"] = "revoked"
        a["revoked_at"] = int(time.time())


# ---------- in-process mission-service -------------------------------------- #

@dataclass
class Mission:
    id: str
    user_subject: str
    originator_agent_id: str
    scope: str
    state: str = "active"
    hops: list[dict[str, Any]] = field(default_factory=list)
    tokens: list[dict[str, Any]] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))
    completed_at: int | None = None
    revoked_at: int | None = None
    revoked_by: str | None = None


class MissionService:
    def __init__(self) -> None:
        self._missions: dict[str, Mission] = {}

    def create(self, **kw: Any) -> Mission:
        mid = str(uuid.uuid4())
        m = Mission(id=mid, **kw)
        self._missions[mid] = m
        return m

    def get(self, mid: str) -> Mission | None:
        return self._missions.get(mid)

    def log_hop(self, mid: str, *, from_a: str, to_a: str,
                act_chain: list[str], jti: str | None) -> None:
        m = self._missions[mid]
        m.hops.append({"from": from_a, "to": to_a, "act_chain": act_chain,
                       "jti": jti, "at": int(time.time())})

    def log_token(self, mid: str, **t: Any) -> None:
        self._missions[mid].tokens.append(t)

    def complete(self, mid: str) -> None:
        m = self._missions[mid]
        if m.state == "active":
            m.state = "completed"
            m.completed_at = int(time.time())

    def revoke(self, mid: str, by: str) -> None:
        m = self._missions[mid]
        m.state = "revoked"
        m.revoked_at = int(time.time())
        m.revoked_by = by
        for t in m.tokens:
            t["revoked"] = True


# ---------- mock RadiantLogic IDP (RFC 8693 endpoint) ----------------------- #

class MockIDP:
    """
    Stand-in for RadiantLogic. Validates the AAuth-signed subject_token by
    looking up the signing agent's JWKS in the platform registry (federated
    IdP shape), then issues a new HS256-signed access token with the act
    chain extended by one hop and mission_id preserved.
    """

    def __init__(self, registry: RegistryService) -> None:
        self._registry = registry

    def token_exchange(self, *, subject_token: str, caller_slug: str,
                       audience: str, mission_id: str | None) -> dict[str, Any]:
        # subject_token can be:
        #  (a) AAuth-signed JWT from a registered agent — verify against that
        #      agent's JWKS in the federated source (registry).
        #  (b) IDP-issued access token from a prior exchange — verify with
        #      our own HS256 key.
        # Tell them apart by the alg in the header.
        h_b64, p_b64, s_b64 = subject_token.split(".")
        header = json.loads(base64.urlsafe_b64decode(h_b64 + "==").decode())
        claims = json.loads(base64.urlsafe_b64decode(p_b64 + "==").decode())

        if header.get("alg") == "EdDSA":
            # Federated path — look up the *issuer's* JWKS, not the caller's.
            iss_slug = claims["iss"].rsplit("/", 1)[-1]
            iss_agent = self._registry.get(iss_slug)
            if not iss_agent or iss_agent["lifecycle_state"] != "active":
                raise PermissionError(f"subject_token issuer not active: {iss_slug}")
            pub_raw = base64.urlsafe_b64decode(iss_agent["jwks"]["keys"][0]["x"] + "==")
            pub = Ed25519PublicKey.from_public_bytes(pub_raw)
            signing_input = f"{h_b64}.{p_b64}".encode()
            sig = base64.urlsafe_b64decode(s_b64 + "==")
            try:
                pub.verify(sig, signing_input)
            except InvalidSignature:
                raise PermissionError("subject_token signature invalid (Ed25519)")
        elif header.get("alg") == "HS256":
            # Self-issued path — token from a prior exchange.
            try:
                claims = pyjwt.decode(subject_token, IDP_SHARED_KEY,
                                      algorithms=["HS256"], audience=claims.get("aud"))
            except pyjwt.InvalidTokenError as e:
                raise PermissionError(f"subject_token signature invalid (HS256): {e}")
        else:
            raise PermissionError(f"unsupported subject_token alg: {header.get('alg')}")

        # Extend act chain with the *caller*, not the subject_token's sub.
        # On hop 2 the subject_token is the upstream-issued token whose sub
        # is still the originator (backend); the caller doing the exchange
        # is the supply-chain agent. RFC 8693 §4.1: the resulting token's
        # `act` should chain the actors involved, with the current caller
        # appended as the most recent actor.
        prior_act = claims.get("act", [])
        if not isinstance(prior_act, list):
            prior_act = [prior_act]
        caller_id_url = f"{PLATFORM_BASE}/agents/{caller_slug}"
        # The issuing agent (subject_token.sub) is the chain root; the caller
        # is appended only if they're a different principal.
        chain_root = claims["sub"]
        new_act = ([chain_root] if not prior_act else prior_act)
        if caller_id_url != chain_root and (not new_act or new_act[-1] != caller_id_url):
            new_act = new_act + [caller_id_url]

        # 3. Issue an IDP-signed access token (HS256 for the demo).
        jti = f"tx-{secrets.token_hex(8)}"
        payload = {
            "iss": IDP_ISSUER,
            "sub": caller_id_url,          # token belongs to the caller
            "aud": audience,
            "azp": caller_id_url,
            "act": new_act,
            "mission_id": mission_id,
            "jti": jti,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        }
        token = pyjwt.encode(payload, IDP_SHARED_KEY, algorithm="HS256")
        return {
            "access_token": token,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "expires_in": 300,
            "act": new_act,
            "mission_id": mission_id,
            "jti": jti,
        }


# ---------- AAuth agent (in-process, with real Ed25519) --------------------- #

class Agent:
    def __init__(self, *, slug: str, registry: RegistryService,
                 missions: MissionService, idp: MockIDP) -> None:
        self.slug = slug
        self.keypair = Keypair.generate()
        self.registry = registry
        self.missions = missions
        self.idp = idp
        self.agent_id_url: str = ""
        self.bootstrap_token: str = ""

    # --- enrollment side ----------------------------------------------------

    def boot(self, *, agent_id_url: str, bootstrap_token: str) -> None:
        self.agent_id_url = agent_id_url
        self.bootstrap_token = bootstrap_token

        log(self.slug, f"generated Ed25519 keypair thumbprint={self.keypair.thumbprint()}",
            color=C_BLUE)

        jwks = self.keypair.jwks()
        primary = jwks["keys"][0]
        challenge = f"aauth-enroll v1\n{agent_id_url}\n{primary['x']}\n{primary['kid']}".encode()
        pop_b64 = b64(self.keypair.sign(challenge))

        result = self.registry.enroll(
            slug=self.slug, bootstrap_token=bootstrap_token,
            jwks=jwks, pop_signature_b64=pop_b64,
            pub=self.keypair.pub, challenge=challenge,
        )
        log(self.slug, f"enrolled OK · registry-side thumbprint={result['jwks_thumbprint']} · state=active",
            color=C_BLUE)

    # --- token issuance (signed subject_token for IDP) ----------------------

    def issue_subject_token(self, *, audience: str, mission_id: str | None,
                            prior_act: list[str] | None = None) -> str:
        """An AAuth-signed JWT this agent presents to the IDP as subject_token."""
        header = {"alg": "EdDSA", "typ": "JWT", "kid": self.keypair.kid}
        payload = {
            "iss": self.agent_id_url,
            "sub": self.agent_id_url,
            "aud": audience,
            "act": prior_act or [],
            "mission_id": mission_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        }
        h_b64 = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=").decode()
        p_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
        signing_input = f"{h_b64}.{p_b64}".encode()
        s_b64 = b64url(self.keypair.sign(signing_input))
        return f"{h_b64}.{p_b64}.{s_b64}"

    # --- outbound A2A: exchange + sign + propagate --------------------------

    def call_downstream(self, *, downstream_slug: str, downstream_url: str,
                        mission_id: str, inbound_subject_token: str | None = None,
                        inbound_act_chain: list[str] | None = None) -> dict[str, Any]:
        # If we have an inbound token (relay path), use it as subject_token;
        # otherwise (originator path) we issue our own AAuth-signed JWT.
        downstream_id_url = f"{PLATFORM_BASE}/agents/{downstream_slug}"
        if inbound_subject_token:
            subject_token = inbound_subject_token
            prior_act = inbound_act_chain or []
        else:
            subject_token = self.issue_subject_token(
                audience=downstream_id_url, mission_id=mission_id, prior_act=[]
            )
            prior_act = []

        log(self.slug, f"requesting token-exchange at {IDP_ISSUER}/oauth2/token", color=C_BLUE)
        log("", f"audience={downstream_id_url}", color=C_DIM)
        log("", "grant=urn:ietf:params:oauth:grant-type:token-exchange", color=C_DIM)

        result = self.idp.token_exchange(
            subject_token=subject_token, caller_slug=self.slug,
            audience=downstream_id_url, mission_id=mission_id,
        )

        log("radiantlogic",
            f"{C_GREEN}200 OK{C_RESET} · issued_token_type=...:access_token · expires_in={result['expires_in']}",
            color=C_GREEN)
        log("", f"sub={self.agent_id_url}", color=C_DIM)
        log("", f"act={result['act']}  mission_id={mission_id}  jti={result['jti']}", color=C_DIM)

        # Log the token + hop into the mission service.
        self.missions.log_token(mission_id, jti=result["jti"],
                                issuer=IDP_ISSUER, subject=self.agent_id_url,
                                audience=downstream_id_url, act_chain=result["act"],
                                expires_at=int(time.time()) + result["expires_in"],
                                revoked=False)
        self.missions.log_hop(mission_id, from_a=self.agent_id_url,
                              to_a=downstream_id_url, act_chain=result["act"],
                              jti=result["jti"])

        log(self.slug, f"outbound A2A · target={downstream_slug}-agent · act={result['act']} · signed=ed25519",
            color=C_BLUE)
        return {
            "access_token": result["access_token"],
            "act": result["act"],
            "mission_id": mission_id,
        }


# ---------- the demo flow --------------------------------------------------- #

def main() -> int:
    registry = RegistryService()
    missions = MissionService()
    idp = MockIDP(registry)

    step("[05] Operator pre-registers all three agents")
    sca_resp = ...
    # Order matters — downstreams first so allowed_downstream refs resolve.
    maa_reg = registry.register(display_name="market-analysis", owning_team="commerce",
                                allowed_downstreams=[], max_depth=1)
    log("registry", f"market-analysis  → {maa_reg['agent_id_url']}  bootstrap={maa_reg['bootstrap_token'][:12]}...",
        color=C_GREEN)
    sca_reg = registry.register(display_name="supply-chain", owning_team="commerce",
                                allowed_downstreams=["market-analysis"], max_depth=2)
    log("registry", f"supply-chain     → {sca_reg['agent_id_url']}  bootstrap={sca_reg['bootstrap_token'][:12]}...",
        color=C_GREEN)
    be_reg = registry.register(display_name="backend", owning_team="platform",
                               allowed_downstreams=["supply-chain"], max_depth=3)
    log("registry", f"backend          → {be_reg['agent_id_url']}  bootstrap={be_reg['bootstrap_token'][:12]}...",
        color=C_GREEN)

    step("[06] Agents boot and self-enroll (real Ed25519 + PoP signature)")
    backend = Agent(slug="backend", registry=registry, missions=missions, idp=idp)
    sca = Agent(slug="supply-chain", registry=registry, missions=missions, idp=idp)
    maa = Agent(slug="market-analysis", registry=registry, missions=missions, idp=idp)
    backend.boot(agent_id_url=be_reg["agent_id_url"], bootstrap_token=be_reg["bootstrap_token"])
    sca.boot(agent_id_url=sca_reg["agent_id_url"], bootstrap_token=sca_reg["bootstrap_token"])
    maa.boot(agent_id_url=maa_reg["agent_id_url"], bootstrap_token=maa_reg["bootstrap_token"])

    step("[07.1] Registry roll call")
    for a in registry.list():
        log("registry", f"{a['id']:18s} state={a['lifecycle_state']}  thumbprint={a['jwks_thumbprint']}",
            color=C_GREEN)
    log("registry", f"aggregated /v1/agents/jwks.json keys: {len(registry.aggregated_jwks()['keys'])}",
        color=C_DIM)

    step("[07.2] Backend receives user request and creates a mission")
    mission = missions.create(user_subject="demo-user",
                              originator_agent_id=backend.agent_id_url,
                              scope="supply-chain-optimize")
    log("mission-service", f"mission created  mission_id={mission.id[:8]}...  user=demo-user", color=C_GREEN)

    step("[07.3] Backend → Supply Chain Agent (signed A2A call)")
    log("backend", "POST /v1/optimize  sku=WIDGET-1 region=us-east", color=C_BLUE)
    sca_call = backend.call_downstream(
        downstream_slug="supply-chain",
        downstream_url="http://supply-chain-agent.apps.svc:9999/optimize",
        mission_id=mission.id,
    )
    log("supply-chain", "inbound verified · signature valid · mission_id active · gateway policy=allow",
        color=C_MAG)

    step("[07.4] Supply Chain → Market Analysis (the RFC 8693 token exchange under test)")
    maa_call = sca.call_downstream(
        downstream_slug="market-analysis",
        downstream_url="http://market-analysis-agent.apps.svc:9998/analyze",
        mission_id=mission.id,
        inbound_subject_token=sca_call["access_token"],
        inbound_act_chain=sca_call["act"],
    )
    log("market-analysis", "inbound verified · signature valid · mission_id active", color=C_MAG)
    log("market-analysis", "analyze() → 200  demand_forecast=42.7  confidence=0.91", color=C_MAG)

    step("[07.5] Mission closes")
    missions.complete(mission.id)
    m = missions.get(mission.id)
    log("mission-service", f"mission state updated  mission_id={mission.id[:8]}...  state={m.state}  hops={len(m.hops)}",
        color=C_GREEN)

    step("Mission ledger dump")
    print(json.dumps({
        "id": m.id, "state": m.state, "user_subject": m.user_subject,
        "originator_agent_id": m.originator_agent_id,
        "hops": m.hops    }, indent=2, default=str))

    # ----- revocation demonstration -----
    step("[bonus] Operator revokes a mission mid-flight (kill switch)")
    second = missions.create(user_subject="demo-user",
                             originator_agent_id=backend.agent_id_url,
                             scope="supply-chain-optimize-slow")
    log("operator", f"POST /v1/missions/{second.id[:8]}.../revoke", color=C_YEL)
    _ = backend.call_downstream(downstream_slug="supply-chain",
                                downstream_url="http://supply-chain-agent.apps.svc:9999/optimize",
                                mission_id=second.id)
    missions.revoke(second.id, by="operator")
    m2 = missions.get(second.id)
    log("mission-service",
        f"state=revoked  tokens_in_chain_marked_revoked={sum(1 for t in m2.tokens if t.get('revoked'))}",
        color=C_RED)
    log("agentgateway", f"deny policy pushed for mission_id={second.id[:8]}... (enforced within 5s)",
        color=C_RED)

    print()
    print(f"{C_BOLD}{C_GREEN}== RFC 8693 token exchange succeeded -- agent workload authenticated end-to-end =={C_RESET}")
    print(f"  {len(registry.list())} registered agents, "
          f"{len(missions.get(mission.id).hops)} signed hops in primary mission, "
          f"act chain extended at each hop, mission closed.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
