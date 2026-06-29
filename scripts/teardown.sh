#!/usr/bin/env bash
set -euo pipefail
CLUSTER="${CLUSTER:-ai-platform}"
kind delete cluster --name "$CLUSTER" || true
docker rm -f kind-registry 2>/dev/null || true
echo "✓ torn down"
