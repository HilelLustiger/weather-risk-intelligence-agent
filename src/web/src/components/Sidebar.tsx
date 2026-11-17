import { useState } from "react";
import { ConversationSummary } from "../api";
import AlertsBell from "./AlertsBell";

type Props = {
  conversations: ConversationSummary[];
  activeId: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  disabled: boolean;
};

const STORAGE_KEY = "sidebar-collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false; // storage blocked (private mode etc.) - just start expanded
  }
}

export default function Sidebar({ conversations, activeId, onSelect, onNew, disabled }: Props) {
  const [collapsed, setCollapsed] = useState(readCollapsed);

  const toggle = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(STORAGE_KEY, c ? "0" : "1");
      } catch {
        /* not persisted - fine */
      }
      return !c;
    });
  };

  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-top">
        <button
          className="icon-button"
          onClick={toggle}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
        >
          {collapsed ? "»" : "«"}
        </button>
        <button className="new-chat" onClick={onNew} disabled={disabled} title="New conversation">
          {collapsed ? "+" : "+ New conversation"}
        </button>
        <AlertsBell />
      </div>
      {!collapsed && (
        <nav>
          {conversations.length === 0 && <p className="muted">No conversations yet</p>}
          {conversations.map((c) => (
            <button
              key={c.id}
              className={`conversation ${c.id === activeId ? "active" : ""}`}
              onClick={() => onSelect(c.id)}
              disabled={disabled}
              dir="auto"
            >
              <span className="title">{c.title}</span>
              {c.updated_at && <span className="date">{new Date(c.updated_at).toLocaleString()}</span>}
            </button>
          ))}
        </nav>
      )}
    </aside>
  );
}
