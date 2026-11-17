import { useEffect, useState } from "react";
import { listAlerts, ScoreChange } from "../api";

// changed_at of the newest alert this browser has seen; anything newer colors the bell
const SEEN_KEY = "alerts-last-seen";

function readSeen(): string {
  try {
    return localStorage.getItem(SEEN_KEY) ?? "";
  } catch {
    return ""; // storage blocked - every load counts as unseen
  }
}

function describe(c: ScoreChange): string {
  const now = c.new_score?.score;
  if (now === undefined) return "";
  if (!c.last_score) return `new score ${now.toFixed(1)}`;
  const diff = now - c.last_score.score;
  return `${c.last_score.score.toFixed(1)} → ${now.toFixed(1)} (${diff > 0 ? "+" : ""}${diff.toFixed(1)})`;
}

export default function AlertsBell() {
  const [alerts, setAlerts] = useState<ScoreChange[]>([]);
  const [seen, setSeen] = useState(readSeen);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    listAlerts().then(setAlerts).catch(() => setAlerts([]));
  }, []);

  const newest = alerts[0]?.changed_at ?? "";
  const unseen = newest > seen; // ISO timestamps compare chronologically

  const toggle = () => {
    if (!open && unseen) {
      setSeen(newest);
      try {
        localStorage.setItem(SEEN_KEY, newest);
      } catch {
        /* not persisted - fine */
      }
    }
    setOpen((o) => !o);
  };

  return (
    <div className="bell-wrap">
      <button
        className={`icon-button bell ${unseen ? "unseen" : ""}`}
        onClick={toggle}
        title="Score changes"
        aria-label={unseen ? "Score changes (new)" : "Score changes"}
        aria-expanded={open}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill={unseen ? "currentColor" : "none"}
             stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
      </button>
      {open && (
        <div className="alerts-panel">
          <h3>Score changes</h3>
          {alerts.length === 0 && <p className="muted">No score changes yet</p>}
          <ul>
            {alerts.map((c, i) => (
              <li key={i}>
                <div className="alert-head">
                  <strong>{c.hub_id}</strong>
                  <span>{describe(c)}</span>
                </div>
                <div className="alert-reasons" title={c.reasons}>{c.reasons}</div>
                <div className="date">{new Date(c.changed_at).toLocaleString()}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
