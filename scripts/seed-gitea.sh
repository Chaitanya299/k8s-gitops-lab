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
# Standard gitea image runs as root; drop to the git user (it refuses to run as root).
kc -n gitea exec deploy/gitea -- su-exec git gitea admin user create \
  --username "$GITEA_USER" --password "$GITEA_PASSWORD" \
  --email "platform@local" --admin --must-change-password=false 2>&1 || true

echo "→ gitops repo"
curl -fsS -u "${GITEA_USER}:${GITEA_PASSWORD}" -H 'Content-Type: application/json' \
  -X POST "http://${GITEA_HOST}/api/v1/user/repos" \
  -d '{"name":"gitops","private":false,"default_branch":"main"}' >/dev/null 2>&1 || true

echo "→ push project"
cd "$ROOT"
[ -d .git ] || git init -q -b main
# Clear any stale locks (e.g. from an interrupted run or an IDE git integration).
rm -f .git/config.lock .git/index.lock 2>/dev/null || true
git config user.email "platform-bot@local"
git config user.name "platform-bot"
git add -A
git commit -qm "platform bootstrap" 2>/dev/null || true
git branch -M main
GITEA_REMOTE="http://${GITEA_USER}:${GITEA_PASSWORD}@${GITEA_HOST}/${GITEA_USER}/gitops.git"
git remote set-url gitea "$GITEA_REMOTE" 2>/dev/null || git remote add gitea "$GITEA_REMOTE"
git push -f -q gitea main
echo "✓ seeded http://${GITEA_HOST}/${GITEA_USER}/gitops"
