#!/usr/bin/env bash
# Create the platform user + gitops repo in Gitea, then push this project to it.
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CLUSTER="${CLUSTER:-ai-platform}"
CTX="kind-${CLUSTER}"
GITEA_USER="${GITEA_USER:-platform}"
GITEA_PASSWORD="${GITEA_PASSWORD:-platformdev123}"
GITEA_HOST="gitea.127.0.0.1.nip.io"
kc() { kubectl --context "$CTX" "$@"; }

echo "→ wait for Gitea API"
for _ in $(seq 1 60); do
  curl -fsS "http://${GITEA_HOST}/api/healthz" >/dev/null 2>&1 && break || sleep 3
done

echo "→ admin user"
kc -n gitea exec deploy/gitea -- gitea admin user create \
  --username "$GITEA_USER" --password "$GITEA_PASSWORD" \
  --email "platform@local" --admin --must-change-password=false 2>/dev/null || true

echo "→ gitops repo"
curl -fsS -u "${GITEA_USER}:${GITEA_PASSWORD}" -H 'Content-Type: application/json' \
  -X POST "http://${GITEA_HOST}/api/v1/user/repos" \
  -d '{"name":"gitops","private":false,"default_branch":"main"}' >/dev/null 2>&1 || true

echo "→ push project"
cd "$ROOT"
[ -d .git ] || git init -q -b main
git config user.email "platform-bot@local"
git config user.name "platform-bot"
git add -A
git commit -qm "platform bootstrap" 2>/dev/null || true
git branch -M main
git remote remove gitea 2>/dev/null || true
git remote add gitea "http://${GITEA_USER}:${GITEA_PASSWORD}@${GITEA_HOST}/${GITEA_USER}/gitops.git"
git push -f -q gitea main
echo "✓ seeded http://${GITEA_HOST}/${GITEA_USER}/gitops"
