#!/usr/bin/env bash
# preflight.sh — sanity-check the local environment before `make demo`.
# Run this first. If everything is green, proceed with the numbered scripts.
set -uo pipefail

PASS=0
FAIL=0

check() {
  local label="$1"; local cmd="$2"; local hint="$3"
  if eval "$cmd" >/dev/null 2>&1; then
    printf "  \033[32m✓\033[0m  %-32s\n" "$label"
    PASS=$((PASS+1))
  else
    printf "  \033[31m✗\033[0m  %-32s  → %s\n" "$label" "$hint"
    FAIL=$((FAIL+1))
  fi
}

echo ""
echo "AAuth Mission Platform — preflight check"
echo ""
echo "Required tools:"
check "docker"   "docker version"                "install Docker Desktop with WSL2 backend"
check "kind"     "kind version"                  "go install sigs.k8s.io/kind@latest  OR  choco install kind"
check "kubectl"  "kubectl version --client"      "https://kubernetes.io/docs/tasks/tools/"
check "helm"     "helm version --short"          "https://helm.sh/docs/intro/install/"
check "make"     "make --version"                "install GNU make (apt/brew/choco/scoop)"
check "jq"       "jq --version"                  "apt install jq  /  brew install jq"
check "curl"     "curl --version"                "should be everywhere"
check "git"      "git --version"                 "https://git-scm.com/downloads"
check "python3"  "python3 --version"             "needed for sdk/integration/patches/apply_patches.py"

echo ""
echo "Docker daemon reachable:"
check "docker info"          "docker info"            "start Docker Desktop"
check "8 GB RAM allocated"   "[ \"\$(docker info --format '{{.MemTotal}}' 2>/dev/null)\" -gt 8000000000 ]"  "raise Docker Desktop RAM limit"

echo ""
echo "Host port availability (KIND will publish these):"
for port in 80 443 3000 8000 8080 8443 9000 9001; do
  if (echo > /dev/tcp/127.0.0.1/$port) >/dev/null 2>&1; then
    printf "  \033[33m!\033[0m  port %-5s already in use\n" "$port"
  else
    printf "  \033[32m✓\033[0m  port %-5s free\n" "$port"
  fi
done

echo ""
echo "Repo layout:"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for f in Makefile kind/kind-cluster.yaml manifests/00-namespaces.yaml \
         manifests/keycloak/01-keycloak-values.yaml manifests/platform/01-registry-service.yaml \
         scripts/00-kind-up.sh sdk/python/pyproject.toml; do
  if [ -f "${ROOT}/${f}" ]; then
    printf "  \033[32m✓\033[0m  %s\n" "$f"
  else
    printf "  \033[31m✗\033[0m  %s missing\n" "$f"
    FAIL=$((FAIL+1))
  fi
done

echo ""
if [ $FAIL -eq 0 ]; then
  echo "  \033[1;32mAll checks passed.\033[0m  You can run:  make USE_KEYCLOAK=1 demo"
  exit 0
else
  echo "  \033[1;31m${FAIL} checks failed.\033[0m  Fix the items above before running make demo."
  exit 1
fi
