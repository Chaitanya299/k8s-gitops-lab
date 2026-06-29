#!/usr/bin/env bash
# Build the three app images and push them to the local kind registry.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REG="localhost:5001"
TAG="${TAG:-0.1.0}"

build() {
  local name="$1" ctx="$2"
  echo "→ $name"
  docker build -t "${REG}/${name}:${TAG}" "$ctx"
  docker push "${REG}/${name}:${TAG}"
}

build sample-ai-service "$ROOT/apps/sample-ai-service"
build platform-backend  "$ROOT/apps/platform-backend"
build platform-frontend "$ROOT/apps/platform-frontend"
