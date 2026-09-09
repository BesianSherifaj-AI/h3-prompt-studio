import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api";
import { storyTurnPending, storyVideos, type GameIntent, type Story } from "./storyTypes";
import type { SceneCatalog } from "./GameScenePanel";
import "./GamePlayerPicker.css";

export type PlayerPickerRequest = { nonce: string; message?: string; intent?: GameIntent };
export type PlayerPickerScope = { storyId: string; runId: string; branchId: string; revision: number };
export function playerPickerScope(story: Story): PlayerPickerScope {
  return { storyId: story.id, runId: story.active_run_id || "", branchId: story.active_branch_id, revision: story.configuration_revision || 0 };
}
export function samePickerFrame(scope: PlayerPickerScope, story: Story, revision = scope.revision) {
  return story.id === scope.storyId && story.active_run_id === scope.runId && story.active_branch_id === scope.branchId && story.configuration_revision === revision;
}
export function selectablePlayers(scene: SceneCatalog | undefined, scope: PlayerPickerScope, playerId?: string) {
  return scene?.status === "ready" && scene.run_id === scope.runId && scene.branch_id === scope.branchId && scene.configuration_revision === scope.revision
    ? scene.targets.filter(target => target.kind === "person" && (!target.known_id || target.known_id === playerId)) : [];
}

/** A lost acknowledgement reuses its original body and UUID. Concurrent clicks
 * share the in-flight promise; only an explicit retry of a completed scan is new. */
export function playerPickerRequests(scope: PlayerPickerScope, request = api) {
  const base = { run_id: scope.runId, branch_id: scope.branchId, configuration_revision: scope.revision };
  let scanBody: Record<string, unknown> | null = null, scanPromise: Promise<any> | null = null;
  const binds = new Map<string, { body: Record<string, unknown>; promise: Promise<any> | null }>();
  return {
    scan(newAttempt = false) {
      if (scanPromise) return scanPromise;
      if (!scanBody || newAttempt) scanBody = { ...base, request_id: crypto.randomUUID() };
      scanPromise = request(`/stories/${encodeURIComponent(scope.storyId)}/scene-inspection`, scanBody, undefined, undefined, { timeoutMs: 30000 });
      const pending = scanPromise;
      void pending.then(() => { if (scanPromise === pending) scanPromise = null; }, () => { if (scanPromise === pending) scanPromise = null; });
      return pending;
    },
    bind(selection: { candidate_id: string } | { appearance: string }) {
      const key = JSON.stringify(selection);
      let attempt = binds.get(key);
      if (!attempt) { attempt = { body: { ...base, ...selection, request_id: crypto.randomUUID() }, promise: null }; binds.set(key, attempt); }
      if (attempt.promise) return attempt.promise;
      attempt.promise = request(`/stories/${encodeURIComponent(scope.storyId)}/scene-player`, attempt.body, undefined, undefined, { timeoutMs: 30000 });
      const pending = attempt.promise;
      void pending.then(() => { if (attempt!.promise === pending) attempt!.promise = null; }, () => { if (attempt!.promise === pending) attempt!.promise = null; });
      return pending;
    },
  };
}

