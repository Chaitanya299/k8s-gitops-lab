// Same-origin by default: the dashboard ingress routes /api/* to the backend.
// Override with NEXT_PUBLIC_API_BASE for running `next dev` outside the cluster.
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const GRAFANA_URL =
  process.env.NEXT_PUBLIC_GRAFANA_URL ?? "http://grafana.127.0.0.1.nip.io";
