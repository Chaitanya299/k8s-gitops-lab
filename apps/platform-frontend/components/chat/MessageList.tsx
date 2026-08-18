"use client";

import { DeploySpec } from "@/lib/chat";
import DeployProposalCard from "./DeployProposalCard";
import ToolActivity, { ToolState } from "./ToolActivity";

export type ChatMessage = {
  role: "user" | "assistant" | "system";
  text: string;
  tools?: ToolState[];
  proposals?: DeploySpec[];
  error?: boolean;
};

export default function MessageList({
  messages,
  conversationId,
}: {
  messages: ChatMessage[];
  conversationId: string | null;
}) {
  return (
    <div className="flex flex-col gap-3">
      {messages.map((m, i) => (
        <div key={i}>
          {m.role === "user" ? (
            <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-accent/20 px-3 py-2 text-sm text-slate-100">
              {m.text}
            </div>
          ) : m.role === "system" ? (
            <div className="mx-auto text-center text-xs text-slate-500">{m.text}</div>
          ) : (
            <div className="max-w-[92%]">
              {m.tools && m.tools.length > 0 && <ToolActivity tools={m.tools} />}
              {m.text && (
                <div
                  className={`whitespace-pre-wrap rounded-2xl rounded-bl-sm px-3 py-2 text-sm ${
                    m.error
                      ? "bg-red-950/40 text-red-300"
                      : "bg-slate-800/70 text-slate-200"
                  }`}
                >
                  {m.text}
                </div>
              )}
              {m.proposals?.map((spec, j) => (
                <DeployProposalCard key={j} spec={spec} conversationId={conversationId} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