export function GamePlayerPicker({ story, request, scope, preparationError = "", onCancel, onRestart, onChosen, onRefreshStory }: {
  story: Story; request: PlayerPickerRequest; scope: PlayerPickerScope | null; preparationError?: string;
  onCancel: () => void; onRestart: () => void; onChosen: (story: Story) => void;
  onRefreshStory?: () => Promise<unknown>;
}) {
  const [scene, setScene] = useState<SceneCatalog>(), [phase, setPhase] = useState("loading"), [error, setError] = useState("");
  const [manual, setManual] = useState(false), [appearance, setAppearance] = useState("");
  const [scanVersion, setScanVersion] = useState(0), [imageFailed, setImageFailed] = useState(false);
  const active = useRef(true), saving = useRef(false), revision = useRef(0), dialog = useRef<HTMLDivElement>(null);
  const session = useRef<{ scope: PlayerPickerScope; requests: ReturnType<typeof playerPickerRequests> } | null>(null);
  const confirmed = useRef<Story | null>(null), lastSelection = useRef<{ candidate_id: string } | { appearance: string } | null>(null);
  const currentStory = useRef(story); currentStory.current = story;
  const latest = useRef({ onChosen, onRefreshStory }); latest.current = { onChosen, onRefreshStory };
  const scopeKey = scope ? `${scope.storyId}:${scope.runId}:${scope.branchId}:${scope.revision}` : "";
  const stale = !!scope && !samePickerFrame(scope, story) && !saving.current && !confirmed.current;
  const blocked = story.turns.some(storyTurnPending);
  const people = scope ? selectablePlayers(scene, scope, story.player_character_id) : [];
  const runId = scope?.runId || story.active_run_id;
  const ending = storyVideos(story).find(video => video.id === runId)?.ending_image_url || (runId ? `/api/video/runs/${encodeURIComponent(runId)}/ending` : "");
  useEffect(() => {
    active.current = true;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    return () => { active.current = false; revision.current++; previous?.focus(); };
  }, []);
  useEffect(() => {
    if (!scope || !scope.runId || blocked || stale) return;
    if (!session.current || JSON.stringify(session.current.scope) !== JSON.stringify(scope)) session.current = { scope, requests: playerPickerRequests(scope) };
    const token = ++revision.current, controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let started = false;
    const valid = () => active.current && revision.current === token && !controller.signal.aborted && !saving.current;
    const poll = async () => {
      try {
        const result = await api(`/stories/${encodeURIComponent(scope.storyId)}/actions`, undefined, undefined, undefined, { timeoutMs: 15000, signal: controller.signal });
        if (!valid()) return;
        const current = result?.scene as SceneCatalog | undefined;
        if (!current || current.run_id !== scope.runId || current.branch_id !== scope.branchId || current.configuration_revision !== scope.revision) {
          setPhase("stale"); setError("The scene changed. Choose from the current scene instead."); return;
        }
        setScene(current);
        if (current.status === "pending_review") { setPhase("blocked"); return; }
        if (selectablePlayers(current, scope, currentStory.current.player_character_id).length) { setPhase("ready"); setError(""); return; }
        if (["pending", "running"].includes(current.inspection?.status || "")) { started = true; setPhase("finding"); timer = setTimeout(poll, 1500); return; }
        if (!started) {
          started = true; setPhase("finding");
          await session.current!.requests.scan(scanVersion > 0 && ["failed", "succeeded"].includes(current.inspection?.status || ""));
          if (valid()) timer = setTimeout(poll, 700);
          return;
        }
        setPhase("empty");
        setError(current.inspection?.status === "failed" ? "I couldn’t find the people in this picture. Try again, or describe your character below." : "No selectable person was found. Try again, or describe which person you are.");
      } catch (error) {
        if (valid()) { setPhase("error"); setError(`The character choices could not be loaded. ${(error as Error).message}`); }
      }
    };
    setPhase("loading"); setError(""); void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [scopeKey, scanVersion, blocked, stale]);
  const choose = async (selection: { candidate_id: string } | { appearance: string }) => {
    if (!scope || saving.current || stale || blocked) return;
    saving.current = true; revision.current++; setPhase("saving"); setError(""); lastSelection.current = selection;
    try {
      if (!session.current) session.current = { scope, requests: playerPickerRequests(scope) };
      const updated = confirmed.current || await session.current.requests.bind(selection);
      if (!updated || updated.id !== scope.storyId || updated.active_run_id !== scope.runId || updated.active_branch_id !== scope.branchId || !(updated.configuration_revision > scope.revision)) throw new Error("Your choice could not be confirmed. Retry saving it.");
      confirmed.current = updated;
      if (!active.current) return;
      await latest.current.onRefreshStory?.();
      if (active.current) latest.current.onChosen(updated);
    } catch (error) {
      if (active.current) { setPhase("save-error"); setError(confirmed.current ? "Your character was saved, but the game could not refresh. Try again to continue." : `Your choice could not be confirmed. ${(error as Error).message}`); }
    } finally { saving.current = false; }
  };
  const content = <div className="game-player-picker-backdrop" onClick={event => { if (event.target === event.currentTarget) onCancel(); }}>
    <div className="game-player-picker" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="game-player-picker-title" tabIndex={-1} onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); onCancel(); }
      if (event.key === "Tab") {
        const items = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),textarea:not(:disabled),[href]') || []);
        const first = items[0], last = items.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <header><div><h2 id="game-player-picker-title">Who are you playing?</h2><p>{request.intent ? "Choose once. Your move continues after you pick." : "Choose the person you want to play."}</p></div><button type="button" aria-label="Cancel character selection" onClick={onCancel}>×</button></header>
      {ending && !imageFailed ? <img className="game-player-picker-image" src={ending} alt="Your current scene. Choose your character from this picture." onError={() => setImageFailed(true)}/> : <p>The current picture is unavailable. Keep the saved video available, then try again.</p>}
      <div className="game-player-picker-content">
        {request.intent && <p className="game-picker-pending">Saved move: {request.intent.direction || request.message}</p>}
        {preparationError ? <div role="alert"><p>{preparationError}</p><button type="button" onClick={onRestart}>Try again</button></div> : scope && !scope.runId ? <p>Create your first scene before choosing a character from its picture. You can set your appearance in Edit game.</p> : stale || phase === "stale" ? <div role="alert"><p>The scene changed while this was open. Your move has not been sent.</p><button type="button" onClick={onRestart}>Use the current scene</button></div> : blocked || phase === "blocked" ? <p>Finish or review the current scene first, then choose your character.</p> : <>
          {(!scope || ["loading", "finding"].includes(phase)) && <p role="status">{!scope ? "Getting your scene ready…" : "Finding people in this picture…"}</p>}
          {phase === "saving" && <p role="status">Saving your character…</p>}
          {error && <p role="alert">{error}</p>}
          {people.length > 0 && !["saving", "save-error"].includes(phase) && <div className="game-player-picker-people">{people.map(person => <button type="button" key={person.id} onClick={() => void choose({ candidate_id: person.id })}><strong>{person.label}</strong><span>{person.description}</span><small>{person.position}</small></button>)}</div>}
          {["empty", "error"].includes(phase) && <button type="button" onClick={() => setScanVersion(value => value + 1)}>Try finding people again</button>}
          {phase === "save-error" && lastSelection.current && <button type="button" onClick={() => void choose(lastSelection.current!)}>Retry saving my character</button>}
          <button type="button" className="quiet" disabled={!scope || phase === "saving"} aria-expanded={manual} onClick={() => setManual(value => !value)}>Describe my character instead</button>
          {manual && <form onSubmit={event => { event.preventDefault(); if (appearance.trim()) void choose({ appearance: appearance.trim() }); }}><label>What does your character look like?<textarea rows={2} maxLength={500} value={appearance} onChange={event => setAppearance(event.target.value)} placeholder="For example: the person on the left in the purple shirt and glasses" disabled={phase === "saving"}/></label><button type="submit" className="primary" disabled={!scope || !appearance.trim() || phase === "saving"}>{request.intent ? "Use this character and continue" : "Use this character"}</button></form>}
        </>}
      </div>
      <footer><button type="button" className="quiet" onClick={onCancel}>{request.intent ? "Cancel this move" : "Cancel"}</button></footer>
    </div>
  </div>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}
