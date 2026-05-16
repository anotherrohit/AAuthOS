# Keycloak (fallback IDP)

Same realm shape as RadiantLogic — same federated-IdP target (the platform's agent JWKS aggregator), same RFC 8693 token-exchange grant, same `mission_id` and `act`-chain mappings. Switching between the two is a `make USE_KEYCLOAK=1 demo` away.

Use this path if you don't have a RadiantLogic license. The agent and platform code are unchanged; only `IDP_ISSUER_URL` / `IDP_TOKEN_EXCHANGE_URL` differ.

## Demo credentials

- Admin: `admin` / `admin` (admin console at http://localhost:8080)
- End user: `demo-user` / `demo-pass`

Override both for any real use.
