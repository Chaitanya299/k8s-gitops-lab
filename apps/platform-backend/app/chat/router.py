"""HTTP surface for the assistant: one SSE turn endpoint plus history and issues."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import require_user
from ..config import settings
from ..llm import LLMNotConfigured, get_provider
from ..observability import (
    CHAT_DURATION,
    CHAT_ERRORS,
    CHAT_REQUESTS,
    get_logger,
    log,
    new_request_id,
)
from ..store.conversations import get_store as get_conversation_store
from ..store.knowledge import get_store as get_knowledge_store
from . import agent

logger = get_logger(__name__)
router = APIRouter()

HEARTBEAT_SECONDS = 15

# ponytail: in-process rate limiter — correct for the single backend replica this
# platform runs. Move to Redis if the control plane is ever scaled out.
_hits: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(client: str) -> bool:
    window = 60.0
    now = time.monotonic()
    seen = _hits[client]
    while seen and now - seen[0] > window:
        seen.popleft()
    if len(seen) >= settings.chat_rate_limit_per_min:
        return True
    seen.append(now)
    return False


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class DeployResultRequest(BaseModel):
    conversation_id: str
    service: str
    committed: bool
    commit: str | None = None
    message: str = ""


class IssueRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=8000)
    resolution: str = Field(min_length=1, max_length=8000)


def _provider_or_503():
    try:
        return get_provider()
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _sse(frames: AsyncIterator[dict]) -> AsyncIterator[str]:
    """Serialise frames as SSE, injecting heartbeats so idle proxies hold the line."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump() -> None:
        try:
            async for frame in frames:
                await queue.put(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the client must learn it failed
            CHAT_ERRORS.labels(type=type(exc).__name__).inc()
            log(logger, logging.ERROR, "chat stream failed", error=str(exc))
            await queue.put({"type": "error", "message": f"Assistant failed: {exc}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if frame is None:
                break
            yield f"data: {json.dumps(frame)}\n\n"
    finally:
        task.cancel()


@router.post("/api/chat")
async def chat(
    req: ChatRequest, request: Request, user: dict = Depends(require_user)
) -> StreamingResponse:
    provider = _provider_or_503()
    client = request.client.host if request.client else "unknown"
    if _rate_limited(client):
        CHAT_REQUESTS.labels(outcome="rate_limited").inc()
        raise HTTPException(
            status_code=429,
            detail="Too many chat requests. Wait a moment and try again.",
        )

    request_id = new_request_id()
    conversations = get_conversation_store()

    cid = req.conversation_id
    if not cid or not conversations.exists(cid):
        cid = conversations.create()
    conversations.title_from(cid, req.message)

    async def frames() -> AsyncIterator[dict]:
        started = time.perf_counter()
        yield {"type": "start", "conversation_id": cid, "request_id": request_id}

        history = conversations.messages(cid)
        history.append(agent.user_message(req.message))
        conversations.append(cid, "user", history[-1]["content"])
        unsaved_from = len(history)
        outcome = "ok"

        try:
            async for frame in agent.run_turn(provider, history, agent.TokenBudget()):
                if frame.get("type") == "error":
                    outcome = "error"
                yield frame
        finally:
            for msg in history[unsaved_from:]:
                conversations.append(cid, msg["role"], msg["content"])
            CHAT_DURATION.observe(time.perf_counter() - started)
            CHAT_REQUESTS.labels(outcome=outcome).inc()

    return StreamingResponse(
        _sse(frames()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/api/chat/deploy-result")
def deploy_result(req: DeployResultRequest, user: dict = Depends(require_user)) -> dict:
    """Feed a confirmed deploy's outcome back so the assistant knows it happened."""
    conversations = get_conversation_store()
    if not conversations.exists(req.conversation_id):
        raise HTTPException(status_code=404, detail="unknown conversation")

    if req.committed:
        text = (
            f"[platform] The user confirmed the deployment of {req.service}. "
            f"It was committed as {req.commit}. ArgoCD will reconcile within about "
            f"30 seconds — use get_sync_status to check."
        )
    else:
        text = (
            f"[platform] The deployment of {req.service} made no change "
            f"({req.message or 'settings already match'}). Nothing was committed."
        )
    conversations.append(
        req.conversation_id, "user", [{"type": "text", "text": text}]
    )
    return {"recorded": True}


@router.get("/api/chat/conversations")
def list_conversations(user: dict = Depends(require_user)) -> list[dict]:
    return get_conversation_store().list_recent()


@router.get("/api/chat/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, user: dict = Depends(require_user)
) -> dict:
    conversations = get_conversation_store()
    if not conversations.exists(conversation_id):
        raise HTTPException(status_code=404, detail="unknown conversation")
    return {
        "id": conversation_id,
        "messages": conversations.messages(conversation_id),
    }


@router.post("/api/issues")
def create_issue(req: IssueRequest, user: dict = Depends(require_user)) -> dict:
    issue_id = get_knowledge_store().ingest_issue(
        req.title, req.description, req.resolution
    )
    return {"id": issue_id, "title": req.title}


@router.get("/api/issues")
def list_issues(user: dict = Depends(require_user)) -> list[dict]:
    return get_knowledge_store().list_issues()


@router.delete("/api/issues/{issue_id}")
def delete_issue(issue_id: str, user: dict = Depends(require_user)) -> dict:
    if not get_knowledge_store().delete_issue(issue_id):
        raise HTTPException(status_code=404, detail="unknown issue")
    return {"deleted": issue_id}


@router.get("/api/chat/status")
def chat_status(user: dict = Depends(require_user)) -> dict:
    """Lets the UI hide or explain the assistant without provoking a failed turn."""
    try:
        provider = get_provider()
    except LLMNotConfigured as exc:
        return {"enabled": False, "reason": str(exc)}
    return {"enabled": True, "provider": provider.name}
