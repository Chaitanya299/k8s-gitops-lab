"""Platform control-plane API. All routes under /api (same-origin with the UI)."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import k8s, metrics
from .auth import require_user
from .config import settings
from .gitops import DeploySpec, GitOps

app = FastAPI(title="AI Platform Control Plane")

# ponytail: wide-open CORS — single-tenant local dev platform. Tighten when auth lands.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

gitops = GitOps()


class DeployRequest(BaseModel):
    service: str = "sample-ai-service"
    replicas: int = Field(1, ge=1, le=20)
    cpu: str = "100m"
    memory: str = "128Mi"
    namespace: str = "ai-services"
    image_tag: str | None = None
    model: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/deploy")
def deploy(req: DeployRequest, user: dict = Depends(require_user)) -> dict:
    try:
        return gitops.deploy(DeploySpec(**req.model_dump()))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — surface git/clone failures to the UI
        raise HTTPException(status_code=502, detail=f"gitops error: {e}") from e


@app.get("/api/services")
def services(user: dict = Depends(require_user)) -> list[dict]:
    return k8s.list_services()


@app.get("/api/logs/{pod}")
def logs(pod: str, tail: int = 200, user: dict = Depends(require_user)) -> dict:
    return {"pod": pod, "logs": k8s.get_logs(pod, tail=tail)}


@app.get("/api/metrics")
async def service_metrics(user: dict = Depends(require_user)) -> dict:
    return await metrics.summary()


@app.get("/api/history")
def history(service: str | None = None, user: dict = Depends(require_user)) -> list[dict]:
    try:
        return gitops.history(service=service)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"gitops error: {e}") from e


@app.get("/api/config")
def config_view(user: dict = Depends(require_user)) -> dict:
    """Non-secret config the UI needs (e.g. which namespace it's watching)."""
    return {"ai_namespace": settings.ai_namespace}
