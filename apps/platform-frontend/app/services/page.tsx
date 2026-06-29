"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Pod = { name: string; phase: string; node: string | null };
type Service = {
  name: string;
  namespace: string;
  desired: number;
  ready: number;
  available: number;
  restarts: number;
  age: string;
  pods: Pod[];
};

export default function ServicesPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const data = await api<Service[]>("/services");
        if (alive) {
          setServices(data);
          setError(null);
        }
      } catch (e: any) {
        if (alive) setError(e.message);
      } finally {
        if (alive) setLoaded(true);
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-white">Running services</h1>
      <p className="mt-1 text-sm text-slate-400">
        Live from the Kubernetes API · auto-refresh 5s
      </p>

      {error && (
        <div className="card mt-5 border-red-900/60 bg-red-950/30 text-sm text-red-300">
          {error}
        </div>
      )}

      {loaded && services.length === 0 && !error && (
        <div className="card mt-5 text-sm text-slate-400">
          Nothing deployed yet. Head to <a href="/" className="text-accent">Deploy</a>.
        </div>
      )}

      <div className="mt-5 grid gap-4">
        {services.map((s) => {
          const healthy = s.ready === s.desired && s.desired > 0;
          return (
            <div key={s.name} className="card">
              <div className="flex items-center justify-between">
                <div className="font-semibold text-white">{s.name}</div>
                <span
                  className={`pill ${
                    healthy
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-amber-500/15 text-amber-300"
                  }`}
                >
                  {s.ready}/{s.desired} ready
                </span>
              </div>
              <div className="mt-3 flex flex-wrap gap-x-8 gap-y-1 text-sm text-slate-400">
                <span>Namespace: <span className="text-slate-200">{s.namespace}</span></span>
                <span>Available: <span className="text-slate-200">{s.available}</span></span>
                <span>Restarts: <span className="text-slate-200">{s.restarts}</span></span>
                <span>Age: <span className="text-slate-200">{s.age}</span></span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {s.pods.map((p) => (
                  <span
                    key={p.name}
                    className={`pill font-mono ${
                      p.phase === "Running"
                        ? "bg-slate-800 text-slate-300"
                        : "bg-amber-500/15 text-amber-300"
                    }`}
                    title={p.node ?? ""}
                  >
                    {p.phase === "Running" ? "●" : "○"} {p.name}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
