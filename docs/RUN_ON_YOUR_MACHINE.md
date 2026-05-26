# Running the demo on your machine

I can't reach your Docker daemon from the session sandbox, so this is the script for you to execute locally. The walkthrough is tuned for **Windows + WSL2 + Docker Desktop** since that's what your file paths suggest you're on. macOS / Linux users: skip step 0.

## 0. One-time setup (Windows only)

1. **Install Docker Desktop with the WSL2 backend.** In Docker Desktop → Settings → Resources → WSL Integration, enable your Ubuntu distro.
2. **Open an Ubuntu WSL terminal.** Everything from here on runs *inside WSL*, not in PowerShell. WSL talks to Docker Desktop transparently.
3. **Install the rest:**
   ```bash
   sudo apt update
   sudo apt install -y make jq curl git python3 python3-venv
   # kind
   curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.22.0/kind-linux-amd64
   chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind
   # kubectl
   curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
   chmod +x kubectl && sudo mv kubectl /usr/local/bin/kubectl
   # helm
   curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
   ```
4. **Bump Docker Desktop's RAM** to at least 8 GB (Settings → Resources). KIND with three worker nodes + RL/Keycloak + the platform services needs the room.

## 1. Get the repo into WSL

The repo currently lives at `C:\Users\nayak\AppData\Roaming\Claude\...\outputs\aauth-mission-platform-demo\`. WSL can read that path, but Docker-in-Docker and KIND volume mounts are happier when the project is on the Linux filesystem. Copy it once:

```bash
mkdir -p ~/aauth && cd ~/aauth
cp -R /mnt/c/Users/nayak/AppData/Roaming/Claude/local-agent-mode-sessions/8204b6c9-ca8c-4915-ad1a-9d057a3cdcc4/793bdd41-16a3-488b-ba61-309517d82d4f/local_152af623-db54-4699-8619-02202ee72fde/outputs/aauth-mission-platform-demo .
cd aauth-mission-platform-demo
chmod +x scripts/*.sh
```

## 2. Pre-flight

```bash
./scripts/preflight.sh
```

Expected output (everything green):

```
  ✓  docker
  ✓  kind
  ✓  kubectl
  ✓  helm
  ✓  make
  ...
  ✓  port 80    free
  ✓  port 443   free
  ✓  port 3000  free
  ...
  All checks passed.  You can run:  make USE_KEYCLOAK=1 demo
```

If a port shows `!  already in use`, find the offender with `sudo lsof -i :<port>` and stop it (or edit `kind/kind-cluster.yaml` to use a different host port).

## 3. Run the demo, one script at a time

Skip RadiantLogic (no license needed); use Keycloak. Each command lists what to look for before moving on.

### `make kind-up`  — 00

What it does: creates a 3-node KIND cluster from `kind/kind-cluster.yaml`, installs ingress-nginx, creates the four namespaces.

Verify:
```bash
kubectl get nodes
# NAME                       STATUS   ROLES
# aauth-demo-control-plane   Ready    control-plane
# aauth-demo-worker          Ready    <none>
# aauth-demo-worker2         Ready    <none>

kubectl get ns | grep -E 'idp|platform|gateway|apps'
# idp        Active
# platform   Active
# gateway    Active
# apps       Active
```

### `make keycloak` — 02 (skip 01-deploy-radiantlogic.sh)

What it does: Bitnami Keycloak chart, realm import with the federated-IdP + token-exchange config, writes the `platform-config` ConfigMap.

Verify:
```bash
kubectl -n idp get pods
# keycloak-0                  1/1   Running
# keycloak-postgresql-0       1/1   Running

# realm import succeeded
kubectl -n idp port-forward svc/keycloak 18080:80 &
sleep 2
curl -sf http://127.0.0.1:18080/realms/aauth/.well-known/openid-configuration | jq .issuer
# "http://keycloak.aauth.local/realms/aauth"
kill %1
```

### `make platform` — 03

What it does: builds the `registry-service`, `mission-service`, **and operator-console** images locally, `kind load`s them, applies the manifests.

Verify:
```bash
kubectl -n platform get pods
# mission-service-...    1/1   Running
# registry-service-...   1/1   Running
# operator-console-...   1/1   Running

curl -sf http://localhost:9000/healthz
# {"status":"ok"}

curl -sf http://localhost:9001/healthz
# {"status":"ok"}

# Console served at host port 9002:
curl -sI http://localhost:9002/ | head -1
# HTTP/1.0 200 OK
```

Open the operator console in your browser: **http://localhost:9002**.
Default credentials are `operator` / `aauth-operator-demo`. Override them by editing the `operator-auth` ConfigMap (see `docs/OPERATOR_CONSOLE.md`).

### `make gateway` — 04

What it does: deploys agentgateway, seeds the policy ConfigMap from `registry-service`'s `/v1/policy/render` (which returns an empty allowlist for now — no agents registered yet).

Verify:
```bash
kubectl -n gateway get pods
# agentgateway-...   1/1   Running
```

### `make register` — 05

What it does: operator-side pre-registration of all three agents. Bootstrap tokens land in `./.bootstrap-tokens/`.

Verify:
```bash
ls .bootstrap-tokens/
# backend.id_url  backend.token  market-analysis.id_url  market-analysis.token  supply-chain.id_url  supply-chain.token

curl -sf http://localhost:9000/v1/agents | jq '.[] | {id, lifecycle_state}'
# { "id": "market-analysis", "lifecycle_state": "pending" }
# { "id": "supply-chain",    "lifecycle_state": "pending" }
# { "id": "backend",         "lifecycle_state": "pending" }
```

All three should be in `pending` — they haven't enrolled yet because the agent pods don't exist.

### `make apps` — 06

What it does:
1. Shallow-clones `christian-posta/aauth-full-demo` into `.work/`.
2. Runs `sdk/integration/patches/apply_patches.py` to wire the SDK into each agent.
3. Stages the SDK into each agent's Docker build context.
4. Builds + `kind load`s four images (backend, sca, maa, ui).
5. Creates K8s Secrets with the bootstrap tokens.
6. Applies the workload manifests.

This is the longest step — expect 5–8 minutes. Watch the rollout:
```bash
kubectl -n apps get pods -w
# wait until all four show Running 1/1
```

Verify the agents enrolled themselves on boot:
```bash
curl -sf http://localhost:9000/v1/agents | jq '.[] | {id, lifecycle_state, jwks_thumbprint}'
# { "id": "...",  "lifecycle_state": "active", "jwks_thumbprint": "..." }   ← three of these
```

If any agent is stuck in `pending`, check its logs:
```bash
kubectl -n apps logs deploy/backend | grep -i enroll
```

The most common failure is the bootstrap token having expired between `make register` and `make apps` if you took more than an hour between them. Re-run `make register` to mint fresh tokens, then `make apps` again.

### `make run` — 07

What it does: drives a user-delegated mission end-to-end and tails the mission service logs.

Expected output is what the screenshot SVG renders — roughly:

```
==> Submitting mission via backend
    mission_id=<uuid>

==> Mission state:
{
  "state": "active" (then "completed"),
  "hops": [
    { "from": "...backend",      "to": "...supply-chain",    "act_chain": ["...backend"] },
    { "from": "...supply-chain", "to": "...market-analysis", "act_chain": ["...backend", "...supply-chain"] }
  ],
  "tokens": [ ... two RFC 8693-exchanged tokens with jti and act ... ]
}

mission state updated  state=completed  hops=2
```

That `act_chain = ["...backend", "...supply-chain"]` on the second hop is the success criterion you asked about — it means RadiantLogic (or Keycloak) successfully token-exchanged the upstream credential at the SCA hop, the IDP validated the AAuth-signed subject_token against the platform's federated JWKS source, and the platform issued a new token with the chain correctly extended.

## 4. Try the kill switch

```bash
# Grab any active mission_id from the ledger
MID=$(curl -sf http://localhost:9001/v1/missions | jq -r '.[0].id')

# Revoke it
make revoke-mission ID=$MID

# Check the ledger — state should be 'revoked', tokens marked revoked=1
curl -sf http://localhost:9001/v1/missions/$MID | jq '.state, .tokens[].revoked'
# "revoked"
# true
# true
```

## 5. Stop here (don't run 99-teardown.sh)

The cluster stays up. You can re-run `make run` as many times as you want — each invocation creates a fresh mission.

When you do eventually want to clean up:
```bash
make teardown   # OR: kind delete cluster --name aauth-demo
```

## If something doesn't work

The most likely failures and how to triage:

| Symptom | First thing to check |
|---|---|
| `make kind-up` hangs | Docker Desktop's WSL integration is off; KIND can't talk to Docker |
| `ImagePullBackOff` on platform pods | `kind load docker-image` step in `scripts/03-deploy-platform.sh` failed — re-run `make platform` |
| Agents stuck `pending` after `make apps` | Bootstrap tokens expired; re-run `make register` then `make apps` |
| `make run` returns 404 on `/dev/login` | Backend image doesn't include the dev-login patch — see `sdk/integration/backend.md` Edit 4 |
| Keycloak `realms/aauth/.well-known/...` 404 | Realm import ConfigMap didn't apply before chart install — re-run `make keycloak` |
| Mission shows `state=active` forever, never `completed` | Backend's optimize handler failed mid-call; check `kubectl -n apps logs deploy/backend` |

## If something hard-fails

Paste the failing command + its output back to me in this chat. I can read the logs you paste, even though I can't reach the cluster directly. Most of the failure modes are config drift between the platform-config ConfigMap and the agent env vars — easy to diagnose from one round-trip.
