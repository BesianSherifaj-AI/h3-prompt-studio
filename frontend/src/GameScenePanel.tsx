import { useEffect, useState } from "react";
import type { GameIntent, Story } from "./storyTypes";

export type SceneTarget = {
  id: string; kind: "person" | "door" | "object"; known_id?: string | null;
  label: string; description: string; position: string; identity_status: "known" | "unidentified";
  actions: { kind: string; label: string; enabled?: boolean; reason?: string; intent: GameIntent }[];
};
export type SceneCatalog = {
  run_id?: string; branch_id?: string; configuration_revision?: number;
  status: "ready" | "unavailable" | "pending_review" | "stale"; setting?: string; targets: SceneTarget[];
  inspected_run_id?: string; inspection_status?: string;
  description?: string;
  inspection?: { status: "pending" | "running" | "succeeded" | "failed"; request_id?: string; error?: string };
};

export function observedSceneText(story: Story, runId?: string) {
  const turn = [...story.turns].reverse().find(turn => turn.run_id === runId);
  const observation = turn?.observation as { observed_state?: unknown } | undefined;
  return typeof observation?.observed_state === "string" ? observation.observed_state : "";
}

export function GameScenePanel({ story, viewedRunId, scene, loading, disabled, binding, inspecting, onAction, onBind, onInspect }: {
  story: Story; viewedRunId?: string; scene?: SceneCatalog; loading?: boolean; disabled: boolean;
  binding?: boolean; inspecting?: boolean; onAction: (message: string, intent: GameIntent) => void;
  onBind?: (scene: SceneCatalog, target: SceneTarget) => void;
  onInspect?: (scene: SceneCatalog) => void;
}) {
  const [selected, setSelected] = useState("");
  const current = scene?.run_id === viewedRunId ? scene : undefined;
  const targets = current?.targets || [];
  const target = targets.find(item => item.id === selected);
  const pending = current?.status === "pending_review" || !!story.turns.find(turn => turn.run_id === viewedRunId && ["awaiting_acceptance", "inspection_failed"].includes(turn.status));
  const canAct = current?.status === "ready" && current.run_id === story.active_run_id;
  const stale = current?.status === "stale";
  const text = observedSceneText(story, viewedRunId) || current?.description || "";
  const player = story.world?.characters.find(person => person.id === story.player_character_id);
  useEffect(() => setSelected(""), [current?.run_id, current?.branch_id]);
  return <section className="game-visible-scene" id={`game-visible-scene-${story.id}`} tabIndex={-1} aria-label="Visible scene">
    <h3>In this frame</h3>
    <p className="game-scene-basis">{pending ? "New ending · waiting for review" : viewedRunId === story.active_run_id ? "Current accepted ending" : viewedRunId ? "Viewed ending · earlier take" : "No rendered ending yet"}</p>
    {stale && <p className="game-scene-review-note">These people and objects come from an earlier inspected frame. Their positions in this new ending have not been checked.</p>}
    <p className="game-player-identity">You play <strong>{player?.name || story.player_name}</strong>. {player?.description || "Your appearance is not identified yet. Select a visible person and choose “This is me” on an accepted ending."}</p>
    {current?.setting && <p>{current.setting}</p>}
    {loading && <p role="status">Reading saved scene details…</p>}
    {targets.length > 0 ? <>
      <div className="game-scene-targets" aria-label="People and objects in the ending">{targets.map(item => <button type="button" key={item.id} aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}>
        <strong>{item.label}{item.known_id === story.player_character_id ? " · You" : ""}</strong>
        <span>{stale ? "Previously: " : ""}{item.position || "Position not established"}</span>
        <small>{item.identity_status === "known" ? "Established in the story" : "Visible · identity not established"}</small>
      </button>)}</div>
      {!target && <p>Select a person or object to see its available actions.</p>}
      {target && <div className="game-scene-selected" aria-label="Selected visible target">
        <strong>{target.label}</strong><p>{target.description}</p>
        <div className="game-scene-actions">{target.actions.map((action, index) => <button type="button" key={index} disabled={disabled || !canAct || action.enabled === false || !action.intent} title={stale ? "Inspect this ending to refresh visible positions." : !canAct ? "Review and accept this ending before interacting with its people or objects." : action.reason || ""} onClick={() => onAction(`I ${action.kind === "talk" ? "talk to" : action.kind === "move" ? "move toward" : "examine"} ${target.label}.`, action.intent)}>{action.label}</button>)}
          {!stale && target.kind === "person" && (!target.known_id || target.known_id === story.player_character_id) && onBind && <button type="button" disabled={disabled || !canAct || binding} title="Identify this visible person as your player character. This does not create a video." onClick={() => current && onBind(current, target)}>{binding ? "Saving identity…" : "This is me"}</button>}
        </div>
      </div>}
    </> : !loading && <p className="game-scene-empty">{viewedRunId ? "This ending has no saved selectable people or objects yet. Visible scenery is not automatically an item in your inventory." : "Your first rendered ending will provide the scene to inspect."}</p>}
    {text && <details open={!targets.length}><summary>What the ending inspection saw</summary><p>{text}</p></details>}
    {current && ["unavailable", "stale"].includes(current.status) && viewedRunId === story.active_run_id && onInspect && <div className="game-scene-inspection"><button type="button" disabled={disabled || inspecting || ["pending", "running"].includes(current.inspection?.status || "")} onClick={() => onInspect(current)}>{inspecting || ["pending", "running"].includes(current.inspection?.status || "") ? "Inspecting the ending…" : "Inspect this ending"}</button><p>Find selectable people and objects in the saved ending image. This does not render another video.</p>{current.inspection?.error && <p role="status">{current.inspection.error}</p>}</div>}
    {pending && <p className="game-scene-review-note">Review this new ending above the video first. Queued moves wait for your decision.</p>}
  </section>;
}
