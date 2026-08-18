"""Platform control-plane API. All routes under /api (same-origin with the UI)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from . import k8s, metrics
from .auth import require_user
from .chat import router as chat_router
from .config import settings
from .gitops import DeploySpec, GitOps
from .observability import configure_logging, get_logger, log, metrics_response
from .schemas import DeployRequest
from .store import db as store_db
from .store.knowledge import get_store as get_knowledge_store

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare the assistant's store. Never fatal — chat is not the platform."""
    try:
        store_db.ensure_ready()
        docs_dir = Path(__file__).resolve().parent / "platform_docs"
        seeded = get_knowledge_store().seed_docs(sorted(docs_dir.glob("*.md")))
        log(logger, logging.INFO, "knowledge store ready", docs_seeded=seeded)
    except Exception as e:  # noqa: BLE001
        log(logger, logging.ERROR, "knowledge store unavailable", error=str(e))
    yield


app = FastAPI(title="AI Platform Control Plane", lifespan=lifespan)

# ponytail: wide-open CORS — single-tenant local dev platform. Tighten when auth lands.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

gitops = GitOps()
app.include_router(chat_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness. Deliberately independent of chat/LLM configuration — an unset
    ANTHROPIC_API_KEY must never take the control plane down."""
    return {"status": "ok"}


@app.get("/metrics")
def prometheus_metrics() -> Response:
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


@app.post("/api/deploy")
def deploy(req: DeployRequest, user: dict = Depends(require_user)) -> dict:
    spec = DeploySpec(**req.model_dump())
    try:
        result = gitops.deploy(spec)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gitops error: {e}") from e

    if result.get("committed"):
        # Indexing is best-effort. The deploy loop is the platform's core; a
        # knowledge-store failure must never turn a successful deploy into a 500.
        try:
            get_knowledge_store().ingest_deployment(result, spec)
        except Exception as e:  # noqa: BLE001
            log(logger, logging.WARNING, "deploy not indexed", error=str(e))
    return result


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
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gitops error: {e}") from e


@app.get("/api/config")
def config_view(user: dict = Depends(require_user)) -> dict:
    """Non-secret config the UI needs (e.g. which namespace it's watching)."""
    return {"ai_namespace": settings.ai_namespace}
