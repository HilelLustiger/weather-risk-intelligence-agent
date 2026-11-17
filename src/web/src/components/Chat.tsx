import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ChatMessage } from "../api";

type Props = {
  messages: ChatMessage[];
  sending: boolean;
  error: string | null;
  onSend: (text: string) => void;
};

export default function Chat({ messages, sending, error, onSend }: Props) {
  const [input, setInput] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // braces matter: scrollIntoView returns a Promise in newer browsers, and anything an
    // effect returns is treated by React as a cleanup function
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    onSend(text);
  };

  return (
    <main className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <p className="muted empty">Ask about weather risk at the hubs, e.g. "Which 3 hubs are most at risk?"</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`message ${m.role}`} dir="auto">
            {m.role === "assistant" ? <ReactMarkdown>{m.content}</ReactMarkdown> : m.content}
          </div>
        ))}
        {sending && <div className="message assistant muted">Analyzing…</div>}
        {error && <div className="error">{error}</div>}
        <div ref={bottom} />
      </div>
      <form className="composer" onSubmit={submit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a question…"
          dir="auto"
          disabled={sending}
          autoFocus
        />
        <button type="submit" disabled={sending || !input.trim()}>
          Send
        </button>
      </form>
    </main>
  );
}
