"""Read-only views of the cluster for the status/logs APIs.

Uses in-cluster config when running as a pod, falls back to local kubeconfig for
dev. All read-only — the backend has no write RBAC (deploys go through git).
"""
from __future__ import annotations

from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .config import settings

_loaded = False


def _ensure_config() -> None:
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _loaded = True


def _age(start) -> str:
    if not start:
        return ""
    delta = datetime.now(timezone.utc) - start
    secs = int(delta.total_seconds())
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def list_services(namespace: str | None = None) -> list[dict]:
    """One row per Deployment with live pod status, drawn from the AI namespace."""
    _ensure_config()
    ns = namespace or settings.ai_namespace
    apps = client.AppsV1Api()
    core = client.CoreV1Api()
    try:
        deploys = apps.list_namespaced_deployment(ns).items
    except ApiException:
        return []

    out = []
    for d in deploys:
        selector = ",".join(f"{k}={v}" for k, v in (d.spec.selector.match_labels or {}).items())
        pods = core.list_namespaced_pod(ns, label_selector=selector).items
        restarts = sum(
            cs.restart_count
            for p in pods
            for cs in (p.status.container_statuses or [])
        )
        out.append(
            {
                "name": d.metadata.name,
                "namespace": ns,
                "desired": d.spec.replicas or 0,
                "ready": d.status.ready_replicas or 0,
                "available": d.status.available_replicas or 0,
                "restarts": restarts,
                "age": _age(d.metadata.creation_timestamp),
                "pods": [
                    {
                        "name": p.metadata.name,
                        "phase": p.status.phase,
                        "node": p.spec.node_name,
                    }
                    for p in pods
                ],
            }
        )
    return out


def get_logs(pod: str, namespace: str | None = None, tail: int = 200) -> str:
    _ensure_config()
    ns = namespace or settings.ai_namespace
    core = client.CoreV1Api()
    try:
        return core.read_namespaced_pod_log(pod, ns, tail_lines=tail)
    except ApiException as e:
        return f"error reading logs: {e.reason}"


def get_argocd_status(service: str | None = None) -> list[dict]:
    """ArgoCD Application sync/health — read-only, for 'did my deploy land yet?'.

    ArgoCD owns reconciliation; the backend only observes it. The RBAC for this
    grants get/list/watch on applications and nothing else.
    """
    _ensure_config()
    api = client.CustomObjectsApi()
    kwargs = {"group": "argoproj.io", "version": "v1alpha1", "namespace": "argocd",
              "plural": "applications"}
    try:
        if service:
            items = [api.get_namespaced_custom_object(name=service, **kwargs)]
        else:
            items = api.list_namespaced_custom_object(**kwargs).get("items", [])
    except ApiException as e:
        return [{"error": f"cannot read ArgoCD applications: {e.reason}"}]

    out = []
    for app in items:
        status = app.get("status", {}) or {}
        sync = status.get("sync", {}) or {}
        health = status.get("health", {}) or {}
        operation = (status.get("operationState", {}) or {})
        out.append(
            {
                "name": (app.get("metadata", {}) or {}).get("name"),
                "sync_status": sync.get("status"),
                "health_status": health.get("status"),
                "revision": (sync.get("revision") or "")[:8],
                "last_operation": operation.get("phase"),
                "last_operation_message": operation.get("message"),
                "finished_at": operation.get("finishedAt"),
            }
        )
    return out
