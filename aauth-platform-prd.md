# AAuth Mission Platform — Product Requirements Document

**Status:** Draft v0.3
**Author:** Rohit
**Last updated:** 2026-05-14
**Target ship:** 2026-07-03 (KubeCon NA 2026 launch window)
**Audience:** Engineering · Security & IAM reviewers · Product / exec stakeholders
**Source artifacts:**
- [christian-posta/aauth-full-demo](https://github.com/christian-posta/aauth-full-demo) — AAuth + A2A reference implementation
- [anotherrohit/spiffe-radiantlogic](https://github.com/anotherrohit/spiffe-radiantlogic) — prior PoC for SPIRE-attested workload identity → RadiantLogic RFC 8693 token exchange (the RadiantOne integration shape we are reusing)

---

## TL;DR

We are productizing the `aauth-full-demo` reference implementation into a deployable **Mission Management Platform for agentic workloads** (working name: **AAuth Mission Platform**). The platform turns three currently-static demo concepts — ephemeral agent keypairs, opaque user-delegated tokens, and best-effort tracing — into managed primitives:

1. **A registered agent identity** with a documented join flow, a JWKS-based proof of possession, and a clean lifecycle (register → activate → rotate → revoke).
2. **A traceable token lifecycle** — every AAuth token issued is observable, attributable, and revocable through the edge gateway.
3. **A mission** — the headline primitive — a durable, auditable container for a multi-agent goal that spans sessions, token-exchange hops, and identities. Missions are how operators, security reviewers, and end users see, govern, and stop what agents are doing on someone's behalf.

The primary IDP is **RadiantLogic RadiantOne**, integrated as both the user-facing OIDC IDP and the **RFC 8693 token-exchange broker** that issues and tracks AAuth tokens across mission hops. **Keycloak** is supported as a co-equal fallback (matching the `USE_KEYCLOAK=1` toggle precedent from `spiffe-radiantlogic`) — same agent code, same registration flow, same mission semantics, just a different issuer.

SPIRE-attested workload identity is **deferred to P1**. We can deliver the full mission management story — register, issue, track, revoke — using the JWKS-publication pattern the demo already uses. SPIRE upgrades the *attestation strength* of registered agents later; it is not a prerequisite for the platform's core value.

## Problem Statement

The demo proves AAuth works end-to-end, but it is structurally not a product:

- **Agents are anonymous-by-default.** Keys are generated per-process and there is no registry, no lifecycle, no posture, and no humans-in-the-loop approving "who" each agent is.
- **Tokens are fire-and-forget.** Once Keycloak issues an auth token and the Supply Chain Agent exchanges it for a downstream token (SPEC §9.10), nobody is tracking what was issued, to whom, for what purpose, or how to revoke it mid-flight if something goes wrong.
- **Sessions are the wrong unit.** A single user request fans out across N agents over potentially minutes-to-hours, but our audit unit today is the HTTP session of whichever component logged it. Security teams cannot answer "what did the user authorize the agents to do, and what did the agents actually do on their behalf?" without manually correlating traces.

Enterprise security teams cannot greenlight agentic workloads without verifiable agent identity, an auditable consent trail per user-delegated action, and a working revocation story. Today, our answer to each of those is a code snippet, not a product.

## Goals

1. **Missions are the audit and governance primitive.** 100% of user-delegated agent actions in pilot tenants are attributable to a mission ID end-to-end (Backend → SCA → MAA → any future hop), and the mission is the unit on which operators and end users see, govern, and revoke.
2. **A registered agent has a durable identity.** 95% of agents in production are issued via the registry (vs. ephemeral keypairs) within 60 days of GA. The registration flow is documented well enough that a developer can register and run a new agent in under 15 minutes.
3. **Every issued token is observable and revocable.** P50 < 5s from "revoke" click to enforcement at the edge gateway; 100% of issued tokens appear in the token ledger within 1s of issuance.
4. **RadiantOne and Keycloak both ship as P0.** A net-new customer can onboard against either IDP in < 30 minutes following the install guide; the agent and platform code paths are identical across the two.
5. **Land at KubeCon NA 2026 (Jul 2026)** with a public demo tenant, a recorded reference architecture talk, and `v1.0` of the platform tagged.

Goals are outcomes, not features. The features that get us there are in the Requirements section. Mission management is goal #1 deliberately — it's the headline narrative and the thing that makes everything else worth doing.

## Non-Goals

- **Building our own IDP.** RadiantOne and Keycloak own user identity. We integrate; we do not replace.
- **Workload attestation in v1.** SPIRE-attested identity is a P1 enhancement, not a v1 requirement. The v1 registry trusts the operator + bootstrap token + proof-of-possession during enrollment; that is sufficient for the launch story. Stronger attestation comes after.
- **Cross-org agent federation in v1.** Multi-tenant within one org is in scope; agent identity federation across organizations (the "agent passport" story) is v2.
- **Replacing agentgateway as the PEP.** The platform is the control plane and audit/data plane for identities, tokens, and missions. The existing agentgateway remains the policy enforcement point at the edge per `agentgateway/config-policy.yaml`. We extend its config surface; we do not rewrite it.
- **Building a new A2A protocol or signature scheme.** We stay strictly compliant with AAuth (`SPEC.md`), A2A Protocol 0.3.0, RFC 9421 (HTTP Message Signatures), and RFC 9530 (Content-Digest). New schemes wait for the standards working group.
- **A first-party agent runtime / orchestrator.** The Supply Chain Agent and Market Analysis Agent in the demo stay as illustrative consumers. Customers bring their own agents; we issue them identities and govern their missions.

## Personas & User Stories

### Persona 1 — Platform Operator ("Priya")
Operates the AAuth Platform inside a customer's environment. Cares about: identity, lifecycle, revocation, compliance evidence.

- As a platform operator, I want to register a new agent in one CLI call — supplying its display name, owning team, allowed downstreams, and the JWKS URI it will publish — and receive back a stable agent ID URL plus a one-time bootstrap token I can hand to the developer.
- As a platform operator, I want to rotate or revoke an agent's keys without redeploying the agent so I can respond to a suspected compromise inside an SLA.
- As a platform operator, I want to view every token issued in the last 24h filtered by `act` claim chain, mission ID, or revocation status so I can answer auditor questions in minutes, not days.
- As a platform operator, I want to set per-agent policy — required signature scheme (HWK vs JWKS vs JWT), allowed downstream agents, max delegation depth — and have those policies render into `agentgateway` config automatically so I don't hand-edit YAML.

### Persona 2 — Application Developer ("Devon")
Builds an agent and integrates it with the platform. Cares about: SDK ergonomics, local dev story, time-to-first-signed-call.

- As a developer, given a `bootstrap_token` from my operator, I want to start my agent and have it self-enroll with the platform — generating its keypair, publishing its JWKS, posting proof-of-possession — without me writing any of that flow by hand. (P0-1 ships the REST endpoints and a minimal Python reference; the SDK in P1-2 wraps it.)
- As a developer, I want a `aauth dev` local mode that issues throwaway agent IDs and tokens so I can iterate without touching the production registry or RadiantOne.
- As a developer, I want my agent to receive its mission ID on every inbound call and propagate it on every outbound call automatically so I don't have to thread it manually through my business logic.

### Persona 3 — Security / IAM Reviewer ("Sam")
Approves the platform for production use. Cares about: provenance, revocation, blast radius, audit trail.

- As a security reviewer, I want every agent action to be attributable to (a) a registered agent identity, (b) an issued token, (c) a user consent grant, and (d) a mission so I can answer "who authorized this, and what was the scope" for any line in the audit log.
- As a security reviewer, I want a kill switch at the mission level — revoke the mission, and every token chained from it stops working at the edge within seconds — so an in-flight compromise has a clean stop.
- As a security reviewer, I want RadiantOne to be the source of truth for human identity and entitlements so I am not maintaining a parallel user store.

### Persona 4 — End User ("Ulla")
The human in the consent loop. Cares about: clarity of consent, ability to see and rescind.

- As an end user, I want the Keycloak / RadiantOne consent screen to show me the named, registered agents in the call chain (not opaque client IDs) so I know what I am actually approving.
- As an end user, I want a "missions on my account" view where I can see what agents are currently acting on my behalf and revoke any mission with one click.

## Diagrams

Four diagrams ground the core flows in the requirements that follow. Each is referenced from the matching P0 item; each SVG is also available as a standalone file under `diagrams/`.

### Figure 1 · Registration sequence

Two-phase shape: operator pre-registers the agent and receives a one-time bootstrap token; the agent self-enrolls on first boot with proof-of-possession; the registry pushes policy to agentgateway and activates the agent. The PoP signature is the security pivot — it binds the JWKS being enrolled to whoever holds the bootstrap token.

![Registration sequence diagram](diagrams/01-registration-sequence.svg)

### Figure 2 · Operator and developer split

Side-by-side view of what each persona actually does. The operator's lane is six steps but only three are pre-activation (1–3); the rest are reactive lifecycle. The developer's lane is deliberately tight — receive, configure, start — and everything else is the SDK's job. The two crossing arrows are the only required human handoffs.

![Operator and developer split](diagrams/02-operator-developer-split.svg)

### Figure 3 · Rotation and revocation

Rotation (top) is agent-initiated and additive: the agent signs the rotate request with its current key, the registry persists the new JWKS alongside the old during a 1h grace window, then auto-prunes the old key. Revocation (bottom) is operator-initiated and destructive: the registry marks the agent revoked, marks all issued tokens revoked in the ledger, pushes a deny policy to agentgateway, and the next signed call from anyone holding the agent's keys is rejected with 401 within five seconds.

![Rotation and revocation sequences](diagrams/03-rotation-revocation.svg)

### Figure 4 · Mission lifecycle

Phase A walks a normal active-to-completed mission end-to-end: backend creates the mission with the platform, makes a signed A2A call to the supply chain agent, the SCA performs an RFC 8693 token exchange at the platform to get a new token with extended `act` chain, calls the market analysis agent, responses bubble back, and the backend closes the mission with `state=completed`. Every hop carries the same `mission_id` in the `X-Mission-ID` header and emits an OTel span tagged with it — that is what makes attribution and the mission view possible.

Phase B shows the kill switch. A revoke request from a user (via the mission view) or an operator (via the CLI) reaches the platform; the platform marks the mission revoked, marks all tokens in its chain revoked in the ledger, and pushes a deny policy to agentgateway. Any subsequent signed call carrying that `mission_id` is rejected with 401 within five seconds — even if the calling agent's own keys are still otherwise valid.

![Mission lifecycle sequence](diagrams/04-mission-lifecycle.svg)

## Requirements

### P0 — Must Have (ship-blocking for KubeCon)

**P0-1. Agent Registry & Join Flow**
A first-class service that owns agent identity, with an explicitly documented two-stage join flow. The registry is the system of record for who agents are; the AAuth token machinery and missions in later P0s reference it.

**Registry data model** — each registered agent record contains:

- `agent_id_url` — the canonical identifier per SPEC §10.3.1; platform-issued at registration time.
- `display_name` — human-readable label rendered on the consent screen and in mission views.
- `owning_team` and `owner_contact` — for revocation triage.
- `jwks_uri` — where the platform (and other agents) discover the agent's signing keys. Defaults to `<agent_id_url>/jwks.json` per the demo convention.
- `metadata_uri` — `.well-known/aauth-agent` endpoint, also per the demo convention.
- `allowed_signature_schemes` — subset of `{hwk, jwks, jwt}`. Most production agents will be `jwks` only.
- `allowed_downstream_agents` — list of `agent_id_url`s this agent is permitted to call (drives P0-6 policy generation).
- `max_delegation_depth` — caps `act`-chain length when this agent initiates a call.
- `lifecycle_state` — one of `pending` → `active` → `disabled` → `revoked`. State transitions are audited.
- `bootstrap_token_hash` — one-time secret used during the join flow (see below); hashed at rest, zeroed once consumed.
- Timestamps: `created_at`, `activated_at`, `last_rotated_at`, `revoked_at`.

**Join flow** — two stages, explicit (see Figure 1 for the sequence and Figure 2 for the operator/developer split):

1. **Pre-registration (operator-driven).** Operator calls `POST /v1/agents` (REST) or `aauth agents register` (CLI) with display name, owning team, and policy fields (allowed downstreams, max delegation depth, allowed schemes). Platform responds with: the assigned `agent_id_url`, a one-time `bootstrap_token`, and the URL the agent should `POST` its public keys to on first boot. Agent record is created in `pending` state.
2. **Self-enrollment (agent-driven, on first boot).** The agent generates its Ed25519 (or P-256) keypair, publishes its JWKS at the agreed URI, and then calls `POST /v1/agents/{id}/enroll` with: the `bootstrap_token` as a bearer credential, its JWKS payload (or a signed reference to its `jwks_uri`), and a proof-of-possession — a signed challenge using its new private key per RFC 9421. On success, the platform: verifies the bootstrap token, verifies the PoP signature, persists the JWKS thumbprint, zeroes the bootstrap token, transitions the agent to `active`, and pushes policy to agentgateway (P0-6).

This is the same shape SPIRE uses for join tokens, deliberately — so the P1 SPIRE integration can later replace the `bootstrap_token` step with k8s_psat attestation without changing the registry's data model or operator UX.

**Key rotation** is an authenticated re-enrollment (see Figure 3, Flow A): agent calls `POST /v1/agents/{id}/rotate` signed with its *current* active key, supplying a new JWKS. The platform keeps the previous JWKS valid for a grace window (default 1h, configurable) so in-flight tokens don't break, then auto-prunes it.

**Revocation** is a single operator action (see Figure 3, Flow B): `DELETE /v1/agents/{id}` (or `aauth agents revoke <id>`). The agent's JWKS is invalidated in the registry, every existing token issued to or by this agent is marked revoked in the token ledger (P0-2), and the deny policy propagates to agentgateway within 5s — so the next signed call from anyone holding the agent's keys is rejected with 401.

CRUD is exposed via REST + CLI. UI for the registry ships as part of the platform's operator console.

*Acceptance:*

- Given an operator pre-registers an agent and gives a developer the `bootstrap_token`, when the developer's agent boots, publishes its JWKS, and calls the enrollment endpoint, then the agent transitions to `active` and is callable end-to-end without further operator intervention. Total operator-side effort: one CLI call. Total developer-side effort: copy the token into config and start the agent.
- Given an operator revokes a registered agent, when that agent (or anyone else with its keys) attempts a signed call within 5s, then agentgateway rejects the call and the token ledger reflects the revocation.
- Given an operator attempts to register an agent with an `allowed_downstream_agents` list referencing an unregistered ID URL, then the registration fails with a clear error (no dangling references).

**P0-2. Token Ledger with Revocation**
Every token issued in the AAuth flow — Keycloak/RadiantOne-issued auth tokens, resource tokens (issued on 401 with `Agent-Auth` header), and exchanged tokens with `act` claims (SPEC §9.10) — is written to a queryable ledger with: issuer, subject, audience, mission ID, `act` chain, scopes, issued-at, expiry, and revocation status. Revoking a token propagates to agentgateway within 5s P50, 15s P99.
*Acceptance:* The ledger contains a row for every token within 1s of issuance (verified by an integration test that issues 1,000 tokens and asserts ledger count). Revoked tokens fail signature verification at the edge within the SLA.

**P0-3. Mission as the Audit Primitive** (see Figure 4 for the lifecycle)
A mission is a server-issued identifier created when a user initiates a delegated action via the Backend. The mission ID is carried as a signed header through every downstream A2A call (including across token exchanges), is logged in every span via OpenTelemetry, and links to: originating user, originating registered agent, all tokens in its chain, all participating agents. A mission has a state machine: `active` → `completed` | `failed` | `revoked`.
*Acceptance:* A user initiates a request that fans out across Backend → Supply Chain Agent → Market Analysis Agent. The mission view shows all three agents, all three tokens, the consent grant, the start/end timestamps, and a single mission ID that appears as a trace tag on every span. Revoking the mission halts in-flight calls at agentgateway within 5s.

**P0-4. RadiantLogic RadiantOne as Primary IDP (with RFC 8693 token exchange)**
RadiantOne plays two distinct roles in the platform, reusing the integration shape proven in `spiffe-radiantlogic`:

1. **User-facing OIDC IDP** for the Backend's human login flow: standard OIDC authorization-code flow, custom consent screen that surfaces registered agent *names* (from P0-1) rather than opaque OIDC client IDs, and standard ID/access-token issuance.
2. **Token-exchange broker (RFC 8693)** for the agent-to-agent and user-delegated paths: RL accepts an AAuth-signed assertion (the calling agent's registered JWKS-signed JWT) as `subject_token`, validates it against the agent's registered JWKS in P0-1, and issues an RL-signed access token whose `sub` carries the calling agent's `agent_id_url` and whose `act` claim chain preserves the upstream delegation history per AAuth SPEC §9.10. We layer AAuth's `act` semantics on top of RL's existing RFC 8693 endpoint rather than building a parallel exchange.

Concrete deliverables, modeled on the prior PoC's structure:

- A K8s manifest tree under `manifests/radiantlogic/` with: RL Helm values, federated-IdP config pointing at the platform's registered-agents JWKS endpoint, token-exchange config accepting `subject_token_type=urn:ietf:params:oauth:token-type:jwt`, and subject-mapping rules (registered `agent_id_url` → RL token `sub`).
- A numbered deploy script set (`scripts/00-` through `scripts/05-`), with `scripts/02-deploy-radiantlogic.sh` adapted from the prior project as the canonical starting point.
- A documented setup runbook including realm/tenant config, license note, and the admin-console steps for enabling the token-exchange grant.

*Acceptance:* A clean install with a RadiantOne tenant + the platform completes the full user-delegated AAuth flow (Backend login → consent → resource token issued by RL → multi-hop RL token exchange with `act` chain → call landing at the downstream agent through agentgateway) end-to-end with no Keycloak in the stack. The full `act` chain is present in the final token and renders correctly in the mission view (P0-3).

**P0-5. Keycloak as Co-Equal Fallback IDP**
The existing Keycloak flow from `aauth-full-demo` is supported as a co-equal IDP — same registration flow (P0-1), same mission semantics (P0-3), same token ledger (P0-2). Selection is per-tenant via a configuration toggle, matching the `USE_KEYCLOAK=1` precedent from `spiffe-radiantlogic`. Agent code and platform code are identical across the two; only the issuer URL and the IdP-side config differ. No code paths are RadiantOne-only or Keycloak-only.

*Acceptance:* CI runs the full integration test suite against both `IDP=radiantone` and `IDP=keycloak` on every release and passes both. The Keycloak path additionally serves as the OSS demo path for users without an RL license; the install runbook treats it as a first-class option, not a footnote.

**P0-6. agentgateway Policy Generation**
Platform exposes a "policy" API; on write, it renders the current registry + ledger state into a valid `agentgateway/config-policy.yaml` and triggers a hot reload. Operators set high-level intent (e.g. "agent X may only call agents Y and Z, requires JWKS scheme, max delegation depth 2") rather than hand-editing YAML.
*Acceptance:* An operator change in the UI is reflected in agentgateway enforcement within 10s. A YAML diff is logged for every change.

### P1 — Should Have (fast-follow if cut for KubeCon)

**P1-1. SPIRE-attested workload identity for K8s deployments.** An upgrade path for P0-1's join flow: instead of consuming a one-time `bootstrap_token`, an agent's enrollment can be authenticated by a SPIRE-issued JWT-SVID, with k8s_psat node attestation binding it to a namespace + ServiceAccount. The registry's data model is unchanged — `bootstrap_token_hash` becomes optional and is replaced by a `spiffe_id` field. RadiantOne's federated-IdP config (already validated in `spiffe-radiantlogic`) is the integration point. Direct lift of the manifests and `scripts/02-deploy-radiantlogic.sh` from that PoC.

**P1-2. SDK packages for Python and TypeScript.** Wraps registration enrollment, signing (HWK/JWKS/JWT), inbound verification, mission-ID propagation, and the OTel hookup the demo currently does longhand in `aauth_interceptor.py`. Python is the priority given the demo's stack; TS follows.

**P1-3. End-user "my missions" view.** A user-facing page (in the Backend's frontend) listing the user's active and recent missions, what agents participated, and a one-click revoke. Backed by P0-3.

**P1-4. Pre-built dashboards.** Grafana / equivalent dashboards for: tokens issued/minute, revocation latency, mission success rate, top agents by call volume, signature scheme distribution. Sourced from the existing Jaeger/OTel pipeline.

**P1-5. Bulk import of existing agents.** A migration path for customers running the demo today to one-shot register their agents and rotate keys into the platform without downtime.

**P1-6. Mission timeouts and budgets.** A mission can carry a wall-clock TTL and an optional max-hop count. Enforced at agentgateway.

### P2 — Future Considerations (design-for, do not build)

**P2-1. Cross-org agent federation.** "Agent passport" — present a registered agent identity from org A to org B's gateway and have it verifiable. Drives a federation-aware ID URL scheme today even though we don't build the federation layer.

**P2-2. Non-human consent flows (machine-to-machine missions).** Some missions originate from a scheduled job, not a user. Design the mission schema to allow a `service-account` originator now so we don't repaint the data model later.

**P2-3. Additional IDPs (Okta, Auth0, Ping).** Keep the IDP integration behind a clean interface so adding a third provider is a connector, not a rewrite.

**P2-4. Anomaly detection on the token ledger.** Surface suspicious patterns (sudden expansion of `act` chain depth, unfamiliar agent pairings, token reuse from new IPs). Requires the ledger from P0-2 to exist with the right shape.

## Success Metrics

### Leading indicators (first 30 days post-launch)

- **Time-to-first-signed-call** for a developer following the quickstart: target ≤ 15 minutes, stretch ≤ 8.
- **Agent registration coverage** in pilot tenants: % of agents making calls that are registered (vs. anonymous/ephemeral). Target 80% by day 30.
- **Mission attribution rate:** % of user-delegated calls with a resolvable mission ID end-to-end. Target 99%.
- **Revocation latency** (P50/P99): target P50 < 5s, P99 < 15s.
- **Setup completion rate** for the RadiantOne install runbook: target 70% of starts reach a working end-to-end call.

### Lagging indicators (90 days post-launch)

- **Security-team approval rate** for new agentic workloads using the platform vs. without: target measurable lift (we will set the specific delta after the first month of baseline data).
- **Audit response time** in pilot tenants: time to answer "what did agent X do for user Y last Tuesday?" Target < 5 minutes vs. current "hours-to-days".
- **Active missions per tenant per week:** proxy for adoption depth. No target for v1; we are establishing baseline.
- **Support tickets per tenant per week:** target trending down after week 4 as the install runbook stabilizes.

Measurement: tokens-issued and revocation latency from the ledger itself; setup metrics from a lightweight `aauth telemetry` opt-in in the install script; security/audit metrics from pilot-customer interviews at day 30 and day 90.

## Open Questions

- **(Sam — security)** Does RadiantOne's consent screen support the customization we need (rendering registered agent names instead of OIDC client IDs)? If no, what is the workaround — a proxy consent page hosted by the platform? Blocks P0-4 design.
- **(Engineering)** Where does the token ledger physically live? Postgres is the obvious default; do we have a write-rate requirement (the demo's `act`-chain exchanges suggest 3+ writes per user request) that argues for a different store? Blocks P0-2 estimation.
- **(Engineering)** Does RadiantOne's RFC 8693 token-exchange flow let us carry through the AAuth `act` claim chain unchanged across hops, or do we need a small subject-mapping rule that copies `act` from `subject_token` into the issued token? If the latter, this is a small RL config delta from the prior PoC.
- **(Engineering)** Does the existing `agentgateway` config-reload story support hot reload at the cadence we'd push policy changes (P0-6), or do we need to land a small contribution upstream to make it work? Non-blocking but on the critical path.
- **(Engineering / Security)** Bootstrap token transport — operator pre-registers an agent, then how does the `bootstrap_token` get to the developer running the agent safely? Console copy-paste is fine for v1; do we need an envelope-encrypted delivery mechanism, or is "give it to your secret manager" the documented answer?
- **(Engineering)** Does the registry need to support agents that *do not* publish their JWKS at a discoverable URI — for example, an agent inside a private network that pushes its JWKS to the registry directly? My read is yes for v1; please confirm before we lock the data model.
- **(Design / Product)** Is "Mission" the term we ship with externally? It is the right primitive but unfamiliar; we should sanity-check with two or three design partners before we name UI around it.
- **(Standards / Dick Hardt)** Is anything in this productization in tension with the direction AAuth itself is heading? Specifically, the mission concept is platform-layer, not protocol-layer — we should confirm nothing the spec is adding (e.g. session-binding extensions) conflicts.
- **(Engineering)** What does "fallback IDP" actually mean operationally — per-tenant configuration toggle (matching the `USE_KEYCLOAK=1` precedent and my current assumption), or both IDPs warm simultaneously with failover? Please confirm.

## Timeline Considerations

**Hard deadline:** 2026-07-03, two weeks before KubeCon NA 2026, so we have buffer for talk rehearsal and the launch blog.

**Phasing (working assumption — confirm with engineering during spec review):**

- **By 2026-06-01** — P0-1 (Agent Registry) and P0-2 (Token Ledger) cut over. These are the foundation for everything else.
- **By 2026-06-15** — P0-3 (Missions) lands on top of the registry + ledger.
- **By 2026-06-22** — P0-4 (RadiantOne) and P0-5 (Keycloak parity) both passing CI.
- **By 2026-06-26** — P0-6 (policy generation) end-to-end with agentgateway.
- **2026-06-26 to 2026-07-03** — Hardening, install runbook, demo tenant, launch artifacts.

**Dependencies / risks:**

- **RadiantOne tenant access.** We need a sandbox tenant by end of May at the latest. Procurement risk. Mitigated partly by the `spiffe-radiantlogic` PoC already running against an RL v8.1 instance — same license / container access path applies.
- **agentgateway upstream contribution.** If hot-reload work is required, it needs to land in the agentgateway main branch with enough time to bake. Owner: TBD during eng review.
- **AAuth spec movement.** SPEC.md is large and active; if §9.x changes between now and launch, P0-2 and P0-3 may need adjustment. Mitigation: weekly check-in with the spec maintainer.
- **Pilot customer for the mission UI.** Mission is the headline primitive; we want at least one pilot customer running real workloads by KubeCon so the demo is grounded in a real use case rather than a synthetic supply-chain example.

**Cut order if we slip:** drop P1-2 (end-user missions view) first, then P1-3 (dashboards). P0 items are non-negotiable for the KubeCon narrative — without revocation and mission attribution, this is still a demo, not a platform.

---

*Next steps after this review: (1) Eng resolves the open questions tagged Engineering, (2) Security signs off on the RadiantOne integration shape, (3) we lock the name "Mission" or pick an alternative with a design partner, (4) I cut this into an engineering ticket breakdown.*
