"use client";

import { useEffect, useRef, useState } from "react";
import { ChatStatus, getChatStatus, streamChat } from "@/lib/chat";
import IssueReportForm from "./IssueReportForm";
import MessageList, { ChatMessage } from "./MessageList";
import { ToolState } from "./ToolActivity";

// Mounted once at the root so it survives page navigation. Fixed position, so
// it overlays the existing layout without disturbing the flex column.
export default function ChatPanel() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [showIssue, setShowIssue] = useState(false);
  const conversationId = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatStatus().then(setStatus).catch(() => setStatus({ enabled: false }));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  // Mutate the last assistant message in place as frames arrive.
  function patchAssistant(fn: (m: ChatMessage) => void) {
    setMessages((prev) => {
      const next = [...prev];
      const last = { ...next[next.length - 1] };
      fn(last);
      next[next.length - 1] = last;
      return next;
    });
  }

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setStreaming(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", text },
      { role: "assistant", text: "", tools: [] },
    ]);

    try {
      for await (const frame of streamChat(text, conversationId.current)) {
        switch (frame.type) {
          case "start":
            conversationId.current = frame.conversation_id;
            break;
          case "token":
            patchAssistant((m) => {
              m.text += frame.text;
            });
            break;
          case "tool_start":
            patchAssistant((m) => {
              m.tools = [
                ...(m.tools ?? []),
                { id: frame.id, tool: frame.tool, done: false, error: false } as ToolState,
              ];
            });
            break;
          case "tool_end":
            patchAssistant((m) => {
              m.tools = (m.tools ?? []).map((t) =>
                t.id === frame.id ? { ...t, done: true, error: frame.is_error } : t,
              );
            });
            break;
          case "proposal":
            patchAssistant((m) => {
              m.proposals = [...(m.proposals ?? []), frame.spec];
            });
            break;
          case "error":
            patchAssistant((m) => {
              m.text = m.text ? `${m.text}\n\n${frame.message}` : frame.message;
              m.error = true;
            });
            break;
          // "usage" and "done" need no UI change.
        }
      }
    } finally {
      setStreaming(false);
    }
  }

  function reset() {
    conversationId.current = null;
    setMessages([]);
    setShowIssue(false);
  }

  return (
    <>
      {!open && (
        <button
          aria-label="Open deployment assistant"
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-lg text-white shadow-lg transition hover:bg-accent-hover"
        >
          ✦
        </button>
      )}

      <div
        className={`chat-panel ${open ? "translate-x-0" : "translate-x-full"}`}
        aria-hidden={!open}
      >
        <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-white">Deploy assistant</div>
            {status && (
              <div className="text-xs text-slate-500">
                {status.enabled ? `via ${status.provider}` : "not configured"}
              </div>
            )}
          </div>
          <div className="flex items-center gap-3 text-slate-400">
            <button title="New conversation" onClick={reset} className="hover:text-slate-200">
              ↺
            </button>
            <button title="Report an issue" onClick={() => setShowIssue(true)} className="hover:text-slate-200">
              ⚑
            </button>
            <button title="Close" onClick={() => setOpen(false)} className="hover:text-slate-200">
              ✕
            </button>
          </div>
        </header>

        {status && !status.enabled && (
          <div className="m-3 rounded-lg border border-amber-900/50 bg-amber-950/20 p-3 text-xs text-amber-200">
            The assistant is not configured. Set <code>LLM_PROVIDER</code> on the
            platform backend (<code>claude</code> or <code>ollama</code>) and redeploy.
            {status.reason && <div className="mt-1 text-amber-300/70">{status.reason}</div>}
          </div>
        )}

        {showIssue && <IssueReportForm onClose={() => setShowIssue(false)} />}

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3">
          {messages.length === 0 ? (
            <div className="mt-8 px-2 text-center text-sm text-slate-500">
              Ask what&apos;s running, why a pod is failing, or describe a service
              you want to deploy.
            </div>
          ) : (
            <MessageList messages={messages} conversationId={conversationId.current} />
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
          className="border-t border-slate-800 p-3"
        >
          <div className="flex items-end gap-2">
            <textarea
              className="input min-h-[42px] resize-none"
              rows={1}
              placeholder={status?.enabled ? "Ask the assistant…" : "Assistant unavailable"}
              disabled={!status?.enabled || streaming}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button className="btn" disabled={!status?.enabled || streaming || !input.trim()}>
              {streaming ? "…" : "Send"}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
