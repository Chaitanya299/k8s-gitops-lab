# AI Platform — Kubernetes GitOps (v1)

An internal platform for deploying AI services to Kubernetes via a GitOps
workflow — **no `kubectl` after setup**. A Next.js dashboard talks to a FastAPI
control plane, which commits Helm values to Gitea. ArgoCD watches that repo and
reconciles the cluster. Prometheus + Grafana handle observability.

v1 runs entirely on a **local `kind` cluster** — no cloud account needed.

---

## How it works

```
Next.js dashboard ─▶ FastAPI control plane ─▶ Gitea (git = source of truth)
                          │ reads k8s API           │ watched by
                          ▼                          ▼
                     pods/logs/metrics          ArgoCD ─▶ Kubernetes ─▶ sample-ai-service
                          ▲                                                   │ /metrics
                     Prometheus ◀──────────────────────────────────────────── ┘
                          ▲
                     Grafana (iframed in dashboard)
```

**The one invariant:** deploys go through git, never `kubectl`. `POST /api/deploy`
mutates the service's Helm values file, commits, and pushes. ArgoCD reconciles
from that commit. The backend has read-only k8s RBAC — it cannot write to the
cluster directly.

---

## Prerequisites

| Tool | Install (macOS) |
|------|----------------|
| Docker | [docker.com/get-started](https://www.docker.com/get-started) |
| kind | `brew install kind` |
| kubectl | `brew install kubectl` |
| Helm | `brew install helm` |
| Node.js ≥ 18 | `brew install node` |
| Python ≥ 3.11 | `brew install python` |

---

## Quickstart

```bash
# 1. Bring up the full platform (takes ~5 min on first run)
make up

# 2. Print service URLs
make demo

# 3. Tear everything down
make down
```

`make up` runs `scripts/bootstrap.sh` which:
1. Creates a local Docker registry on port `5001`
2. Creates a `kind` cluster named `ai-platform`
3. Installs ingress-nginx + metrics-server
4. Installs ArgoCD
5. Installs Gitea and seeds the GitOps repo
6. Installs kube-prometheus-stack (Prometheus + Grafana)
7. Builds and pushes platform images to the local registry
8. Deploys the platform (dashboard + backend) via Kustomize
9. Deploys the sample AI service via ArgoCD

After `make demo`, open:

| Service | URL |
|---------|-----|
| Dashboard | http://dashboard.127.0.0.1.nip.io |
| ArgoCD | http://argocd.127.0.0.1.nip.io |
| Grafana | http://grafana.127.0.0.1.nip.io |
| Gitea | http://gitea.127.0.0.1.nip.io |

---

## Deploying a service

1. Open the dashboard
2. Select **sample-ai-service**
3. Set replicas, CPU, memory (and optionally image tag or model)
4. Click **Deploy**

The backend commits updated Helm values to Gitea. ArgoCD detects the commit
(reconciles every 30 s) and rolls out the change. The dashboard's **Services**
tab shows live pod status; **Monitoring** shows Prometheus metrics; **History**
shows the git commit log.

---

## Development

### Install backend dependencies

```bash
pip install -r apps/platform-backend/requirements.txt
```

### Run the backend locally

```bash
cd apps/platform-backend
uvicorn app.main:app --reload
# API at http://localhost:8000  (requires a running cluster for k8s/metrics routes)
```

### Run the frontend locally

```bash
cd apps/platform-frontend
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
# Dashboard at http://localhost:3000
```

### Run tests

```bash
make test
# Runs: pytest (core loop) + helm lint + helm template + next build
```

Or the core loop only (no cluster needed):

```bash
python3 -m pytest tests/test_gitops_loop.py -q
```

### Other make targets

```bash
make build   # Build + push app images to the local registry
make lint    # Helm lint only
make demo    # Print the service URLs
```

---

## Project layout

```
.
├── apps/
│   ├── platform-backend/       # FastAPI control plane
│   │   └── app/
│   │       ├── main.py         # Route definitions (/api/deploy, /services, /logs, /metrics, /history)
│   │       ├── gitops.py       # The deploy loop — git commit/push via GitOps class
│   │       ├── k8s.py          # Read-only cluster views (pods, logs)
│   │       ├── metrics.py      # Prometheus PromQL proxy
│   │       ├── auth.py         # Auth seam (stub in v1; slot JWT here later)
│   │       └── config.py       # 12-factor env config with sane in-cluster defaults
│   ├── platform-frontend/      # Next.js + Tailwind dashboard
│   │   └── app/
│   │       ├── page.tsx        # Deploy form (home)
│   │       ├── services/       # Live pod status
│   │       ├── monitoring/     # Grafana iframe + Prometheus metrics
│   │       └── history/        # Git commit log
│   └── sample-ai-service/      # Deployable FastAPI AI service (/generate, /metrics)
├── charts/
│   └── ai-service/             # Helm chart: Deployment/Service/Ingress/HPA/ServiceMonitor
├── gitops/
│   ├── argocd/                 # ArgoCD Application manifests (app-of-apps)
│   └── environments/dev/       # Per-service Helm values — the GitOps source of truth
├── infra/
│   └── terraform/              # AWS EKS/ECR/VPC modules (code only, not applied in v1)
├── monitoring/                 # kube-prometheus-stack values + Grafana dashboard JSON
├── scripts/
│   ├── bootstrap.sh            # Full platform bringup
│   ├── teardown.sh             # Cluster + registry teardown
│   ├── seed-gitea.sh           # Creates Gitea org/user/repo and pushes the gitops tree
│   └── build-load-images.sh    # Builds images and pushes to local registry
└── tests/
    └── test_gitops_loop.py     # Core loop guard (pure-function + end-to-end git test)
```

---

## Backend API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness check |
| `POST` | `/api/deploy` | Commit updated Helm values and push to Gitea |
| `GET` | `/api/services` | Live Deployment/pod status from the cluster |
| `GET` | `/api/logs/{pod}` | Tail pod logs (`?tail=200`) |
| `GET` | `/api/metrics` | Prometheus headline metrics (rps, p95 latency, error rate, inflight) |
| `GET` | `/api/history` | Git commit log (`?service=sample-ai-service`) |
| `GET` | `/api/config` | Non-secret runtime config (namespace, etc.) |

### Deploy request body

```json
{
  "service": "sample-ai-service",
  "replicas": 2,
  "cpu": "200m",
  "memory": "256Mi",
  "namespace": "ai-services",
  "image_tag": null,
  "model": null,
  "env": {}
}
```

---

## Configuration

All backend config is environment-driven. Defaults work out-of-the-box inside
the cluster. Override via env vars for local development:

| Env var | Default | Description |
|---------|---------|-------------|
| `GITOPS_REPO_URL` | in-cluster Gitea URL | Git remote the backend commits to |
| `GITOPS_BRANCH` | `main` | Branch ArgoCD watches |
| `GITOPS_VALUES_TEMPLATE` | `gitops/environments/dev/{service}.yaml` | Path pattern to per-service values |
| `GITOPS_WORKDIR` | `/tmp/gitops` | Local clone path inside the backend pod |
| `AI_NAMESPACE` | `ai-services` | Kubernetes namespace the platform watches |
| `PROMETHEUS_URL` | in-cluster Prometheus URL | PromQL endpoint |
| `GIT_AUTHOR_NAME` | `platform-bot` | Git commit author name |
| `GIT_AUTHOR_EMAIL` | `platform-bot@local` | Git commit author email |

---

## Architecture notes

- **`apply_settings()` in `gitops.py` is a pure function** — no I/O, fully
  unit-tested. `GitOps` wraps it with git I/O. Keep it pure.
- **One global git lock** serializes all commits because the workdir is a single
  shared checkout. Per-service locks are the upgrade path if throughput matters.
- **Identical re-deploys are no-ops** — if the values file is unchanged after
  `apply_settings`, no commit is made.
- **Backend is read-only against the cluster** — status and logs use the k8s API;
  writes go through git → ArgoCD only.
- **`ponytail:` comments** in source mark intentional v1 simplifications (wide-open
  CORS, stubbed auth, single namespace) and name the upgrade path.

---

## Deferred (v2+)

- JWT auth + Admin/Developer/Viewer RBAC (seam is `app/auth.py`)
- Multi-namespace deployments (one ArgoCD Application per namespace)
- Loki log aggregation
- AlertManager + Slack alerts
- Real AWS EKS apply (`infra/terraform/` is ready, not wired)
- Postgres/Redis for platform state
