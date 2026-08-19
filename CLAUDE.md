# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An internal **AI deployment platform** built on a GitOps workflow. A dashboard
(Next.js) drives a control-plane API (FastAPI) that deploys AI services to
Kubernetes — **without ever calling `kubectl`**. v1 runs entirely on a local
`kind` cluster; no cloud, no accounts (see `README.md`).

## The one invariant: deploys go through git, never `kubectl`

This is the architectural rule everything else follows from. Read
[apps/platform-backend/app/gitops.py](apps/platform-backend/app/gitops.py) before
touching the deploy path.

```
Next.js dashboard ──▶ FastAPI control plane ──▶ Gitea (git = source of truth)
                          │ reads k8s API              │ watched by
                          ▼                            ▼
                     pods/logs/metrics            ArgoCD ──▶ Kubernetes ──▶ sample-ai-service
                          ▲                                                      │ /metrics
                     Prometheus ◀──────────────────────────────────────────────┘
```

- A `POST /api/deploy` mutates a service's Helm **values file** in the Gitea
  repo, commits, and pushes. ArgoCD reconciles the cluster from that commit.
  The backend has **read-only** k8s RBAC — it cannot write to the cluster.
- `apply_settings()` in `gitops.py` is a **pure function** (no I/O) and is the
  unit-tested core. Keep it pure — `GitOps` wraps it with the git I/O.
- `git` operations are serialized by one global lock because the workdir is a
  single shared checkout. Don't parallelize without per-service workdirs.
- Identical re-deploys must be **no-ops** (no empty commits) — a guarded
  behavior, see `test_deploy_commits_change`.

If you change the deploy loop and `tests/test_gitops_loop.py` still passes, the
platform's core still works. If that test breaks, the whole platform is
decorative.

## Commands

```bash
make up      # bootstrap.sh: create kind cluster, install ArgoCD+Gitea+monitoring, seed Gitea, deploy
make down    # teardown
make build   # build + push app images to the local registry
make demo    # print the dashboard / ArgoCD / Grafana / Gitea URLs
make test    # pytest + helm lint + helm template + frontend build  (the full gate)
make lint    # helm lint charts/ai-service
```

Narrower loops:

```bash
# One test (the core-loop guard runs with plain pytest, no cluster needed)
python3 -m pytest tests/test_gitops_loop.py::test_apply_settings_is_pure_and_correct -q

# Frontend dev against a running backend
cd apps/platform-frontend && npm install && NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev

# Backend locally (uses your local kubeconfig for the read-only k8s views)
cd apps/platform-backend && pip install -r requirements.txt && uvicorn app.main:app --reload
```

## Layout that isn't obvious from the tree

| Path | Role |
|------|------|
| `apps/platform-backend/app/gitops.py` | The deploy loop — the heart of the system |
| `apps/platform-backend/app/k8s.py` | **Read-only** cluster views (status/logs); in-cluster config, falls back to local kubeconfig |
| `apps/platform-backend/app/metrics.py` | Thin proxy over the Prometheus HTTP API; PromQL lives here |
| `apps/platform-backend/app/auth.py` | **Stub seam** — v1 has no auth; `require_user()` returns a fake admin. Every protected route already `Depends` on it, so turning auth on is a one-file change |
| `apps/platform-backend/app/chat/` | The deploy assistant: `agent.py` (manual tool-use loop + constant system prompt — the prompt is a module constant so prompt caching works), `tools.py` (8 read-only tools + `propose_deployment`; every tool result is redacted and `<untrusted_data>`-wrapped), `router.py` (SSE `/api/chat`). No tool writes to git/cluster |
| `apps/platform-backend/app/llm/` | Pluggable providers behind `LLMProvider`; `get_provider()` raises `LLMNotConfigured` when `LLM_PROVIDER` is unset/unknown (chat routes 503). Claude via the `anthropic` SDK; Gemini, OpenAI, and Ollama are raw-httpx adapters (no extra deps) that translate the canonical Anthropic-shaped history to each wire format. Adding one = a new adapter + a factory branch |
| `apps/platform-backend/app/store/` | SQLite (WAL, numbered migrations): `knowledge.py` (BM25/FTS5 retrieval behind a `Retriever` seam) + `conversations.py`. Backed by the `platform-knowledge` PVC |
| `apps/platform-backend/app/redact.py` | Secret redaction applied at the boundary — every tool result, before prompt or storage. Pure; tested exhaustively |
| `apps/sample-ai-service/` | The workload the platform deploys. Echoes by default; proxies a real model only if `OLLAMA_URL` is set |
| `charts/ai-service/` | The Helm chart `apply_settings` mutates via its values file |
| `gitops/environments/dev/<service>.yaml` | Per-service Helm values = the GitOps source of truth that gets committed |
| `gitops/argocd/` | ArgoCD app-of-apps + per-service `Application`s (repoURL points at in-cluster Gitea) |
| `infra/terraform/` | AWS EKS/ECR modules — **code only, not applied in v1**. The only path that needs real cloud credentials |

## Conventions

- **Config is env-driven (12-factor)** with sane in-cluster defaults in
  `app/config.py`. No secrets in code. The backend's git credentials come from a
  secret created **imperatively by `bootstrap.sh`**, never committed (see the note
  in `deploy/platform/kustomization.yaml`).
- **Helm values are environment data, not base manifests.** Deploy requests
  change `gitops/environments/<env>/<service>.yaml`, not the chart templates.
- **`ponytail:` comments mark deliberate v1 simplifications** (global lock,
  wide-open CORS, stubbed auth, single target namespace) and name the upgrade
  path. They're intentional scope cuts — don't "fix" them without checking the
  deferred-features list in `README.md`.
- The full gate is `make test`; CI runs in `.github/workflows/ci.yml`.

## Helper docs

`.claude/QUICK_START.md`, `.claude/ARCHITECTURE_MAP.md`, and
`.claude/COMMON_MISTAKES.md` hold quick references (file locations, commands,
pitfalls). Read `COMMON_MISTAKES.md` first if something behaves unexpectedly.
