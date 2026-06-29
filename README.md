# AI Platform — Kubernetes GitOps (v1)

Internal platform to deploy AI services to Kubernetes via a GitOps workflow — no
`kubectl` after setup. Dashboard → **Deploy** → backend edits Helm values → git
commit/push → ArgoCD syncs → Kubernetes deploys → Prometheus/Grafana monitor.

This v1 runs **entirely on a local `kind` cluster** (no AWS, no accounts).

## Architecture

```
Next.js dashboard ─▶ FastAPI control-plane ─▶ Gitea (git, source of truth)
                          │ reads k8s API           │ watched by
                          ▼                          ▼
                     pods/logs/metrics          ArgoCD ─▶ Kubernetes ─▶ sample-ai-service
                          ▲                                                   │ /metrics
                     Prometheus ◀──────────────────────────────────────────── ┘
                          ▲
                     Grafana (iframed in dashboard)
```

## Prerequisites

`docker`, `kind`, `kubectl`, `helm`, `node`, `python3`. No cloud account needed.

```bash
# macOS
brew install kind kubectl helm
```

## Quickstart

```bash
make up        # create kind cluster, install ArgoCD + Gitea + monitoring, deploy platform
make demo      # print the URLs to open
make down      # tear everything down
```

Then open the dashboard URL, pick **sample-ai-service**, set replicas/CPU/memory,
and click **Deploy**. Watch ArgoCD sync it and Grafana chart the metrics.

## Layout

| Path | What |
|------|------|
| `apps/sample-ai-service` | The deployable FastAPI AI service (`/generate`, `/metrics`) |
| `apps/platform-backend`  | FastAPI control plane (deploy / status / logs / metrics / history) |
| `apps/platform-frontend` | Next.js + Tailwind dashboard |
| `charts/ai-service`      | Helm chart emitting Deployment/Service/Ingress/HPA/… |
| `gitops/`                | ArgoCD apps + per-service Helm values (the GitOps source of truth) |
| `monitoring/`            | kube-prometheus-stack values + Grafana dashboard |
| `infra/terraform/`       | AWS EKS/ECR/VPC modules — **code only, not applied in v1** |
| `scripts/`               | bootstrap / teardown / seed-gitea / build-load-images |

## Deferred to later versions

JWT auth + RBAC roles, Loki logging, AlertManager/Slack alerts, real AWS EKS
apply, Postgres/Redis. See the plan for the full mapping.
