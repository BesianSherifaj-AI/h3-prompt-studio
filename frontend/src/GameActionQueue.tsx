import { useEffect, useRef, useState } from "react";
import type { GameIntent, Story } from "./storyTypes";

export type QueuedMove = { id: string; message: string; intent?: GameIntent; readyAt: number; submitted: boolean };
export type MoveQueue = { branch: string; paused: boolean; items: QueuedMove[]; error: string };
const empty = (): MoveQueue => ({ branch: "", paused: false, items: [], error: "" });
export function restoreMoveQueue(raw: string | null): MoveQueue {
  try {
    const q = JSON.parse(raw || "null");
    if (typeof q?.branch !== "string" || !Array.isArray(q.items) || q.items.length > 12) return empty();
    if (!q.items.every((i: QueuedMove) => i && typeof i.id === "string" && /^[A-Za-z0-9_-]{1,200}$/.test(i.id) && typeof i.message === "string" && i.message.length <= 4000 && Number.isFinite(i.readyAt) && typeof i.submitted === "boolean")) return empty();
    if (new Set(q.items.map((i: QueuedMove) => i.id)).size !== q.items.length) return empty();
    return { branch: q.branch, items: q.items.map((i: QueuedMove) => ({
      id: i.id, message: i.message, readyAt: i.readyAt, submitted: i.submitted,
      ...(i.intent && typeof i.intent.kind === "string" && Object.values(i.intent).every(v => v === undefined || typeof v === "string") ? { intent: i.intent } : {}),
    })), paused: true, error: q.items.length ? "Queue restored and paused. Review it before continuing." : "" };
  } catch { return empty(); }
}
export function queueDecision(queue: MoveQueue, story: Story, blocked: boolean, now: number) {
  const head = queue.items[0];
  if (!head || !head.message.trim()) return "idle";
  if (queue.branch !== story.active_branch_id) return "branch-changed";
  const turn = story.turns.find(t => t.id === head.id || t.request_id === head.id);
  if (turn?.status === "succeeded") return "complete";
  if (turn && ["failed", "cancelled", "uncertain", "inspection_failed"].includes(turn.status)) return "attention";
  if (turn || head.submitted) return "waiting";
  if (queue.paused || blocked || now < head.readyAt) return "idle";
  return "send";
}
export function queueAfterFailure(queue: MoveQueue, moveId: string, error: { message?: string; notSubmitted?: boolean }): MoveQueue {
  if (!queue.items.some(item => item.id === moveId)) return queue;
  return { ...queue, paused: true, error: String(error?.message || error),
    items: error?.notSubmitted ? queue.items.map(item => item.id === moveId ? { ...item, submitted: false } : item) : queue.items };
}

