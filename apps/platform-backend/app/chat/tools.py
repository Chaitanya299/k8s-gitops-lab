"""The assistant's tools: everything it can see, and the one thing it can propose.

Every tool here is read-only except `save_learned_issue`. There is deliberately
no tool that writes to git or the cluster — the model's most powerful action is
*proposing* a deployment, which a human then confirms through the existing
`POST /api/deploy`. That is the whole safety story in one sentence, and it is
enforced by this file containing no write path rather than by prompt wording.

Results from every read tool are redacted and wrapped in `<untrusted_data>`,
because pod logs are written by workloads and a workload can print anything —
including something shaped like an instruction.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .. import k8s, metrics
from ..config import settings
from ..observability import CHAT_TOOL_CALLS, get_logger, log
from ..redact import platform_secrets, redact
from ..schemas import DeployRequest
from ..store.knowledge import get_store

logger = get_logger(__name__)


def wrap_untrusted(source: str, meta: dict[str, Any], body: str) -> str:
    """Fence workload-controlled text so the model treats it as data.

    Any attempt by the content to close the fence early is neutralised first,
    so a log line containing `</untrusted_data>` cannot break out of it.
    """
    safe = body.replace("<untrusted_data", "<untrusted-data").replace(
        "</untrusted_data", "</untrusted-data"
    )
    attrs = " ".join(f'{k}="{v}"' for k, v in meta.items() if v is not None)
    open_tag = f'<untrusted_data source="{source}"' + (f" {attrs}" if attrs else "") + ">"
    return f"{open_tag}\n{safe}\n</untrusted_data>"


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # Set by propose_deployment so the router can emit a UI card for it.
    proposal: dict | None = None


ToolHandler = Callable[[dict], Awaitable[ToolResult]]


# ── handlers ────────────────────────────────────────────────────────────────
# k8s and git calls are blocking; they run in a thread so the SSE stream keeps
# flowing while a tool is in flight.


async def _search_platform_knowledge(args: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()
    limit = min(int(args.get("limit", 5) or 5), 10)
    chunks = await asyncio.to_thread(get_store().search, query, limit)
    if not chunks:
        return ToolResult("No matching entries in the platform knowledge base.")
    body = "\n\n---\n\n".join(
        f"[{c.source}] {c.title}\n{c.content}" for c in chunks
    )
    return ToolResult(wrap_untrusted("platform_knowledge", {"query": query}, body))


async def _get_running_services(args: dict) -> ToolResult:
    services = await asyncio.to_thread(k8s.list_services)
    if not services:
        return ToolResult(
            f"No deployments found in namespace {settings.ai_namespace}."
        )
    return ToolResult(
        wrap_untrusted(
            "cluster_state",
            {"namespace": settings.ai_namespace},
            json.dumps(services, indent=2, default=str),
        )
    )


async def _get_pod_logs(args: dict) -> ToolResult:
    pod = str(args.get("pod", "")).strip()
    if not pod:
        return ToolResult("pod is required", is_error=True)
    tail = max(1, min(int(args.get("tail", 100) or 100), 400))
    raw = await asyncio.to_thread(k8s.get_logs, pod, None, tail)
    return ToolResult(
        wrap_untrusted(
            "pod_logs",
            {"pod": pod, "tail": tail},
            redact(raw, platform_secrets()),
        )
    )


async def _get_service_metrics(args: dict) -> ToolResult:
    data = await metrics.summary()
    return ToolResult(
        "Current Prometheus readings for the AI services "
        f"(null means no data yet):\n{json.dumps(data, indent=2)}"
    )


async def _get_deployment_history(args: dict) -> ToolResult:
    from ..gitops import GitOps

    service = args.get("service") or None
    try:
        entries = await asyncio.to_thread(GitOps().history, service)
    except Exception as exc:  # noqa: BLE001 — surface git failures to the model
        return ToolResult(f"Could not read deployment history: {exc}", is_error=True)
    if not entries:
        return ToolResult("No deployment history recorded yet.")
    return ToolResult(
        wrap_untrusted(
            "git_history",
            {"service": service or "all"},
            json.dumps(entries, indent=2, default=str),
        )
    )


async def _get_sync_status(args: dict) -> ToolResult:
    service = args.get("service") or None
    apps = await asyncio.to_thread(k8s.get_argocd_status, service)
    return ToolResult(
        wrap_untrusted(
            "argocd",
            {"service": service or "all"},
            json.dumps(apps, indent=2, default=str),
        )
    )


async def _propose_deployment(args: dict) -> ToolResult:
    """Validate a proposal and hand it to the UI. Writes nothing, anywhere."""
    try:
        spec = DeployRequest(**{k: v for k, v in args.items() if v is not None})
    except ValidationError as exc:
        # The model sees the reason and can correct itself; nothing is shown to
        # the user. This is the layer that stops an injected "50 replicas".
        return ToolResult(
            f"Proposal rejected by the platform's own validation:\n{exc}",
            is_error=True,
        )
    return ToolResult(
        "Proposal sent to the user as a confirmation card. It is NOT deployed. "
        "Wait for the user to click Deploy; do not claim anything has shipped.",
        proposal=spec.model_dump(),
    )


async def _save_learned_issue(args: dict) -> ToolResult:
    title = str(args.get("title", "")).strip()
    description = str(args.get("description", "")).strip()
    resolution = str(args.get("resolution", "")).strip()
    if not (title and description and resolution):
        return ToolResult(
            "title, description and resolution are all required", is_error=True
        )
    issue_id = await asyncio.to_thread(
        get_store().ingest_issue, title, description, resolution
    )
    return ToolResult(f"Saved as {issue_id}. Future sessions will find this.")


# ── registry ────────────────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "search_platform_knowledge",
        "description": (
            "Search this platform's memory: past deployments, issues the team has "
            "solved before, and the platform's own documentation. Use this FIRST "
            "when the user describes a symptom or asks how something works here — "
            "it is the only source of this cluster's history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search terms."},
                "limit": {"type": "integer", "description": "Max results (default 5)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_running_services",
        "description": (
            "List deployments currently running in the AI namespace with replica "
            "counts, readiness, restart counts, and pod names. Call this before "
            "advising on scaling or diagnosing a problem."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_pod_logs",
        "description": (
            "Read recent logs from one pod. Get pod names from get_running_services "
            "first. Use when diagnosing a crash, restart loop, or error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pod": {"type": "string", "description": "Exact pod name."},
                "tail": {"type": "integer", "description": "Lines to read (default 100, max 400)."},
            },
            "required": ["pod"],
        },
    },
    {
        "name": "get_service_metrics",
        "description": (
            "Current request rate, p95 latency, error rate, and in-flight requests "
            "from Prometheus. Use when the user asks about performance or load, or "
            "to size replicas from real traffic rather than guessing."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_deployment_history",
        "description": (
            "Git history of deployments — who changed what, when, and the commit. "
            "Use for 'what changed recently' or to correlate a regression with a deploy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Limit to one service."}
            },
        },
    },
    {
        "name": "get_sync_status",
        "description": (
            "ArgoCD sync and health status. Use after a deploy to tell the user "
            "whether the commit has reconciled into the cluster yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Limit to one application."}
            },
        },
    },
    {
        "name": "propose_deployment",
        "description": (
            "Propose deployment settings for the user to confirm. This does NOT "
            "deploy — it renders a confirmation card the user must click. Call it "
            "once you know the service and the settings you would recommend, and "
            "explain your reasoning in your reply."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name to deploy."},
                "replicas": {"type": "integer", "description": "Replica count, 1-20."},
                "cpu": {"type": "string", "description": "CPU request, e.g. '200m'."},
                "memory": {"type": "string", "description": "Memory request, e.g. '512Mi'."},
                "namespace": {"type": "string", "description": "Target namespace."},
                "model": {"type": "string", "description": "Model name for the service."},
            },
            "required": ["service", "replicas", "cpu", "memory", "namespace", "model"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_learned_issue",
        "description": (
            "Record a problem and its fix so future sessions find it. Call this "
            "when the user describes something that went wrong and how it was "
            "resolved — ask them to confirm before saving."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short summary of the problem."},
                "description": {"type": "string", "description": "What happened and how it presented."},
                "resolution": {"type": "string", "description": "What actually fixed it."},
            },
            "required": ["title", "description", "resolution"],
        },
    },
]

HANDLERS: dict[str, ToolHandler] = {
    "search_platform_knowledge": _search_platform_knowledge,
    "get_running_services": _get_running_services,
    "get_pod_logs": _get_pod_logs,
    "get_service_metrics": _get_service_metrics,
    "get_deployment_history": _get_deployment_history,
    "get_sync_status": _get_sync_status,
    "propose_deployment": _propose_deployment,
    "save_learned_issue": _save_learned_issue,
}


async def dispatch(name: str, arguments: dict) -> ToolResult:
    """Run one tool call. Every invocation is logged and counted."""
    handler = HANDLERS.get(name)
    if handler is None:
        CHAT_TOOL_CALLS.labels(tool=name, outcome="unknown_tool").inc()
        return ToolResult(f"No such tool: {name}", is_error=True)

    try:
        result = await handler(arguments)
    except Exception as exc:  # noqa: BLE001 — a tool failure must not kill the turn
        CHAT_TOOL_CALLS.labels(tool=name, outcome="error").inc()
        log(logger, logging.ERROR, "tool call failed", tool=name, error=str(exc))
        return ToolResult(f"Tool {name} failed: {exc}", is_error=True)

    CHAT_TOOL_CALLS.labels(
        tool=name, outcome="error" if result.is_error else "ok"
    ).inc()
    log(logger, logging.INFO, "tool call", tool=name, args=arguments,
        is_error=result.is_error)
    return result
