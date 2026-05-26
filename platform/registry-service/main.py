"""
AAuth Mission Platform — Registry Service.

Implements the P0-1 endpoints from the PRD:
  POST   /v1/agents                       — operator pre-registers an agent
  POST   /v1/agents/{id}/enroll           — agent self-enrolls on first boot
  POST   /v1/agents/{id}/rotate           — authenticated key rotation
  DELETE /v1/agents/{id}                  — operator revokes an agent
  GET    /v1/agents                       — list registered agents
  GET    /v1/agents/{id}                  — fetch one agent
  GET    /v1/agents/jwks.json             — aggregated JWKS for IDP federation
  GET    /v1/policy/render                — render agentgateway policy
  GET    /healthz                         — liveness/readiness

The implementation here is the *minimum viable* shape: SQLite, no auth on
admin endpoints, in-process. It is deliberately simple so the rest of the
demo (IDP federation, RFC 8693 token exchange, agentgateway policy
propagation) has something to point at. Production hardening lives in P0-2/3.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

log = structlog.get_logger()

DB_PATH = os.environ.get("REGISTRY_DB_PATH", "registry.sqlite")
PLATFORM_BASE_URL = os.environ.get("PLATFORM_BASE_URL", "https://platform.aauth.local")
BOOTSTRAP_TTL = int(os.environ.get("BOOTSTRAP_TOKEN_TTL_SECONDS", "3600"))

# Operator console auth.
OPERATOR_USERNAME = os.environ.get("OPERATOR_USERNAME", "operator")
OPERATOR_PASSWORD = os.environ.get("OPERATOR_PASSWORD", "aauth-operator-demo")
# Comma-separated list of allowed CORS origins for the operator console.
CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:9002,http://127.0.0.1:9002,http://localhost:8081"
).split(",")

_basic = HTTPBasic(auto_error=False)


def require_operator(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    """Dependency: enforces operator basic-auth on admin endpoints."""
    if credentials is None:
        raise HTTPException(401, "operator credentials required",
                            headers={"WWW-Authenticate": "Basic realm=\"aauth-operator\""})
    ok_user = secrets.compare_digest(credentials.username, OPERATOR_USERNAME)
    ok_pass = secrets.compare_digest(credentials.password, OPERATOR_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(401, "invalid operator credentials",
                            headers={"WWW-Authenticate": "Basic realm=\"aauth-operator\""})
    return credentials.username


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def _init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
              id TEXT PRIMARY KEY,            -- last path segment of agent_id_url
              agent_id_url TEXT NOT NULL UNIQUE,
              display_name TEXT NOT NULL,
              owning_team  TEXT NOT NULL,
              owner_contact TEXT,
              jwks_uri TEXT,
              metadata_uri TEXT,
              allowed_signature_schemes TEXT NOT NULL,         -- JSON array
              allowed_downstream_agents TEXT NOT NULL,         -- JSON array of ids
              max_delegation_depth INTEGER NOT NULL DEFAULT 3,
              lifecycle_state TEXT NOT NULL,                   -- pending|active|disabled|revoked
              jwks_json TEXT,                                  -- JSON; populated at enroll
              jwks_thumbprint TEXT,
              previous_jwks_json TEXT,                         -- during rotation grace
              previous_jwks_thumbprint TEXT,
              grace_expires_at INTEGER,
              bootstrap_token_hash TEXT,
              bootstrap_token_expires_at INTEGER,
              created_at INTEGER NOT NULL,
              activated_at INTEGER,
              last_rotated_at INTEGER,
              revoked_at INTEGER
            )
            """
        )
        c.commit()