export function useGameActionQueue(story: Story | null, blocked: boolean, send: (move: QueuedMove) => Promise<unknown>) {
  const key = story ? `h3-game:move-queue:${story.id}` : "";
  const [queue, setQueue] = useState<MoveQueue>(empty), [loaded, setLoaded] = useState(""), [now, setNow] = useState(Date.now);
  const state = useRef(queue), storageKey = useRef(""), sender = useRef(send);
  sender.current = send;
  const commit = (change: (old: MoveQueue) => MoveQueue) => {
    const next = change(state.current); state.current = next;
    let persisted = true;
    if (storageKey.current) {
      try { localStorage.setItem(storageKey.current, JSON.stringify(next)); }
      catch { persisted = false; next.paused = true; next.error = "Cannot save the queue in this browser. Free some browser storage before running it."; }
    }
    setQueue(next);
    return persisted;
  };
  useEffect(() => {
    storageKey.current = key;
    let next = empty();
    try { if (key) next = restoreMoveQueue(localStorage.getItem(key)); } catch {}
    state.current = next; setQueue(next); setLoaded(key);
  }, [key]);
  useEffect(() => { if (!queue.items.length) return; const timer = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(timer); }, [queue.items.length]);
  useEffect(() => {
    if (!story || loaded !== key) return;
    const decision = queueDecision(state.current, story, blocked, now);
    if (decision === "complete") { commit(q => ({ ...q, items: q.items.slice(1), error: "" })); return; }
    if (decision === "branch-changed" && !queue.error.startsWith("This queue")) {
      commit(q => ({ ...q, paused: true, error: "This queue belongs to another story branch. Clear it before adding moves to this branch." })); return;
    }
    if (decision === "attention" && !queue.paused) {
      commit(q => ({ ...q, paused: true, error: "The current scene needs attention. Retry it, or remove it from this queue before continuing." })); return;
    }
    if (decision !== "send") return;
    const move = state.current.items[0], scope = key;
    if (!commit(q => ({ ...q, items: q.items.map((i, n) => n ? i : { ...i, submitted: true }), error: "" }))) {
      commit(q => ({ ...q, items: q.items.map(i => i.id === move.id ? { ...i, submitted: false } : i) }));
      return;
    }
    void sender.current(move).catch(error => {
      if (storageKey.current === scope) commit(q => queueAfterFailure(q, move.id, error));
      else {
        // A rejection belongs to the original queue even after another game is opened.
        // Update only that existing entry; never resurrect an entry the user removed.
        try {
          const previous = restoreMoveQueue(localStorage.getItem(scope));
          if (previous.items.some(item => item.id === move.id))
            localStorage.setItem(scope, JSON.stringify(queueAfterFailure(previous, move.id, error)));
        } catch { /* The saved request remains available for manual reconciliation. */ }
      }
    });
  }, [story, blocked, now, queue, loaded, key]);
  const enqueue = (message: string, intent?: GameIntent) => {
    if (!story || loaded !== key || !message.trim()) return false;
    if (message.trim().length > 4000) { commit(q => ({ ...q, error: "Keep each scene instruction under 4,000 characters." })); return false; }
    if (state.current.items.length >= 12) { commit(q => ({ ...q, error: "The queue holds 12 scenes. Remove one or wait for it to finish." })); return false; }
    if (state.current.items.length && state.current.branch !== story.active_branch_id) return false;
    commit(q => ({ ...q, branch: story.active_branch_id, paused: q.items.length ? q.paused : false, error: "",
      items: [...q.items, { id: crypto.randomUUID(), message: message.trim(), intent, readyAt: Date.now() + 3000, submitted: false }] }));
    return true;
  };
  return { queue: loaded === key ? queue : empty(), now, enqueue,
    edit: (id: string, message: string) => commit(q => ({ ...q, items: q.items.map(i => i.id === id && !i.submitted ? { ...i, message, intent: undefined, readyAt: Date.now() + 3000 } : i) })),
    remove: (id: string) => commit(q => ({ ...q, items: q.items.filter(i => i.id !== id), error: "" })),
    pause: () => commit(q => ({ ...q, paused: true })),
    resume: () => commit(q => ({ ...q, paused: false, error: "" })),
    clear: () => commit(q => ({ ...empty(), paused: true })),
  };
}

export function GameActionQueue({ controls, busy, onCheck }: { controls: ReturnType<typeof useGameActionQueue>; busy: boolean; onCheck: () => void }) {
  const { queue, now } = controls;
  return <section className="game-action-queue" aria-label="Scene queue">
    <div className="game-queue-heading"><strong>Scene queue · {queue.items.length}</strong>
      {queue.items.length > 0 && <><button type="button" onClick={queue.paused ? controls.resume : controls.pause}>{queue.paused ? "Run queue" : "Pause queue"}</button><button type="button" onClick={controls.clear}>Clear queue</button></>}
    </div>
    <p>Each move is a separate video. You have 3 seconds to edit it before it starts. Add your next moves while rendering; each waits for the previous ending.</p>
    {!!queue.error && <p role="alert">{queue.error}</p>}
    {queue.paused && queue.items[0]?.submitted && <button type="button" onClick={onCheck}>Check saved request</button>}
    <ol>{queue.items.map((item, index) => <li key={item.id}>
      <label><span>{index + 1}. {item.submitted ? "Submitted · waiting for its result" : queue.paused ? "Paused" : busy || index > 0 ? "Waiting in order" : `Starts in ${Math.max(0, Math.ceil((item.readyAt - now) / 1000))}s`}</span>
        <textarea aria-label={`Queued scene ${index + 1}`} value={item.message} maxLength={4000} readOnly={item.submitted} onChange={e => controls.edit(item.id, e.target.value)} rows={2}/></label>
      <button type="button" aria-label={`Remove queued scene ${index + 1}`} onClick={() => controls.remove(item.id)}>×</button>
    </li>)}</ol>
    {queue.items.some(i => i.submitted) && <small>Removing a submitted scene only removes this queue entry. Use Stop on the active turn to cancel its rendering.</small>}
  </section>;
}
