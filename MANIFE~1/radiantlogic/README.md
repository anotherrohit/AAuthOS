# RadiantLogic IDM — demo deployment notes

This directory deploys RadiantLogic IDM v8.1 as the primary IDP for the AAuth Mission Platform demo. It plays two roles:

1. The user-facing OIDC IDP for the supply-chain-ui frontend.
2. The RFC 8693 token-exchange broker for agent-to-agent calls — federated against the platform's aggregated agent JWKS endpoint.

## Files

- `01-radiantlogic-values.yaml` — Helm values for the RL IDM chart. KIND-friendly, single replica, NodePort exposed on host port 8080.
- `02-token-exchange-config.yaml` — realm import + federated-IdP + token-exchange grant config. Applied separately so it can be reloaded without re-installing the chart.

## Prerequisites

RadiantLogic IDM is **commercial software**. You need:

1. A valid RL IDM v8.1 license file (`.lic`).
2. Access to RL's container registry. Set `RL_REGISTRY_USER` and `RL_REGISTRY_PASS` env vars before running `scripts/01-deploy-radiantlogic.sh`; the script will create an image pull secret automatically.
3. The Helm chart published at `https://helm.radiantlogic.com` (added by the script).

If you do not have a license, run the demo with `make USE_KEYCLOAK=1 demo` instead. See [`../keycloak/README.md`](../keycloak/README.md).

## Manual admin-console steps (only if the automated reload didn't pick up the config)

If `02-token-exchange-config.yaml` was applied but RL didn't reload it, log into the admin console (default `admin` / `admin` on port 8080) and:

1. **Realms → aauth → Federated IdPs → Add** an OIDC IdP pointing at `https://registry-service.platform.svc.cluster.local:9000/v1/agents/jwks.json` with subject token type set to `urn:ietf:params:oauth:token-type:jwt`.
2. **Realms → aauth → Token Exchange → Enable**, accept the JWT subject token type, set TTL to 300s.
3. **Realms → aauth → Token Exchange → Subject Mappings → Add** the four mappings from the YAML.

You can verify the federation is working with:

```bash
kubectl -n idp exec deploy/radiantlogic -- curl -sf \
  https://registry-service.platform.svc.cluster.local:9000/v1/agents/jwks.json
```

## Token-exchange test

After everything is up, you can exercise the token exchange directly:

```bash
# A registered agent (e.g. backend) signs a JWT with its private key,
# then POSTs it to RL's /token endpoint as the subject_token.
curl -X POST https://radiantlogic.aauth.local/oauth2/token \
  -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
  -d subject_token=<signed-jwt> \
  -d subject_token_type=urn:ietf:params:oauth:token-type:jwt \
  -d audience=https://platform.aauth.local/agents/supply-chain
```

You should get back a new access_token with `sub` set to the calling agent's `agent_id_url` and an `act` claim chain.

## Further reading

- [RadiantLogic IDM v8.1 self-managed install](https://developer.radiantlogic.com/idm/v8.1/installation/self-managed/)
- [RFC 8693 OAuth 2.0 Token Exchange](https://datatracker.ietf.org/doc/html/rfc8693)
- Prior PoC with the same federation shape: [anotherrohit/spiffe-radiantlogic](https://github.com/anotherrohit/spiffe-radiantlogic)
