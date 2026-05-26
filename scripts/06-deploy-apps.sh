#!/usr/bin/env bash
# 06 — Build agent images from the upstream aauth-full-demo + apply the
# aauth_sdk patches, then deploy the four workloads (backend, SCA, MAA, UI).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKENS_DIR="${ROOT}/.bootstrap-tokens"
UPSTREAM_REPO="${UPSTREAM_REPO:-https://github.com/christian-posta/aauth-full-demo.git}"
UPSTREAM_REF="${UPSTREAM_REF:-main}"
WORK="${ROOT}/.work"
CLUSTER_NAME="${CLUSTER_NAME:-aauth-demo}"
SDK_DIR="${ROOT}/sdk/python"
PATCHER="${ROOT}/sdk/integration/patches/apply_patches.py"

mkdir -p "${WORK}"

# Clone (or refresh) upstream into a sibling of the SDK so we can install the
# SDK by path from inside the Docker build context.
if [[ ! -d "${WORK}/aauth-full-demo" ]]; then
  echo "==> Cloning ${UPSTREAM_REPO} (${UPSTREAM_REF})"
  git clone --depth 1 --branch "${UPSTREAM_REF}" "${UPSTREAM_REPO}" "${WORK}/aauth-full-demo"
fi

# Apply the SDK patches to the entrypoints + replace the hand-rolled
# aauth_interceptor.py with a shim. Idempotent.
echo "==> Applying aauth_sdk patches to upstream"
python3 "${PATCHER}" "${WORK}/aauth-full-demo"

# Stage the SDK into every agent dir so the agent's Dockerfile build context
# includes it. We extend each Dockerfile with two lines: COPY + pip install.
stage_sdk_for() {
  local subdir="$1"
  local dst="${WORK}/aauth-full-demo/${subdir}/aauth-sdk"
  local copy_src="aauth-sdk"
  local pyproject="${WORK}/aauth-full-demo/${subdir}/pyproject.toml"
  rm -rf "${dst}"
  cp -R "${SDK_DIR}" "${dst}"
if [[ -f "${pyproject}" ]] && ! grep -q "structlog" "${pyproject}"; then
    awk '
      /^\]/ && in_deps && !done { print "    \"structlog>=24.1\","; done=1; in_deps=0 }
      /^\[project\]/ { in_project=1 }
      in_project && /^dependencies = \[/ { in_deps=1 }
      { print }
    ' "${pyproject}" > "${pyproject}.new" && mv "${pyproject}.new" "${pyproject}"
  fi
   local df="${WORK}/aauth-full-demo/${subdir}/Dockerfile"
  if ! grep -q "aauth-sdk" "${df}"; then
    # Backend builds from the upstream repo root; agent images build from their
    # own subdirectories. Match the Dockerfile COPY source to that context.
     if [[ "${subdir}" == "backend" ]]; then
    sed -i 's#CMD \["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"\]#CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]#' "${df}"
  else
    sed -i 's#CMD \["uv", "run", ".", "--host", "0.0.0.0", "--port", "[0-9]*"\]#CMD ["python", "__main__.py"]#' "${df}"
  fi
    # Inject before USER so the SDK install runs as root and fails fast.
    awk '
 /^USER/ && !done { print "COPY '"${copy_src}"' /opt/aauth-sdk"; print "ENV PYTHONPATH=/opt/aauth-sdk:/app"; print "RUN pip install /opt/aauth-sdk"; done=1 } { print }
      ' "${df}" > "${df}.new" && mv "${df}.new" "${df}"
    echo "    patched ${df}"
  fi
}

stage_sdk_for "backend"
stage_sdk_for "supply-chain-agent"
stage_sdk_for "market-analysis-agent"

build_agent() {
  local name="$1"; local subdir="$2"; local image="$3"
  echo "==> Building ${image} from upstream ${subdir}"
  if [[ "${subdir}" == "backend" ]]; then
    docker build -t "${image}" -f "${WORK}/aauth-full-demo/${subdir}/Dockerfile" "${WORK}/aauth-full-demo"
  else
    docker build -t "${image}" "${WORK}/aauth-full-demo/${subdir}"
  fi
  kind load docker-image "${image}" --name "${CLUSTER_NAME}"
}

build_agent "backend"         "backend"               "aauth/backend:dev"
build_agent "supply-chain"    "supply-chain-agent"    "aauth/supply-chain-agent:dev"
build_agent "market-analysis" "market-analysis-agent" "aauth/market-analysis-agent:dev"

echo "==> Building frontend (no SDK â€” UI is not a registered agent)"
if [[ ! -f "${WORK}/aauth-full-demo/supply-chain-ui/Dockerfile" ]]; then
  cat > "${WORK}/aauth-full-demo/supply-chain-ui/Dockerfile" <<'EOF'
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
EXPOSE 3050
CMD ["npm", "start"]
EOF
fi
docker build -t aauth/supply-chain-ui:dev "${WORK}/aauth-full-demo/supply-chain-ui"
kind load docker-image aauth/supply-chain-ui:dev --name "${CLUSTER_NAME}"

echo "==> Storing bootstrap tokens as K8s Secrets"
for name in backend supply-chain market-analysis; do
  kubectl -n "${APPS_NS:-apps}" create secret generic "${name}-aauth-bootstrap" \
    --from-file=token="${TOKENS_DIR}/${name}.token" \
    --from-file=id_url="${TOKENS_DIR}/${name}.id_url" \
    --dry-run=client -o yaml | kubectl apply -f -
done

echo "==> Applying workload manifests"
kubectl apply -f "${ROOT}/manifests/workloads/"
kubectl -n "${APPS_NS:-apps}" rollout restart \
  deploy/backend \
  deploy/supply-chain-agent \
  deploy/market-analysis-agent \
  deploy/supply-chain-ui

echo "==> Waiting for workloads"
for dep in backend supply-chain-agent market-analysis-agent supply-chain-ui; do
  kubectl -n "${APPS_NS:-apps}" rollout status deploy/${dep} --timeout=180s
done

echo "==> Apps deployed. Next: scripts/07-run-demo.sh"
echo "    UI:        http://localhost:3000"
echo "    backend:   http://localhost:8000"
echo "    gateway:   https://localhost:8443"
echo ""
echo "    Note: the boot wiring is auto-patched, but the OUTBOUND call-site"
echo "          edits are described in sdk/integration/*.md and may need"
echo "          hand-application if upstream's call-site shape changes."
