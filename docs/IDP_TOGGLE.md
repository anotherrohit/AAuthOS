# IDP toggle — RadiantLogic ↔ Keycloak

The platform reads its IDP config from a ConfigMap. Switching IDPs is three steps:

1. Deploy the new IDP (if not already running).
2. Rewrite the `platform-config` ConfigMap with the new issuer / token-exchange / JWKS URLs.
3. Restart the deployments that consume it.

## Clean switch (start over)

The simplest path is to tear down and redeploy:

```bash
make teardown
make USE_KEYCLOAK=1 demo   # or omit USE_KEYCLOAK=1 for RadiantLogic
```

## In-place switch (no teardown)

If you want to keep your cluster and just change IDPs:

```bash
# 1. Stand up the other IDP (the existing one stays running on the side)
make USE_KEYCLOAK=1 idp    # → deploys keycloak in ns/idp
# or
make idp                   # → deploys radiantlogic in ns/idp

# 2. Overwrite platform-config to point at the new IDP
# (the deploy script does this for you, but if you're switching manually:)
kubectl -n platform create configmap platform-config \
  --from-literal=IDP_FLAVOR=keycloak \
  --from-literal=IDP_ISSUER_URL=http://keycloak.idp.svc.cluster.local/realms/aauth \
  --from-literal=IDP_TOKEN_EXCHANGE_URL=http://keycloak.idp.svc.cluster.local/realms/aauth/protocol/openid-connect/token \
  --from-literal=IDP_JWKS_URL=http://keycloak.idp.svc.cluster.local/realms/aauth/protocol/openid-connect/certs \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Restart everything that reads it
kubectl -n platform rollout restart deploy/registry-service deploy/mission-service
kubectl -n apps     rollout restart deploy/backend deploy/supply-chain-agent deploy/market-analysis-agent
kubectl -n gateway  rollout restart deploy/agentgateway
```

## What stays the same across both

| Thing                                  | Same? |
| -------------------------------------- | ----- |
| Agent code (backend, SCA, MAA, UI)     | Yes   |
| Platform code (registry, mission)      | Yes   |
| Agent registration flow                | Yes   |
| Bootstrap token shape and TTL          | Yes   |
| RFC 8693 token exchange semantics      | Yes   |
| Mission lifecycle and revocation       | Yes   |
| `act` chain extension at each hop      | Yes (configured per-IDP, same outcome) |
| Federated JWKS source (the platform)   | Yes   |

| Thing                                  | Different |
| -------------------------------------- | --------- |
| Issuer URL                             | `https://radiantlogic.aauth.local/oauth2` vs. `http://keycloak.aauth.local/realms/aauth` |
| Token endpoint path                    | `/oauth2/token` vs. `/realms/aauth/protocol/openid-connect/token` |
| Admin console UX                       | Vendor-specific                  |
| Subject mapping config syntax          | RL realm JSON vs. Keycloak protocol mappers |
| License requirement                    | RL is commercial; Keycloak is OSS |

That's intentional. The product story we'd tell at KubeCon is "your agents don't know or care which IDP you're using" — and that's literally true: no agent code changes between the two paths.
