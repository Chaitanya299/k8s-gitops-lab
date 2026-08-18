"""Request/response models shared by the HTTP API and the chat assistant.

`DeployRequest` lives here rather than in main.py so the assistant can validate a
proposed deployment against the *same* model the deploy endpoint enforces —
one definition of what a legal deploy is, checked on both paths.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DeployRequest(BaseModel):
    service: str = "sample-ai-service"
    replicas: int = Field(1, ge=1, le=20)
    cpu: str = "100m"
    memory: str = "128Mi"
    namespace: str = "ai-services"
    image_tag: str | None = None
    model: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
