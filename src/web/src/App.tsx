import { useEffect, useState } from "react";
import { ChatMessage, ConversationSummary, getConversation, listConversations, sendMessage } from "./api";
import Sidebar from "./components/Sidebar";
import Chat from "./components/Chat";

export default function App() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string>(() => crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshList = () => listConversations().then(setConversations).catch((e) => setError(String(e)));

  useEffect(() => {
    refreshList();
  }, []);

  const openConversation = async (id: string) => {
    setError(null);
    setActiveId(id);
    try {
      setMessages((await getConversation(id)).messages);
    } catch (e) {
      setError(String(e));
    }
  };

  const newConversation = () => {
    setActiveId(crypto.randomUUID());
    setMessages([]);
    setError(null);
  };

  const send = async (text: string) => {
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setSending(true);
    try {
      const res = await sendMessage(activeId, text);
      setMessages((m) => [...m, { role: "assistant", content: res.answer.answer, assumptions: res.answer.assumptions }]);
      refreshList();
    } catch (e) {
      setError(`The agent could not answer (${e}). Please try again.`);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="layout">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={openConversation}
        onNew={newConversation}
        disabled={sending}
      />
      <Chat messages={messages} sending={sending} error={error} onSend={send} />
    </div>
  );
}
