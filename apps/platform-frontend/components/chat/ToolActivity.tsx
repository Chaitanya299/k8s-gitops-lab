// Renders the model's tool calls as human-readable activity ("Reading pod
// logs…") so a multi-tool turn shows its work instead of a blank spinner.

const LABELS: Record<string, string> = {
  search_platform_knowledge: "Searching platform memory",
  get_running_services: "Reading running services",
  get_pod_logs: "Reading pod logs",
  get_service_metrics: "Reading metrics",
  get_deployment_history: "Reading deployment history",
  get_sync_status: "Checking ArgoCD sync",
  propose_deployment: "Preparing a deploy proposal",
  save_learned_issue: "Saving this issue",
};

export type ToolState = { id: string; tool: string; done: boolean; error: boolean };

export default function ToolActivity({ tools }: { tools: ToolState[] }) {
  if (tools.length === 0) return null;
  return (
    <div className="my-2 flex flex-col gap-1">
      {tools.map((t) => (
        <div key={t.id} className="flex items-center gap-2 text-xs text-slate-400">
          <span
            className={
              t.error
                ? "text-red-400"
                : t.done
                ? "text-emerald-400"
                : "animate-pulse text-accent"
            }
          >
            {t.error ? "✕" : t.done ? "✓" : "◍"}
          </span>
          <span>
            {LABELS[t.tool] ?? t.tool}
            {!t.done && "…"}
          </span>
        </div>
      ))}
    </div>
  );
}
