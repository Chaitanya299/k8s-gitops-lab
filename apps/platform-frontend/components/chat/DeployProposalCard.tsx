"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { DeploySpec, reportDeployResult } from "@/lib/chat";

type DeployResult = {
  service: string;
  committed: boolean;
  commit?: string;
  message: string;
};

// The one place a chat turn can reach the cluster — and it goes through the
// exact same POST /api/deploy the Deploy form uses. Nothing here bypasses it.
export default function DeployProposalCard({
  spec,
  conversationId,
}: {
  spec: DeploySpec;
  conversationId: string | null;
}) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [detail, setDetail] = useState<string>("");

  async function deploy() {
    setState("busy");
    try {
      const result = await api<DeployResult>("/deploy", {
        method: "POST",
        body: JSON.stringify(spec),
      });
      setState("done");
      setDetail(
        result.committed
          ? `Committed ${result.commit}. ArgoCD will sync within ~30s.`
          : result.message,
      );
      if (conversationId) {
        // Close the learning loop: tell the assistant what actually happened.
        void reportDeployResult(conversationId, result);
      }
    } catch (err) {
      setState("error");
      setDetail((err as Error).message);
    }
  }

  return (
    <div className="card my-2 border-accent/40 bg-accent/5 p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-accent">
        Proposed deployment
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <Row k="Service" v={spec.service} />
        <Row k="Replicas" v={String(spec.replicas)} />
        <Row k="CPU" v={spec.cpu} />
        <Row k="Memory" v={spec.memory} />
        <Row k="Namespace" v={spec.namespace} />
        {spec.model && <Row k="Model" v={spec.model} />}
      </dl>

      {state === "idle" && (
        <button className="btn mt-3" onClick={deploy}>
          Deploy
        </button>
      )}
      {state === "busy" && (
        <div className="mt-3 text-sm text-slate-400">Committing…</div>
      )}
      {state === "done" && (
        <div className="mt-3 text-sm text-emerald-300">{detail}</div>
      )}
      {state === "error" && (
        <div className="mt-3 text-sm text-red-300">{detail}</div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt className="text-slate-500">{k}</dt>
      <dd className="font-mono text-slate-200">{v}</dd>
    </>
  );
}
