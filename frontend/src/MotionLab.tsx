import { useEffect, useId, useRef, useState } from "react";
import { ArrowRight, FlaskConical, Pause, RefreshCw, Square, X } from "lucide-react";
import { api } from "./api";
import { retime, type Project } from "./model";
import "./MotionLab.css";
import { PIXEL_STYLE } from "./storyTypes";

type Props = { project: Project; onClose: () => void };
type Recipe = { id: string; name: string; instruction: string; experimental?: boolean; note?: string };
type Run = { id: string; status: string; stage?: string; error?: string; elapsed_seconds?: number; server_execution_seconds?: number; scene_video_url?: string; video_url?: string; frames?: number; width?: number; height?: number; steps?: number; new_seconds?: number };
type Item = { request_id: string; recipe_id: string; recipe_name: string; seed: number; status: string; prompt: string; ratings: Record<string, string>; notes: string; run?: Run };
type Comparison = { id: string; paused: boolean; items: Item[]; created_at: number };
type Saved = { id: string; label: string };
const activeStatuses = new Set(["preparing", "queued", "running", "uncertain", "cancelling"]);
const ratingFields = [["direction", "Movement"], ["identity", "Identity"], ["continuity", "Continuity"]];
const ratingOptions = [["uncertain", "Not sure yet"], ["pass", "Works"], ["fail", "Does not work"], ["not_applicable", "Not applicable"]];

export function motionSeeds(text: string): number[] {
  const parts = text.trim().split(/[\s,]+/).filter(Boolean);
  if (!parts.length || parts.length > 3 || parts.some(value => !/^\d+$/.test(value))) throw new Error("Enter one to three whole-number seeds, separated by commas.");
  const seeds = parts.map(Number);
  if (seeds.some(value => !Number.isSafeInteger(value)) || new Set(seeds).size !== seeds.length) throw new Error("Use distinct seeds from 0 to 9007199254740991.");
  return seeds;
}

export function motionPreviewProject(project: Project): Project {
  const snapshot = structuredClone(project);
  snapshot.duration = 3;
  snapshot.shots = retime(snapshot.shots, 3);
  snapshot.comfy_render = { ...snapshot.comfy_render, experimental_preview: true, resolution: "0.2", duration_basis: "new_footage" };
  // This is a separate paired scene test, not an advance of the story branch.
  // A previous clip may have a different immutable geometry than this preview.
  delete snapshot.comfy_render.continuation_source;
  delete snapshot.comfy_render.continuation_overlap_frames;
  snapshot.style.notes = PIXEL_STYLE;
  return snapshot;
}

function stored(key: string): { active?: string; saved: Saved[] } {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return { active: typeof value.active === "string" ? value.active : undefined,
      saved: Array.isArray(value.saved) ? value.saved.filter((row: Saved) => typeof row?.id === "string" && typeof row?.label === "string").slice(0, 20) : [] };
  } catch { return { saved: [] }; }
}
function timeLabel(seconds?: number) { return typeof seconds === "number" && Number.isFinite(seconds) ? `${seconds.toFixed(1)}s` : "—"; }
function message(error: unknown) { return error instanceof Error ? error.message : "The comparison could not be updated."; }