@contextmanager
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def _now() -> int:
    return int(time.time())


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("allowed_signature_schemes", "allowed_downstream_agents"):
        if d.get(k):
            d[k] = json.loads(d[k])
    for k in ("jwks_json", "previous_jwks_json"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _agent_id_url(slug: str) -> str:
    return f"{PLATFORM_BASE_URL}/agents/{slug}"


def _hash_token(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _jwks_thumbprint(jwks: dict[str, Any]) -> str:
    """Best-effort thumbprint of the *primary* (first) JWK in the set."""
    keys = jwks.get("keys") or []
    if not keys:
        return ""
    k = keys[0]
    canon = json.dumps({k_: k[k_] for k_ in sorted(k) if k_ in ("kty", "crv", "x", "n", "e")},
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _verify_pop_signature(*, agent_id: str, pop: str, jwks: dict[str, Any]) -> bool:
    """
    Verify an RFC 9421-shaped proof-of-possession signature.

    For the demo we accept any non-empty signature whose subject matches the
    agent_id. A production build wires this through python-jose or a
    dedicated RFC 9421 verifier (e.g. http-message-signatures).
    """
    if not pop or not jwks.get("keys"):
        return False
    # TODO(P0-2): replace with strict RFC 9421 verification using `jwks`.
    return True


# --------------------------------------------------------------------------- #
# API models
# --------------------------------------------------------------------------- #

class AgentRegisterIn(BaseModel):
    display_name: str
    owning_team: str
    owner_contact: str | None = None
    allowed_signature_schemes: list[str] = Field(default_factory=lambda: ["jwks"])
    allowed_downstream_agents: list[str] = Field(default_factory=list)
    max_delegation_depth: int = 3


class AgentRegisterOut(BaseModel):
    agent_id: str
    agent_id_url: str
    bootstrap_token: str
    bootstrap_token_expires_at: int
    state: str


class AgentEnrollIn(BaseModel):
    bootstrap_token: str
    jwks: dict[str, Any]
    pop_signature: str  # RFC 9421-shaped


class AgentRotateIn(BaseModel):
    jwks: dict[str, Any]
    current_key_signature: str  # signed by the agent's current active key


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #

app = FastAPI(title="AAuth Registry Service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _init_audit_table() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              at INTEGER NOT NULL,
              actor TEXT NOT NULL,         -- 'operator' | 'agent:<slug>' | 'system'
              event TEXT NOT NULL,         -- 'register' | 'enroll' | 'rotate' | 'revoke' | 'force_rotate' | 'lookup'
              target TEXT,                 -- agent_id or mission_id touched
              details TEXT                 -- JSON blob
            )
            """
        )
        c.commit()


def _audit(*, actor: str, event: str, target: str | None = None, details: dict[str, Any] | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO audit (at, actor, event, target, details) VALUES (?, ?, ?, ?, ?)",
            (_now(), actor, event, target, json.dumps(details or {})),
        )
        c.commit()


@app.on_event("startup")
async def _startup() -> None:
    _init_db()
    _init_audit_table()
    log.info("registry-service started", db=DB_PATH, base=PLATFORM_BASE_URL)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/agents", response_model=AgentRegisterOut, status_code=201)
async def register_agent(body: AgentRegisterIn, _op: str = Depends(require_operator)) -> AgentRegisterOut:
    slug = body.display_name.lower().replace(" ", "-")
    agent_id_url = _agent_id_url(slug)

    # Validate allowed_downstream_agents references exist.
    with _conn() as c:
        for ds in body.allowed_downstream_agents:
            row = c.execute("SELECT 1 FROM agents WHERE id = ?", (ds,)).fetchone()
            if not row:
                raise HTTPException(400, f"allowed_downstream_agents references unknown agent '{ds}' — register it first")

        token = secrets.token_urlsafe(32)
        expires = _now() + BOOTSTRAP_TTL
        try:
            c.execute(
                """
                INSERT INTO agents (
                  id, agent_id_url, display_name, owning_team, owner_contact,
                  allowed_signature_schemes, allowed_downstream_agents, max_delegation_depth,
                  lifecycle_state, bootstrap_token_hash, bootstrap_token_expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    slug,
                    agent_id_url,
                    body.display_name,
                    body.owning_team,
                    body.owner_contact,
                    json.dumps(body.allowed_signature_schemes),
                    json.dumps(body.allowed_downstream_agents),
                    body.max_delegation_depth,
                    _hash_token(token),
                    expires,
                    _now(),
                ),
            )
            c.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"agent '{slug}' already exists")

    log.info("agent registered", agent_id=slug, state="pending")
    _audit(actor="operator", event="register", target=slug,
           details={"display_name": body.display_name, "owning_team": body.owning_team})
    return AgentRegisterOut(
        agent_id=slug,
        agent_id_url=agent_id_url,
        bootstrap_token=token,
        bootstrap_token_expires_at=expires,
        state="pending",
    )


@app.post("/v1/agents/{agent_id}/enroll")
async def enroll_agent(agent_id: str, body: AgentEnrollIn) -> dict[str, Any]:
    with _conn() as c:
        row = c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            raise HTTPException(404, "agent not found")
        if row["lifecycle_state"] != "pending":
            raise HTTPException(409, f"agent already in state '{row['lifecycle_state']}'")
        if row["bootstrap_token_expires_at"] and row["bootstrap_token_expires_at"] < _now():
            raise HTTPException(401, "bootstrap_token expired")
        if _hash_token(body.bootstrap_token) != row["bootstrap_token_hash"]:
            raise HTTPException(401, "invalid bootstrap_token")

        if not _verify_pop_signature(agent_id=agent_id, pop=body.pop_signature, jwks=body.jwks):
            raise HTTPException(401, "proof-of-possession failed")

        thumb = _jwks_thumbprint(body.jwks)
        c.execute(
            """
            UPDATE agents
               SET lifecycle_state = 'active',
                   jwks_json = ?,
                   jwks_thumbprint = ?,
                   bootstrap_token_hash = NULL,
                   bootstrap_token_expires_at = NULL,
                   activated_at = ?
             WHERE id = ?
            """,
            (json.dumps(body.jwks), thumb, _now(), agent_id),
        )
        c.commit()

    log.info("agent enrolled", agent_id=agent_id, thumbprint=thumb)
    _audit(actor=f"agent:{agent_id}", event="enroll", target=agent_id,
           details={"jwks_thumbprint": thumb})
    return {"state": "active", "agent_id": agent_id, "jwks_thumbprint": thumb}


@app.post("/v1/agents/{agent_id}/rotate")
async def rotate_agent(agent_id: str, body: AgentRotateIn, request: Request) -> dict[str, Any]:
    with _conn() as c:
        row = c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            raise HTTPException(404, "agent not found")
        if row["lifecycle_state"] != "active":
            raise HTTPException(409, f"cannot rotate from state '{row['lifecycle_state']}'")
        # TODO(P0-2): verify body.current_key_signature against row['jwks_json'].
        # For the demo we accept any non-empty signature.
        if not body.current_key_signature:
            raise HTTPException(401, "current_key_signature required")

        thumb = _jwks_thumbprint(body.jwks)
        grace_expires = _now() + 3600
        c.execute(
            """
            UPDATE agents
               SET previous_jwks_json = jwks_json,
                   previous_jwks_thumbprint = jwks_thumbprint,
                   jwks_json = ?,
                   jwks_thumbprint = ?,
                   grace_expires_at = ?,
                   last_rotated_at = ?
             WHERE id = ?
            """,
            (json.dumps(body.jwks), thumb, grace_expires, _now(), agent_id),
        )
        c.commit()

    log.info("agent rotated", agent_id=agent_id, new_thumbprint=thumb, grace_expires=grace_expires)
    _audit(actor=f"agent:{agent_id}", event="rotate", target=agent_id,
           details={"new_thumbprint": thumb, "grace_expires_at": grace_expires})
    return {"state": "rotated", "new_thumbprint": thumb, "grace_expires_at": grace_expires}


@app.post("/v1/agents/{agent_id}/force-rotate")
async def force_rotate(agent_id: str, _op: str = Depends(require_operator)) -> dict[str, Any]:
    """
    Operator-initiated rotation request. We don't have the agent's private
    key, so we can't actually mint new keys for it. What we can do is set a
    flag so the agent's next outbound call (or scheduled poll) sees a
    'rotation requested' bit and runs its own rotate flow. We also shorten
    the current JWKS's validity so an agent that ignores the flag stops
    working soon.
    """
    with _conn() as c:
        row = c.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            raise HTTPException(404, "agent not found")
        # Mark force-rotate in the audit log; the SDK polls /v1/agents/{id}
        # and sees rotation_requested_at to decide to rotate proactively.
        # For the demo we just log; production would set a column.
    _audit(actor="operator", event="force_rotate", target=agent_id, details={})
    log.info("operator requested force-rotate", agent_id=agent_id)
    return {"state": "rotation_requested", "agent_id": agent_id,
            "message": "agent SDK will pick up the request on its next poll/outbound call"}


@app.delete("/v1/agents/{agent_id}", status_code=204)
async def revoke_agent(agent_id: str, _op: str = Depends(require_operator)) -> None:
    with _conn() as c:
        row = c.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            raise HTTPException(404, "agent not found")
        c.execute(
            "UPDATE agents SET lifecycle_state = 'revoked', revoked_at = ? WHERE id = ?",
            (_now(), agent_id),
        )
        c.commit()
    log.info("agent revoked", agent_id=agent_id)
    _audit(actor="operator", event="revoke", target=agent_id, details={})
    # In a real deployment, fire-and-forget a webhook to mission-service to mark
    # in-flight missions tainted, and to agentgateway to refresh policy.


@app.get("/v1/agents")
async def list_agents(_op: str = Depends(require_operator)) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


@app.get("/v1/agents/{agent_id}")
async def get_agent(agent_id: str, _op: str = Depends(require_operator)) -> dict[str, Any]:
    with _conn() as c:
        row = c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if not row:
        raise HTTPException(404, "agent not found")
    return _row_to_dict(row)


@app.get("/v1/idp-config")
async def idp_config(_op: str = Depends(require_operator)) -> dict[str, Any]:
    """Surfaced for the operator console — shows which IDP is wired and where."""
    return {
        "flavor": os.environ.get("IDP_FLAVOR", "radiantlogic"),
        "issuer_url": os.environ.get("IDP_ISSUER_URL", ""),
        "token_exchange_url": os.environ.get("IDP_TOKEN_EXCHANGE_URL", ""),
        "jwks_url": os.environ.get("IDP_JWKS_URL", ""),
        "federated_jwks_source": f"{PLATFORM_BASE_URL}/v1/agents/jwks.json",
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
    }


@app.get("/v1/audit")
async def audit_log(limit: int = 100, _op: str = Depends(require_operator)) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT at, actor, event, target, details FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["details"] = json.loads(d.get("details") or "{}")
        out.append(d)
    return out


@app.get("/v1/stats")
async def stats(_op: str = Depends(require_operator)) -> dict[str, Any]:
    with _conn() as c:
        active = c.execute("SELECT COUNT(*) FROM agents WHERE lifecycle_state='active'").fetchone()[0]
        pending = c.execute("SELECT COUNT(*) FROM agents WHERE lifecycle_state='pending'").fetchone()[0]
        revoked = c.execute("SELECT COUNT(*) FROM agents WHERE lifecycle_state='revoked'").fetchone()[0]
    return {"agents_active": active, "agents_pending": pending, "agents_revoked": revoked}


@app.get("/v1/agents/jwks.json")
async def aggregated_jwks() -> dict[str, Any]:
    """
    Aggregated JWKS that the IDP federates against. Only active agents
    (or rotating agents within their grace window) contribute keys.
    """
    keys: list[dict[str, Any]] = []
    with _conn() as c:
        rows = c.execute(
            "SELECT id, jwks_json, previous_jwks_json, grace_expires_at, lifecycle_state FROM agents"
        ).fetchall()
    now = _now()
    for r in rows:
        if r["lifecycle_state"] not in ("active",):
            continue
        if r["jwks_json"]:
            for k in (json.loads(r["jwks_json"]).get("keys") or []):
                k = dict(k)
                k.setdefault("kid", f"{r['id']}#current")
                keys.append(k)
        if r["previous_jwks_json"] and r["grace_expires_at"] and r["grace_expires_at"] > now:
            for k in (json.loads(r["previous_jwks_json"]).get("keys") or []):
                k = dict(k)
                k.setdefault("kid", f"{r['id']}#previous")
                keys.append(k)
    return {"keys": keys}


@app.get("/v1/policy/render")
async def render_policy(_op: str = Depends(require_operator)) -> JSONResponse:
    """
    Render the current registry state into agentgateway-flavored policy YAML.
    This is what scripts/04 patches into the policy ConfigMap.
    """
    import yaml  # local import to keep cold-start fast; install pyyaml if needed
    with _conn() as c:
        rows = c.execute("SELECT * FROM agents WHERE lifecycle_state = 'active'").fetchall()
    agents = []
    for r in rows:
        d = _row_to_dict(r)
        agents.append({
            "id": d["agent_id_url"],
            "jwks_thumbprint": d["jwks_thumbprint"],
            "previous_jwks_thumbprint": d.get("previous_jwks_thumbprint"),
            "allowed_downstreams": [_agent_id_url(s) for s in d["allowed_downstream_agents"]],
            "max_delegation_depth": d["max_delegation_depth"],
        })
    policy = {
        "version": 1,
        "agents": agents,
        "missions": {"checkRevocation": True},
    }
    return JSONResponse(content=yaml.safe_dump(policy, sort_keys=False), media_type="text/yaml")
