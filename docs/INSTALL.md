# Install

End-to-end setup for the AAuth Mission Platform demo on KIND.

## 1. Prerequisites

| Tool     | Version    | Notes                                                |
| -------- | ---------- | ---------------------------------------------------- |
| Docker   | 24+        | KIND runs cluster nodes as containers                |
| kind     | ≥ 0.22     | https://kind.sigs.k8s.io/                            |
| kubectl  | ≥ 1.30     | Match the kind node image version in `kind/`         |
| helm     | ≥ 3.12     | For the RL / Keycloak charts                         |
| make, jq, curl | recent | Used throughout the scripts                    |
| git      | any        | `scripts/06-deploy-apps.sh` clones upstream agents   |

For the **RadiantLogic** path you additionally need:

- A valid RadiantLogic IDM v8.1 license file (`.lic`)
- Credentials to RL's container registry (set `RL_REGISTRY_USER` / `RL_REGISTRY_PASS` env vars before `make demo`)

If you don't have a RL license, skip ahead and run `make USE_KEYCLOAK=1 demo` — the rest of this guide still applies.

## 2. Clone and inspect

```bash
git clone <this-repo-url> aauth-mission-platform-demo
cd aauth-mission-platform-demo
make help
```

Have a look at `kind/kind-cluster.yaml`. The port mappings tell you what will be exposed on the host:

| Host port | Service                | Notes                                         |
| --------- | ---------------------- | --------------------------------------------- |
| 3000      | supply-chain-ui        | The frontend                                  |
| 8000      | backend                | Direct REST access for the demo script        |
| 8080      | RL admin / Keycloak    | Admin console for the active IDP              |
| 8443      | agentgateway           | TLS edge for A2A traffic                      |
| 9000      | registry-service       | `curl http://localhost:9000/v1/agents`        |
| 9001      | mission-service        | `curl http://localhost:9001/v1/missions`      |

If any of those ports conflict on your machine, edit `kind-cluster.yaml` before `make kind-up`.

## 3. Run

### RadiantLogic path (default)

```bash
export RL_REGISTRY_USER=...      # if not already in your shell
export RL_REGISTRY_PASS=...
make demo
```

### Keycloak fallback

```bash
make USE_KEYCLOAK=1 demo
```

Either path runs all the numbered scripts in order. Expect 8–12 minutes on a laptop. The slowest steps are the RL or Keycloak Helm install and the agent image builds.

## 4. Verify

When `make demo` finishes, run through these spot-checks:

```bash
# Cluster is up
kubectl get nodes

# All namespaces have running pods
kubectl get pods -A | grep -E '(idp|platform|gateway|apps)'

# The IDP is reachable
curl -k https://localhost:8443/healthz                 # agentgateway
curl    http://localhost:9000/v1/agents | jq .         # three registered agents

# The frontend opens
open http://localhost:3000                              # macOS
xdg-open http://localhost:3000                          # Linux
```

You should see three agents in `state=active`:

```json
[
  { "id": "backend",         "lifecycle_state": "active", ... },
  { "id": "supply-chain",    "lifecycle_state": "active", ... },
  { "id": "market-analysis", "lifecycle_state": "active", ... }
]
```

## 5. Drive a mission

The simplest path is the demo script `scripts/07-run-demo.sh` (also `make run`). It POSTs to the backend, captures the `mission_id`, and tails the mission service logs so you can see the act-chain in flight.

For an interactive walkthrough see [`MISSION_LIFECYCLE.md`](MISSION_LIFECYCLE.md).

## 6. Switch IDPs after the fact

You don't have to `teardown` to try the other IDP — `docs/IDP_TOGGLE.md` covers the minimum steps to flip a running cluster between RL and Keycloak.

## 7. Teardown

```bash
make teardown          # destroys the KIND cluster
rm -rf .bootstrap-tokens .work   # if you want fully clean state
```

## Troubleshooting

| Symptom                                          | Likely fix                                                    |
| ------------------------------------------------ | ------------------------------------------------------------- |
| `ImagePullBackOff` on platform pods              | `kind load docker-image` didn't run — re-run `make platform`  |
| `radiantlogic` pod CrashLoopBackOff              | Missing license / image pull secret — check `kubectl describe`|
| `registry-service` returns 401 on every enroll   | Bootstrap token TTL expired — re-run `make register`          |
| `make run` reports `dev/login: 404`              | Backend image doesn't include the dev-login patch; rebuild    |
| RL admin console rejects login                   | Defaults `admin/admin` — override via Helm values             |
