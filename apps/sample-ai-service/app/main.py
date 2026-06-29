"""Sample AI service — the workload the platform deploys via GitOps.

Exposes the endpoints a real inference service would: health/readiness probes,
a /generate inference endpoint, and Prometheus /metrics. If OLLAMA_URL is set it
proxies to a real model (Gemma/Qwen/Mistral via Ollama); otherwise it returns a
canned response so the demo needs no GPU or multi-GB model pull.
"""
from __future__ import annotations

import os
import time

import httpx
from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel

SERVICE_NAME = os.getenv("SERVICE_NAME", "sample-ai-service")
MODEL = os.getenv("MODEL", "echo")
OLLAMA_URL = os.getenv("OLLAMA_URL")  # e.g. http://ollama:11434 — optional

app = FastAPI(title=SERVICE_NAME)

REQUESTS = Counter(
    "ai_requests_total", "Total inference requests", ["service", "model", "status"]
)
LATENCY = Histogram(
    "ai_request_latency_seconds", "Inference latency", ["service", "model"]
)
INFLIGHT = Gauge("ai_inflight_requests", "In-flight requests", ["service"])


class GenerateRequest(BaseModel):
    prompt: str
    model: str | None = None


class GenerateResponse(BaseModel):
    service: str
    model: str
    output: str
    latency_ms: float


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    # ponytail: readiness == process is up. Add model-loaded check when a real
    # model is wired in (Ollama warmup, weights mmap'd, etc.).
    return {"status": "ready"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    model = req.model or MODEL
    INFLIGHT.labels(SERVICE_NAME).inc()
    start = time.perf_counter()
    status = "ok"
    try:
        output = await _infer(req.prompt, model)
    except Exception:  # noqa: BLE001 — surface as a counted failure, not a 500 storm
        status = "error"
        output = "inference failed"
    finally:
        elapsed = time.perf_counter() - start
        LATENCY.labels(SERVICE_NAME, model).observe(elapsed)
        REQUESTS.labels(SERVICE_NAME, model, status).inc()
        INFLIGHT.labels(SERVICE_NAME).dec()
    return GenerateResponse(
        service=SERVICE_NAME, model=model, output=output, latency_ms=elapsed * 1000
    )


async def _infer(prompt: str, model: str) -> str:
    if not OLLAMA_URL:
        return f"[{model}] echo: {prompt}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
