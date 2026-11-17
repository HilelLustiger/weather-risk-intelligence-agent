// Types mirror the backend models (src/backend/models.py).
export type ChatMessage = { role: "user" | "assistant"; content: string; assumptions?: string[] };
export type AgentAnswer = {
  answer: string;
  hubs: { hub_id: string; score: number | null }[];
  assumptions: string[];
};
export type ConversationSummary = { id: string; title: string; updated_at: string | null };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const listConversations = () => request<ConversationSummary[]>("/conversations");

export const getConversation = (id: string) =>
  request<{ id: string; messages: ChatMessage[] }>(`/conversations/${encodeURIComponent(id)}`);

export const sendMessage = (sessionId: string, message: string) =>
  request<{ session_id: string; answer: AgentAnswer }>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
