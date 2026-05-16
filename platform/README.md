# Platform services

Two FastAPI services that form the AAuth Mission Platform control plane:

- **`registry-service/`** — agent registry, JWKS aggregator for IDP federation, agentgateway policy renderer. Listens on `:9000`.
- **`mission-service/`** — mission state machine, token ledger, revocation. Listens on `:9001`.

Both are deliberately minimal:

- SQLite for persistence (one file per service, mounted via PVC).
- No authentication on admin endpoints — in v1 we trust the namespace boundary. P0-2 should layer SPIFFE-attested mTLS between control plane and clients.
- Structured logging via `structlog` so the demo flow shows clearly in `kubectl logs`.

## Local dev

```bash
cd registry-service
uv sync && uv run uvicorn main:app --reload --port 9000

cd mission-service
uv sync && uv run uvicorn main:app --reload --port 9001
```

## Endpoint cheat sheet

### Registry — `:9000`

| Method | Path                            | Purpose                                              |
| ------ | ------------------------------- | ---------------------------------------------------- |
| POST   | `/v1/agents`                    | Operator pre-registers an agent → bootstrap token    |
| POST   | `/v1/agents/{id}/enroll`        | Agent self-enrolls (token + JWKS + PoP signature)    |
| POST   | `/v1/agents/{id}/rotate`        | Authenticated key rotation (current-key signature)   |
| DELETE | `/v1/agents/{id}`               | Operator revokes agent                               |
| GET    | `/v1/agents`                    | List registered agents                               |
| GET    | `/v1/agents/{id}`               | Fetch one                                            |
| GET    | `/v1/agents/jwks.json`          | **IDP federates against this** — aggregate JWKS      |
| GET    | `/v1/policy/render`             | agentgateway-flavored policy YAML                    |

### Mission — `:9001`

| Method | Path                                  | Purpose                                          |
| ------ | ------------------------------------- | ------------------------------------------------ |
| POST   | `/v1/missions`                        | Backend creates a mission (user, originator)     |
| GET    | `/v1/missions/{id}`                   | Full mission view (state, hops, tokens)          |
| GET    | `/v1/missions`                        | List, filter by user / state                     |
| PATCH  | `/v1/missions/{id}`                   | Backend marks completed / failed                 |
| POST   | `/v1/missions/{id}/revoke`            | Kill switch                                      |
| POST   | `/v1/missions/{id}/hop`               | Agents log a propagation hop                     |
| POST   | `/v1/missions/{id}/tokens`            | RFC 8693 exchange broker logs an issued token    |
| GET    | `/v1/missions/{id}/tokens`            | Token ledger entries for a mission               |

## TODOs left in code

Grep for `TODO(P0-2)` / `TODO(P0-3)` to find spots where the demo skips real-world hardening (RFC 9421 signature verification, agentgateway hot reload, structured policy push).
