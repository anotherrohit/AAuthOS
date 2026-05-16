# Architecture

This file is the engineering-side companion to the [PRD](../aauth-platform-prd.md). It captures the topology, the trust model, and the threat surface for the v1 demo on KIND.

## Topology

```
┌────────────────────────── KIND cluster (aauth-demo) ──────────────────────────┐
│                                                                                │
│  ns: idp                              ns: platform                             │
│  ┌─────────────────────────┐         ┌─────────────────────────────────────┐  │
│  │  RadiantLogic IDM       │         │  registry-service  (FastAPI)        │  │
│  │  - OIDC discovery       │         │   - /v1/agents (register/enroll/    │  │
│  │  - /token (RFC 8693)    │ ◄─JWKS─┤     rotate/revoke)                  │  │
│  │  - federated IdP =      │         │   - /v1/agents/jwks.json (agg.)    │  │
│  │    platform JWKS        │         │   - SQLite                          │  │
│  │  (Keycloak in fallback) │         ├─────────────────────────────────────┤  │
│  └────────────┬────────────┘         │  mission-service  (FastAPI)         │  │
│               │                       │   - /v1/missions (create/patch/    │  │
│               │                       │     revoke)                         │  │
│               │                       │   - SQLite                          │  │
│               │                       └─────────────────────────────────────┘  │
│               │                                                                 │
│  ns: gateway  │                       ns: apps                                  │
│  ┌────────────▼────────────┐         ┌─────────────────────────────────────┐  │
│  │  agentgateway           │ ◄──signed─┤  backend (FastAPI)                │  │
│  │  - verifies signatures  │   A2A   │  └─signed A2A─►  supply-chain-agent │  │
│  │  - reads policy from    │         │                  └─signed A2A─►    │  │
│  │    platform ConfigMap   │         │                    market-analysis  │  │
│  └─────────────────────────┘         │  supply-chain-ui (React)            │  │
│                                       └─────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Trust model

| Principal                  | Identity                                              | Issued by      | Verified by         |
| -------------------------- | ----------------------------------------------------- | -------------- | ------------------- |
| Human user                 | OIDC `sub`                                            | RL / Keycloak  | Backend             |
| Backend / SCA / MAA        | `agent_id_url` from registry                          | Platform reg.  | agentgateway, IDP   |
| Agent → agent call         | RFC 9421 HTTP signature using registered JWKS         | Calling agent  | agentgateway        |
| Multi-hop delegation token | RFC 8693 exchanged token with `act` chain             | RL / Keycloak  | Downstream agent    |
| Mission                    | UUID issued by mission-service                        | Platform       | agentgateway policy |

## Sequences

For the four flows that drive this design — registration, operator/developer split, rotation/revocation, mission lifecycle — see [the PRD diagrams](../aauth-platform-prd.md#diagrams).

## Threat surface — v1 caveats

The v1 demo has two known weak points that are explicit non-goals for KubeCon, and would block any real production use:

1. **No workload attestation.** The bootstrap token is the only thing standing between an attacker and a registered agent identity. If the token leaks (chat log, env file in a tarball, etc.) between operator pre-registration and developer first-boot, an attacker can enroll a hostile JWKS in the legitimate agent's slot. Mitigation in v1: short TTL on the bootstrap token (configurable, default 1h); P1 fixes this entirely with SPIRE attestation.
2. **Platform is the trust root.** The registry's `/v1/agents/jwks.json` is what RadiantLogic federates against. If the platform is compromised, the attacker can register arbitrary agents and have them accepted everywhere. This is acceptable for a single-tenant control-plane shape and is how every comparable system works (Vault, SPIRE server, IDP itself); flagging it because the centralization is real.

Everything else — RFC 9421 signing, RFC 8693 exchange, RFC 9530 content-digest, OIDC for human login — is unchanged from upstream `aauth-full-demo` and inherits its security properties.

## Configuration surface

Per-component config lives in ConfigMaps in each namespace. The IDP toggle is realized via two values in the `platform-config` ConfigMap:

- `IDP_ISSUER_URL`        — `https://radiantlogic.idp.svc.cluster.local` or `https://keycloak.idp.svc.cluster.local`
- `IDP_TOKEN_EXCHANGE_URL` — `${IDP_ISSUER_URL}/oauth2/token` (RL) or `${IDP_ISSUER_URL}/realms/aauth/protocol/openid-connect/token` (Keycloak)

The platform services, the backend, and the agents all read those two values. Switching IDPs is a `kubectl apply` of a different ConfigMap, plus restarting the deployments that consume it.
