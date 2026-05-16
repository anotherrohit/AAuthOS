"""
AAuth Mission Platform — Mission Service.

Implements the P0-3 endpoints from the PRD:
  POST   /v1/missions                    — backend creates a mission
  GET    /v1/missions/{id}               — fetch one (state, hops, tokens, user)
  GET    /v1/missions                    — list (filterable by user, state)
  PATCH  /v1/missions/{id}               — backend updates state (completed/failed)
  POST   /v1/missions/{id}/revoke        — user or operator kills mission
  POST   /v1/missions/{id}/hop           — agents log a hop (mission_id propagation)
  GET    /v1/missions/{id}/tokens        — token ledger entries for this mission
  GET    /healthz

The state machine:  active → completed | failed | revoked
                    (revoked is terminal and overrides any in-flight state)
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Literal

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

log = structlog.get_logger()

DB_PATH = os.environ.get("MISSION_DB_PATH", "missions.sqlite")
REGISTRY_URL = os.environ.get("REGISTRY_URL", "http://registry-service.platform.svc.cluster.local:9000")
GATEWAY_POLICY_URL = os.environ.get("GATEWAY_POLICY_URL", "http://agentgateway.gateway.svc.cluster.local:8080/admin/policy")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def _init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS missions (
              id TEXT PRIMARY KEY,
              user_subject TEXT NOT NULL,
              originator_agent_id TEXT NOT NULL,
              scope TEXT NOT NULL,
              state TEXT NOT NULL,                  -- active|completed|failed|revoked
              ttl_seconds INTEGER NOT NULL,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              completed_at INTEGER,
              revoked_at INTEGER,
              revoked_by TEXT,
              metadata_json TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS hops (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              mission_id TEXT NOT NULL,
              from_agent_id TEXT NOT NULL,
              to_agent_id   TEXT NOT NULL,
              act_chain TEXT NOT NULL,              -- JSON array
              token_jti TEXT,
              span_id TEXT,
              at INTEGER NOT NULL,
              FOREIGN KEY (mission_id) REFERENCES missions(id)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
              jti TEXT PRIMARY KEY,
              mission_id TEXT NOT NULL,
              issuer TEXT NOT NULL,
              subject TEXT NOT NULL,
              audience TEXT NOT NULL,
              act_chain TEXT NOT NULL,
              issued_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              revoked INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY (mission_id) REFERENCES missions(id)
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


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class MissionCreate(BaseModel):
    user_subject: str
    originator_agent_id: str
    scope: str
    ttl_seconds: int = 600
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionState(BaseModel):
    state: Literal["completed", "failed"]
    summary: dict[str, Any] = Field(default_factory=dict)


class HopLog(BaseModel):
    from_agent_id: str
    to_agent_id: str
    act_chain: list[str]
    token_jti: str | None = None
    span_id: str | None = None


class TokenLogEntry(BaseModel):
    jti: str
    issuer: str
    subject: str
    audience: str
    act_chain: list[str]
    expires_at: int


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #

app = FastAPI(title="AAuth Mission Service", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    _init_db()
    log.info("mission-service started", db=DB_PATH)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/missions", status_code=201)
async def create_mission(body: MissionCreate) -> dict[str, Any]:
    mid = str(uuid.uuid4())
    now = _now()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO missions (id, user_subject, originator_agent_id, scope, state,
                                  ttl_seconds, created_at, expires_at, metadata_json)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (mid, body.user_subject, body.originator_agent_id, body.scope,
             body.ttl_seconds, now, now + body.ttl_seconds, json.dumps(body.metadata)),
        )
        c.commit()
    log.info("mission created", mission_id=mid, user=body.user_subject, originator=body.originator_agent_id)
    return {
        "mission_id": mid,
        "state": "active",
        "expires_at": now + body.ttl_seconds,
    }


@app.get("/v1/missions/{mission_id}")
async def get_mission(mission_id: str) -> dict[str, Any]:
    with _conn() as c:
        row = c.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        if not row:
            raise HTTPException(404, "mission not found")
        hops = c.execute(
            "SELECT from_agent_id, to_agent_id, act_chain, token_jti, at FROM hops WHERE mission_id = ? ORDER BY id",
            (mission_id,),
        ).fetchall()
        tokens = c.execute(
            "SELECT jti, issuer, subject, audience, expires_at, revoked FROM tokens WHERE mission_id = ? ORDER BY issued_at",
            (mission_id,),
        ).fetchall()
    d = dict(row)
    d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
    d["hops"] = [dict(h, act_chain=json.loads(h["act_chain"])) for h in hops]
    d["tokens"] = [dict(t) for t in tokens]
    return d


@app.get("/v1/missions")
async def list_missions(
    user: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> list[dict[str, Any]]:
    q = "SELECT * FROM missions WHERE 1=1"
    params: list[Any] = []
    if user:
        q += " AND user_subject = ?"
        params.append(user)
    if state:
        q += " AND state = ?"
        params.append(state)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(q, params).fetchall()
    return [dict(r) for r in rows]


@app.patch("/v1/missions/{mission_id}")
async def update_mission(mission_id: str, body: MissionState) -> dict[str, Any]:
    with _conn() as c:
        row = c.execute("SELECT state FROM missions WHERE id = ?", (mission_id,)).fetchone()
        if not row:
            raise HTTPException(404, "mission not found")
        if row["state"] in ("completed", "failed", "revoked"):
            raise HTTPException(409, f"mission is terminal: {row['state']}")
        c.execute(
            "UPDATE missions SET state = ?, completed_at = ? WHERE id = ?",
            (body.state, _now(), mission_id),
        )
        c.commit()
    log.info("mission state updated", mission_id=mission_id, state=body.state)
    return {"mission_id": mission_id, "state": body.state}


@app.post("/v1/missions/{mission_id}/revoke")
async def revoke_mission(mission_id: str, revoked_by: str = "operator") -> dict[str, Any]:
    with _conn() as c:
        row = c.execute("SELECT state FROM missions WHERE id = ?", (mission_id,)).fetchone()
        if not row:
            raise HTTPException(404, "mission not found")
        c.execute(
            "UPDATE missions SET state = 'revoked', revoked_at = ?, revoked_by = ? WHERE id = ?",
            (_now(), revoked_by, mission_id),
        )
        c.execute(
            "UPDATE tokens SET revoked = 1 WHERE mission_id = ?",
            (mission_id,),
        )
        c.commit()

    # Best-effort: push the revocation to agentgateway so the deny list is
    # current within seconds. If the gateway is unreachable we still return
    # success — the next gateway reload will pick it up from the registry.
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(GATEWAY_POLICY_URL, json={"revoke_mission_id": mission_id})
    except Exception as e:  # noqa: BLE001
        log.warning("gateway policy push failed", error=str(e))

    log.info("mission revoked", mission_id=mission_id, revoked_by=revoked_by)
    return {"mission_id": mission_id, "state": "revoked"}


@app.post("/v1/missions/{mission_id}/hop", status_code=201)
async def log_hop(mission_id: str, body: HopLog) -> dict[str, Any]:
    with _conn() as c:
        if not c.execute("SELECT 1 FROM missions WHERE id = ?", (mission_id,)).fetchone():
            raise HTTPException(404, "mission not found")
        c.execute(
            """
            INSERT INTO hops (mission_id, from_agent_id, to_agent_id, act_chain, token_jti, span_id, at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (mission_id, body.from_agent_id, body.to_agent_id, json.dumps(body.act_chain),
             body.token_jti, body.span_id, _now()),
        )
        c.commit()
    return {"ok": True}


@app.post("/v1/missions/{mission_id}/tokens", status_code=201)
async def log_token(mission_id: str, body: TokenLogEntry) -> dict[str, Any]:
    with _conn() as c:
        if not c.execute("SELECT 1 FROM missions WHERE id = ?", (mission_id,)).fetchone():
            raise HTTPException(404, "mission not found")
        c.execute(
            """
            INSERT OR IGNORE INTO tokens (jti, mission_id, issuer, subject, audience, act_chain,
                                          issued_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (body.jti, mission_id, body.issuer, body.subject, body.audience,
             json.dumps(body.act_chain), _now(), body.expires_at),
        )
        c.commit()
    return {"ok": True}


@app.get("/v1/missions/{mission_id}/tokens")
async def list_tokens(mission_id: str) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM tokens WHERE mission_id = ? ORDER BY issued_at",
            (mission_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["act_chain"] = json.loads(d["act_chain"])
        out.append(d)
    return out
