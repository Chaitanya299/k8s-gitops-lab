"use client";

import { useState } from "react";
import { api } from "@/lib/api";

// Manual path for recording a solved issue. The assistant also offers to save
// issues conversationally; this is the explicit form for when a user just wants
// to file one.
export default function IssueReportForm({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ title: "", description: "", resolution: "" });
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const set = (k: string, v: string) => setForm({ ...form, [k]: v });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setState("busy");
    try {
      await api("/issues", { method: "POST", body: JSON.stringify(form) });
      setState("done");
      setTimeout(onClose, 900);
    } catch {
      setState("idle");
    }
  }

  if (state === "done") {
    return (
      <div className="card m-3 border-emerald-900/50 bg-emerald-950/20 text-sm text-emerald-300">
        Saved. Future sessions will find it.
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="card m-3 flex flex-col gap-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        Record a solved issue
      </div>
      <input
        className="input"
        placeholder="Short title"
        required
        value={form.title}
        onChange={(e) => set("title", e.target.value)}
      />
      <textarea
        className="input min-h-[60px]"
        placeholder="What happened?"
        required
        value={form.description}
        onChange={(e) => set("description", e.target.value)}
      />
      <textarea
        className="input min-h-[60px]"
        placeholder="What fixed it?"
        required
        value={form.resolution}
        onChange={(e) => set("resolution", e.target.value)}
      />
      <div className="flex gap-2">
        <button className="btn" disabled={state === "busy"}>
          {state === "busy" ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          className="text-sm text-slate-400 hover:text-slate-200"
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
