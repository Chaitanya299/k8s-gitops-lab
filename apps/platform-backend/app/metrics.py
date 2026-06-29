"""Headline metrics for the dashboard — thin proxy over the Prometheus HTTP API."""
from __future__ import annotations

import httpx

from .config import settings

# PromQL keyed by the label the dashboard shows. Driven by the sample service's
# /metrics (ai_requests_total, ai_request_latency_seconds).
QUERIES = {
    "requests_per_sec": 'sum(rate(ai_requests_total[1m]))',
    "p95_latency_ms": (
        '1000 * histogram_quantile(0.95, '
        'sum(rate(ai_request_latency_seconds_bucket[5m])) by (le))'
    ),
    "error_rate": (
        'sum(rate(ai_requests_total{status="error"}[5m])) '
        '/ clamp_min(sum(rate(ai_requests_total[5m])), 1)'
    ),
    "inflight": 'sum(ai_inflight_requests)',
}


async def summary() -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    async with httpx.AsyncClient(timeout=5) as http:
        for name, promql in QUERIES.items():
            out[name] = await _scalar(http, promql)
    return out


async def _scalar(http: httpx.AsyncClient, promql: str) -> float | None:
    try:
        resp = await http.get(
            f"{settings.prometheus_url}/api/v1/query", params={"query": promql}
        )
        resp.raise_for_status()
        result = resp.json()["data"]["result"]
        if not result:
            return None
        return float(result[0]["value"][1])
    except (httpx.HTTPError, KeyError, ValueError):
        return None