export default function MotionLab({ project, onClose }: Props) {
  const titleId = useId(), dialog = useRef<HTMLDialogElement>(null);
  const key = `h3studio.motion-lab.v1.${project.id}`;
  const [source] = useState(() => motionPreviewProject(project));
  const initial = useRef(stored(key));
  const [saved, setSaved] = useState<Saved[]>(initial.current.saved);
  const [selected, setSelected] = useState(initial.current.active || "");
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [left, setLeft] = useState("slow"), [right, setRight] = useState("fast");
  const [seedText, setSeedText] = useState(String(Number.isSafeInteger(project.comfy_render?.seed) ? project.comfy_render.seed : 1));
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const request = useRef<any>(null), live = useRef(true), loadSequence = useRef(0), mutating = useRef(false);

  useEffect(() => {
    live.current = true;
    const previous = document.activeElement as HTMLElement | null;
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    return () => { live.current = false; previous?.focus(); };
  }, []);
  useEffect(() => {
    let current = true;
    api("/motion-lab/recipes").then(value => { if (current) setRecipes(Array.isArray(value) ? value : value.recipes || []); }).catch(e => { if (current) setError(message(e)); });
    return () => { current = false; };
  }, []);

  const remember = (id: string, rows = saved) => {
    try { localStorage.setItem(key, JSON.stringify({ active: id || undefined, saved: rows })); } catch { /* Server comparisons remain durable if browser storage is full. */ }
  };
  const accept = (value: Comparison) => {
    if (!live.current) return;
    setComparison(value); setSelected(value.id);
    setSaved(previous => {
      const label = value.items.map(row => row.recipe_name).filter((name, index, all) => all.indexOf(name) === index).join(" / ");
      const next = [{ id: value.id, label: `${label} · ${new Date(value.created_at * 1000).toLocaleString()}` }, ...previous.filter(row => row.id !== value.id)].slice(0, 20);
      remember(value.id, next); return next;
    });
  };

  const refresh = async (id = selected) => {
    if (!id) return;
    const sequence = ++loadSequence.current;
    try {
      const value = await api(`/motion-lab/${id}`);
      if (live.current && sequence === loadSequence.current) { accept(value); setError(""); }
    } catch (e) { if (live.current && sequence === loadSequence.current) setError(message(e)); }
  };
  useEffect(() => { if (selected) void refresh(selected); }, [selected]);
  const running = comparison?.items.find(item => activeStatuses.has(item.run?.status || item.status));
  useEffect(() => {
    if (!comparison || !running) return;
    let waiting = false;
    const timer = window.setInterval(async () => {
      if (waiting || mutating.current) return;
      waiting = true;
      try { await refresh(comparison.id); } finally { waiting = false; }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [comparison?.id, !!running]);

  const perform = async (operation: () => Promise<any>) => {
    if (mutating.current) return;
    mutating.current = true; loadSequence.current += 1;
    setBusy(true); setError("");
    try { const result = await operation(); loadSequence.current += 1; if (live.current) accept(result); }
    catch (e) { if (live.current) setError(message(e)); }
    finally { mutating.current = false; if (live.current) setBusy(false); }
  };
  const create = () => perform(async () => {
    if (!request.current) {
      if (left === right) throw new Error("Choose two different movement recipes.");
      request.current = { request_id: crypto.randomUUID(), project: source, settings: {}, recipe_ids: [left, right], seeds: motionSeeds(seedText) };
      remember(request.current.request_id);
    }
    return api("/motion-lab", request.current);
  });
  const newComparison = () => {
    loadSequence.current += 1;
    request.current = null; setComparison(null); setSelected(""); setError(""); remember("");
  };
  const rate = (item: Item, ratings: Record<string, string>, notes = item.notes) => perform(() => api(`/motion-lab/${comparison!.id}/rating`, { request_id: item.request_id, ratings, notes }));
  const failed = comparison?.items.find(item => ["failed", "cancelled"].includes(item.run?.status || item.status));
  const finished = comparison?.items.filter(item => item.run?.status === "succeeded").length || 0;
  const allDone = !!comparison && finished === comparison.items.length;
  const selectedRecipe = (id: string) => recipes.find(recipe => recipe.id === id);

  return <dialog className="motion-lab" ref={dialog} aria-labelledby={titleId} onCancel={e => { e.preventDefault(); onClose(); }}>
    <header className="motion-lab-header"><div><p><FlaskConical size={18} aria-hidden="true" /> Motion Lab</p><h2 id={titleId}>See which direction works</h2></div><button type="button" className="motion-lab-close" aria-label="Close Motion Lab" onClick={onClose}><X size={24} /></button></header>
    <div className="motion-lab-body">
      <p>Compare two instructions with the same source, references and seeds. Each clip is a separate test; your story stays at its current ending.</p>
      <div className="motion-lab-summary"><strong>Experimental pixel preview</strong><span>≈0.2 MP · 3 seconds · {source.comfy_render?.steps || (source.mode === "ref2va" ? 8 : 4)} steps · {source.aspect_ratio}</span></div>
      <p className="motion-lab-help">Copies your project and retimes its shots for this preview. Exact dialogue is retained and may need shortening before a three-second test. Distance and speed wording is a request, not a calibrated measurement.</p>
      {saved.length > 0 && <label className="motion-lab-history">Saved comparisons<select aria-label="Saved motion comparisons" value={selected} disabled={busy} onChange={e => { loadSequence.current += 1; request.current = null; setComparison(null); setSelected(e.target.value); remember(e.target.value); }}><option value="">New comparison</option>{saved.map(row => <option value={row.id} key={row.id}>{row.label}</option>)}</select></label>}
      {!comparison && <section className="motion-lab-setup" aria-label="Set up paired comparison">
        <div className="motion-lab-pair">{[[left, setLeft, "First instruction"], [right, setRight, "Second instruction"]].map(([value, update, label]) => <label key={String(label)}>{String(label)}<select value={value as string} disabled={busy || !!request.current} onChange={e => (update as (value: string) => void)(e.target.value)}>{recipes.map(recipe => <option key={recipe.id} value={recipe.id}>{recipe.name}</option>)}</select><span>{selectedRecipe(value as string)?.instruction}</span></label>)}</div>
        <label>Paired seed(s)<input value={seedText} inputMode="numeric" disabled={busy || !!request.current} onChange={e => setSeedText(e.target.value)} aria-describedby={`${titleId}-seeds`} /><span id={`${titleId}-seeds`} className="motion-lab-help">One to three comma-separated seeds. Both instructions use each seed once.</span></label>
        <div className="motion-lab-actions"><button type="button" className="motion-lab-primary" disabled={busy || !recipes.length} onClick={create}>{request.current ? "Recover saved comparison" : "Create comparison"}</button><span>This saves the test. It does not render.</span>{request.current && <button type="button" disabled={busy} onClick={newComparison}>Start a different comparison</button>}</div>
      </section>}
      {comparison && <>
        <div className="motion-lab-actions motion-lab-sticky"><strong>{finished} / {comparison.items.length} clips ready</strong><button type="button" className="motion-lab-primary" disabled={busy || !!running || comparison.paused || allDone || !!failed} onClick={() => perform(() => api(`/motion-lab/${comparison.id}/advance`, {}))}><ArrowRight size={18} /> Render next clip</button><button type="button" disabled={busy} onClick={() => perform(() => api(`/motion-lab/${comparison.id}/pause`, { paused: !comparison.paused }))}><Pause size={17} /> {comparison.paused ? "Resume comparison" : "Pause comparison"}</button><button type="button" disabled={busy} onClick={() => refresh()}><RefreshCw size={17} /> Refresh</button>{running?.run && <button type="button" disabled={busy} onClick={() => perform(async () => { await api(`/video/runs/${running.run!.id}/cancel`, {}); return api(`/motion-lab/${comparison.id}`); })}><Square size={16} /> Stop current clip</button>}</div>
        <p className="motion-lab-help" role="status">{running ? running.run?.stage || "Rendering the current test…" : comparison.paused ? "Paused. Resume enables the next render; it does not start one." : allDone ? "Comparison complete. Watch the clips and record what worked." : failed ? "A clip failed or was cancelled. Its receipt is preserved; start a new comparison to retry." : "Ready when you are. Render next clip submits one test."} Closing this panel does not stop a running clip.</p>
        <section className="motion-lab-results" aria-label="Comparison results">{comparison.items.map((item, index) => <article key={item.request_id} className="motion-lab-result"><header><h3>{index + 1}. {item.recipe_name}</h3><span>Seed {item.seed} · {item.run?.status || item.status}</span></header>{item.run?.video_url ? <video controls preload="metadata" src={item.run.scene_video_url || item.run.video_url} aria-label={`${item.recipe_name}, seed ${item.seed}`} /> : <div className="motion-lab-empty">{item.run?.stage || "Ready to render"}</div>}
          <p className="motion-lab-metrics">Total {timeLabel(item.run?.elapsed_seconds)} · ComfyUI {timeLabel(item.run?.server_execution_seconds)}{item.run?.width && <> · {item.run.width}×{item.run.height} · {item.run.frames} frames</>}</p>
          {item.run?.error && <p className="motion-lab-error">{item.run.error}</p>}
          <div className="motion-lab-ratings">{ratingFields.map(([field, label]) => <label key={field}>{label}<select value={item.ratings[field] || "uncertain"} disabled={busy || !item.run?.video_url} onChange={e => rate(item, { ...item.ratings, [field]: e.target.value })}>{ratingOptions.map(([value, text]) => <option value={value} key={value}>{text}</option>)}</select></label>)}</div>
          <label>Notes<textarea key={`${item.request_id}-notes`} defaultValue={item.notes} maxLength={2000} rows={2} placeholder="What moved correctly? What changed unexpectedly?" onBlur={e => { if (e.target.value !== item.notes) void rate(item, item.ratings, e.target.value); }} /></label>
          <details><summary>Exact H3 prompt</summary><pre>{item.prompt}</pre></details>
        </article>)}</section>
        <button type="button" disabled={busy || !!running} onClick={newComparison}>Create another comparison</button>
      </>}
      {error && <div className="motion-lab-error" role="alert">{error}{selected && <button type="button" disabled={busy} onClick={() => refresh()}>Reconnect comparison</button>}</div>}
    </div>
  </dialog>;
}
