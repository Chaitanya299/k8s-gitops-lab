// SSE client for the assistant. Raw fetch + ReadableStream — no dependency, and
// EventSource can't POST a body. The one subtlety is the line buffer: a network
// chunk boundary can fall mid-frame, so the trailing partial line is carried
// forward to the next read instead of being parsed as truncated JSON.

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export type DeploySpec = {
  service: string;
  replicas: number;
  cpu: string;
  memory: string;
  namespace: string;
  model?: string;
};

export type ChatFrame =
  | { type: "start"; conversation_id: string; request_id: string }
  | { type: "token"; text: string }
  | { type: "tool_start"; id: string; tool: string; args: Record<string, unknown> }
  | { type: "tool_end"; id: string; tool: string; is_error: boolean }
  | { type: "proposal"; spec: DeploySpec }
  | { type: "usage"; spent: number; limit: number; cache_read: number }
  | { type: "done"; reason: string }
  | { type: "error"; message: string };

export type ChatStatus = { enabled: boolean; provider?: string; reason?: string };

export async function getChatStatus(): Promise<ChatStatus> {
  const res = await fetch(`${BASE}/api/chat/status`, { cache: "no-store" });
  if (res.status === 503) return { enabled: false };
  if (!res.ok) return { enabled: false, reason: `${res.status}` };
  return res.json();
}

// Streams one turn. Yields each frame as it arrives. Retries once on a
// mid-stream network drop — a dropped SSE connection is common behind proxies
// and one clean retry beats surfacing a scary error for a transient blip.
export async function* streamChat(
  message: string,
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatFrame> {
  let attempt = 0;
  while (true) {
    try {
      yield* rawStream(message, conversationId, signal);
      return;
    } catch (err) {
      if (attempt >= 1 || signal?.aborted) {
        yield { type: "error", message: (err as Error).message || "connection lost" };
        return;
      }
      attempt += 1;
    }
  }
}

async function* rawStream(
  message: string,
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatFrame> {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });

  if (res.status === 503) {
    const detail = await res.json().catch(() => ({}));
    yield { type: "error", message: detail.detail || "Chat is not configured." };
    return;
  }
  if (res.status === 429) {
    yield { type: "error", message: "Too many requests — wait a moment." };
    return;
  }
  if (!res.ok || !res.body) {
    throw new Error(`chat failed: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            yield JSON.parse(line.slice(6)) as ChatFrame;
          } catch {
            // A frame that fails to parse is dropped, not fatal.
          }
        }
      }
    }
  }
}

export async function reportDeployResult(
  conversationId: string,
  result: { service: string; committed: boolean; commit?: string; message?: string },
): Promise<void> {
  await fetch(`${BASE}/api/chat/deploy-result`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, ...result }),
  }).catch(() => {});
}
