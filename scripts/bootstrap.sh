#!/usr/bin/env bash
# One-command local platform: kind + registry + ingress + ArgoCD + Gitea +
# monitoring + the platform itself. Idempotent-ish; safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER="${CLUSTER:-ai-platform}"
CTX="kind-${CLUSTER}"
REG_NAME="kind-registry"
REG_PORT="5001"
GITEA_USER="${GITEA_USER:-platform}"
GITEA_PASSWORD="${GITEA_PASSWORD:-platformdev123}"
export GITEA_USER GITEA_PASSWORD ROOT

step() { printf '\n\033[1;35m▶ %s\033[0m\n' "$*"; }
kc() { kubectl --context "$CTX" "$@"; }

# ── 1. Local registry + kind cluster ────────────────────────────────────────
step "Local registry"
if [ "$(docker inspect -f '{{.State.Running}}' "$REG_NAME" 2>/dev/null || true)" != 'true' ]; then
  docker run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" --name "$REG_NAME" registry:2
fi

step "kind cluster: $CLUSTER"
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --config "$ROOT/scripts/kind-config.yaml"
fi
docker network connect kind "$REG_NAME" 2>/dev/null || true

# Document the registry per the local-registry-hosting convention.
kc apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:5001"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
EOF

# ── 2. Ingress controller ───────────────────────────────────────────────────
step "ingress-nginx"
kc apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/kind/deploy.yaml
# rollout status (not `wait pod`) so it tolerates pods still being created.
kc -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=240s
# Admission webhook cert must be in place before we apply any Ingress.
kc -n ingress-nginx wait --for=condition=complete job/ingress-nginx-admission-create --timeout=120s 2>/dev/null || true

# ── 3. metrics-server (HPA needs it; --kubelet-insecure-tls for kind) ───────
step "metrics-server"
kc apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kc -n kube-system patch deployment metrics-server --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' || true

# ── 4. ArgoCD ───────────────────────────────────────────────────────────────
step "ArgoCD"
kc create namespace argocd --dry-run=client -o yaml | kc apply -f -
# Server-side apply: ArgoCD's CRDs exceed the 256KB client-side apply annotation.
kc apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kc -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{"data":{"server.insecure":"true"}}'
# Poll git every 30s so a Deploy click syncs quickly (ArgoCD default is 3m).
kc -n argocd patch configmap argocd-cm --type merge -p '{"data":{"timeout.reconciliation":"30s"}}'
kc -n argocd rollout status deploy/argocd-server --timeout=300s
kc -n argocd rollout restart deploy/argocd-server statefulset/argocd-application-controller
kc apply -f "$ROOT/deploy/argocd/ingress.yaml"

# ── 5. Gitea (GitOps source of truth) ───────────────────────────────────────
step "Gitea"
kc apply -f "$ROOT/deploy/gitea/gitea.yaml"
kc -n gitea rollout status deploy/gitea --timeout=240s

# ── 6. Monitoring (Prometheus + Grafana) ────────────────────────────────────
step "kube-prometheus-stack"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f "$ROOT/monitoring/kube-prometheus-stack.values.yaml" --wait --timeout 10m

step "Grafana dashboard"
kc -n monitoring create configmap ai-platform-dashboard \
  --from-file=ai-platform.json="$ROOT/monitoring/dashboards/ai-platform.json" \
  --dry-run=client -o yaml | kc apply -f -
kc -n monitoring label configmap ai-platform-dashboard grafana_dashboard=1 --overwrite

# ── 7. Build + push app images ──────────────────────────────────────────────
step "Build images"
"$ROOT/scripts/build-load-images.sh"

# ── 8. Seed Gitea with the repo ─────────────────────────────────────────────
step "Seed Gitea"
"$ROOT/scripts/seed-gitea.sh"

# ── 9. Deploy the platform (frontend + backend) ─────────────────────────────
step "Platform"
kc create namespace platform --dry-run=client -o yaml | kc apply -f -
kc -n platform create secret generic platform-backend-secrets \
  --from-literal=GITOPS_REPO_URL="http://${GITEA_USER}:${GITEA_PASSWORD}@gitea-http.gitea.svc.cluster.local:3000/platform/gitops.git" \
  --dry-run=client -o yaml | kc apply -f -
kc apply -k "$ROOT/deploy/platform"
kc -n platform rollout status deploy/platform-backend --timeout=180s
kc -n platform rollout status deploy/platform-frontend --timeout=180s

# ── 10. Hand the sample service to ArgoCD ───────────────────────────────────
step "ArgoCD app-of-apps"
kc apply -f "$ROOT/gitops/argocd/app-of-apps.yaml"

ARGO_PW="$(kc -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo '<not-ready>')"
cat <<EOF

\033[1;32m✓ Platform is up.\033[0m

  Dashboard : http://dashboard.127.0.0.1.nip.io
  ArgoCD    : http://argocd.127.0.0.1.nip.io   (admin / ${ARGO_PW})
  Grafana   : http://grafana.127.0.0.1.nip.io  (anonymous viewer)
  Gitea     : http://gitea.127.0.0.1.nip.io    (${GITEA_USER} / ${GITEA_PASSWORD})

Open the dashboard, pick sample-ai-service, set replicas, and Deploy.
EOF
