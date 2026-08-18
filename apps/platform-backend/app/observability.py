"""Prometheus metrics and structured logging for the chat subsystem.

The platform already runs Prometheus and Grafana, so the assistant reports into
the same place everything else does rather than inventing a second story.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

CHAT_REQUESTS = Counter(
    "chat_requests_total", "Chat turns started, by outcome.", ["outcome"]
)
CHAT_DURATION = Histogram(
    "chat_request_duration_seconds", "Wall time of a full chat turn."
)
# cache_read is the canary: if it stays at zero across turns, something is
# invalidating the prompt prefix and every request is paying full price.
CHAT_TOKENS = Counter(
    "chat_tokens_total", "Tokens consumed by chat.", ["kind"]
)
CHAT_TOOL_CALLS = Counter(
    "chat_tool_calls_total", "Tool invocations from the assistant.", ["tool", "outcome"]
)
CHAT_ERRORS = Counter("chat_errors_total", "Chat failures, by type.", ["type"])
RAG_DURATION = Histogram(
    "rag_retrieval_duration_seconds", "Knowledge-store retrieval latency."
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _request_id.get(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    """Emit a structured line. Field values land as top-level JSON keys."""
    logger.log(level, msg, extra={"fields": fields})


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def current_request_id() -> str:
    return _request_id.get()


def anthropic_request_id(exc: Exception) -> str | None:
    """The SDK's server-side request id — the thing support can actually trace."""
    return getattr(getattr(exc, "response", None), "headers", {}).get("request-id") or getattr(
        exc, "request_id", None
    )


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
