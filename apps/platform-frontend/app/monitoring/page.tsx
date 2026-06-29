"use client";

import { useEffect, useState } from "react";
import { api, GRAFANA_URL } from "@/lib/api";

type Metrics = {
  requests_per_sec: number | null;
  p95_latency_ms: number | null;
  error_rate: number | null;
  inflight: number | null;
};

const fmt = (v: number | null, suffix = "", digits = 2) =>
  v === null || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}${suffix}`;

export default function MonitoringPage() {
  const [m, setM] = useState<Metrics | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api<Metrics>("/metrics")
        .then((d) => alive && setM(d))
        .catch(() => {});
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const cards = [
    { label: "Requests / sec", value: fmt(m?.requests_per_sec ?? null) },
    { label: "p95 latency", value: fmt(m?.p95_latency_ms ?? null, " ms", 0) },
    { label: "Error rate", value: fmt((m?.error_rate ?? null) && (m!.error_rate! * 100), "%") },
    { label: "In-flight", value: fmt(m?.inflight ?? null, "", 0) },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold text-white">Monitoring</h1>
      <p className="mt-1 text-sm text-slate-400">
        Prometheus metrics from the deployed services · Grafana below
      </p>

      <div className="mt-5 grid grid-cols-4 gap-4">
        {cards.map((c) => (
          <div key={c.label} className="card">
            <div className="text-xs uppercase tracking-wide text-slate-500">
              {c.label}
            </div>
            <div className="mt-2 text-2xl font-semibold text-white">{c.value}</div>
          </div>
        ))}
      </div>

      <div className="card mt-5 p-0">
        <iframe
          src={`${GRAFANA_URL}/d/ai-platform/ai-platform?kiosk&theme=dark`}
          className="h-[600px] w-full rounded-xl"
          title="Grafana"
        />
      </div>
    </div>
  );
}
