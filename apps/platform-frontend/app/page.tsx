"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";

const MODELS = ["echo", "gemma", "qwen", "mistral"];

type DeployResult = {
  service: string;
  committed: boolean;
  commit?: string;
  message: string;
};

export default function DeployPage() {
  const [knownServices, setKnownServices] = useState<string[]>([]);

  useEffect(() => {
    api<{ name: string }[]>("/services")
      .then((list) => setKnownServices(list.map((s) => s.name)))
      .catch(() => {});
  }, []);

  const [form, setForm] = useState({
    service: "sample-ai-service",
    replicas: 3,
    cpu: "200m",
    memory: "256Mi",
    namespace: "ai-services",
    model: "echo",
  });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DeployResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const set = (k: string, v: string | number) => setForm({ ...form, [k]: v });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api<DeployResult>("/deploy", {
        method: "POST",
        body: JSON.stringify({ ...form, replicas: Number(form.replicas) }),
      }));
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white">Deploy an AI service</h1>
      <p className="mt-1 text-sm text-slate-400">
        Submitting commits new Helm values to Git. ArgoCD reconciles the cluster —
        no kubectl, no manual steps.
      </p>

      <form onSubmit={submit} className="card mt-6 grid grid-cols-2 gap-5">
        <div className="col-span-2">
          <label className="label">Service</label>
          <input
            className="input"
            list="known-services"
            value={form.service}
            onChange={(e) => set("service", e.target.value)}
            placeholder="service-name (pick existing or type new)"
          />
          <datalist id="known-services">
            {knownServices.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
        </div>

        <div>
          <label className="label">Model</label>
          <select
            className="input"
            value={form.model}
            onChange={(e) => set("model", e.target.value)}
          >
            {MODELS.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Replicas</label>
          <input
            type="number"
            min={1}
            max={20}
            className="input"
            value={form.replicas}
            onChange={(e) => set("replicas", e.target.value)}
          />
        </div>

        <div>
          <label className="label">CPU</label>
          <input
            className="input"
            value={form.cpu}
            onChange={(e) => set("cpu", e.target.value)}
            placeholder="200m"
          />
        </div>
        <div>
          <label className="label">Memory</label>
          <input
            className="input"
            value={form.memory}
            onChange={(e) => set("memory", e.target.value)}
            placeholder="256Mi"
          />
        </div>

        <div className="col-span-2">
          <label className="label">Namespace</label>
          <input
            className="input"
            value={form.namespace}
            onChange={(e) => set("namespace", e.target.value)}
          />
        </div>

        <div className="col-span-2 flex items-center gap-4">
          <button className="btn" disabled={busy}>
            {busy ? "Committing…" : "Deploy"}
          </button>
          <span className="text-xs text-slate-500">
            Writes gitops/environments/dev/{form.service}.yaml → git push → ArgoCD sync
          </span>
        </div>
      </form>

      {error && (
        <div className="card mt-5 border-red-900/60 bg-red-950/30 text-sm text-red-300">
          {error}
        </div>
      )}

      {result && (
        <div className="card mt-5 border-emerald-900/50 bg-emerald-950/20">
          <div className="text-sm font-semibold text-emerald-300">
            {result.committed ? "Deployment committed" : "No change"}
          </div>
          <div className="mt-2 grid gap-1 text-sm text-slate-300">
            <div>
              <span className="text-slate-500">Service:</span> {result.service}
            </div>
            {result.commit && (
              <div>
                <span className="text-slate-500">Commit:</span>{" "}
                <code className="font-mono text-accent">{result.commit}</code>
              </div>
            )}
            <div>
              <span className="text-slate-500">Message:</span> {result.message}
            </div>
          </div>
          <a
            href="/services"
            className="mt-3 inline-block text-xs text-accent hover:underline"
          >
            → Watch it roll out in Running Services
          </a>
        </div>
      )}
    </div>
  );
}
