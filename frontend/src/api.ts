import type { ChatResult, SourcePreview, StreamEvent } from "./types";

const API_BASE = import.meta.env.VITE_API_URL || "/api";

function headers(): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-User-Id": "poc-user",
  };
}

async function apiError(response: Response): Promise<Error> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string };
    return new Error(body.detail || `API error ${response.status}`);
  } catch {
    return new Error(text || `API error ${response.status}`);
  }
}

export async function streamChat(
  query: string,
  onEvent: (event: StreamEvent) => void,
): Promise<ChatResult> {
  const response = await fetch(`${API_BASE}/v1/chat/stream`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ query }),
  });
  if (!response.ok || !response.body) {
    throw await apiError(response);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: ChatResult | undefined;

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const eventName = block
        .split("\n")
        .find((line) => line.startsWith("event:"))
        ?.slice(6)
        .trim();
      const dataText = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (eventName && dataText) {
        const event = { type: eventName, data: JSON.parse(dataText) } as StreamEvent;
        onEvent(event);
        if (event.type === "result") finalResult = event.data;
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }

  if (!finalResult) throw new Error("串流已結束，但沒有收到最終結果");
  return finalResult;
}

export async function fetchSource(
  sourceId: string,
  coCode: string,
): Promise<SourcePreview> {
  const params = new URLSearchParams({ co_code: coCode });
  const response = await fetch(
    `${API_BASE}/v1/sources/${encodeURIComponent(sourceId)}?${params}`,
    { headers: headers() },
  );
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<SourcePreview>;
}
