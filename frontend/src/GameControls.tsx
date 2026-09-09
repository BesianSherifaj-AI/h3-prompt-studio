import { useEffect, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Compass, RefreshCw } from "lucide-react";
import { api } from "./api";
import type { GameCharacter, GameIntent, Story, StoryTurn } from "./storyTypes";

type AvailableAction = { kind: string; label?: string; message?: string; target_id?: string; enabled?: boolean; reason?: string };
type ActionCatalog = { targets: { id: string; name: string; kind: string }[]; actions: AvailableAction[] };
export function isInventoryCommand(message: string) {
  return /^(?:(?:i\s+)?(?:open|show|check)\s+)?(?:my\s+)?inventory[.!?]?$/i.test(message.trim());
}
export function focusGameInventory(storyId: string) {
  const panel = document.getElementById(`game-inventory-${storyId}`);
  panel?.focus({ preventScroll: true });
  panel?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
export function gameCharacterStatus(character: GameCharacter) {
  const state = character.state || {}, status = String(state.status || "").toLowerCase();
  if (state.dead === true || state.alive === false || status === "dead") return "Dead";
  if (state.defeated === true || status === "defeated") return "Defeated";
  if (state.unconscious === true || status === "unconscious") return "Unconscious";
  if (typeof character.state?.health === "number") return `Health ${character.state.health}`;
  return "";
}
export function gameWorldSummary(story: Story) {
  const world = story.world;
  const player = world?.characters.find(c => c.id === story.player_character_id) || world?.characters.find(c => c.control === "player");
  const locationId = player?.location_id ?? world?.current_location_id;
  const nearby = world?.characters.filter(c => c.id !== player?.id && c.location_id === locationId) || [];
  return {
    player, location: world?.locations.find(place => place.id === locationId), nearby,
    recipients: nearby.filter(c => !["Dead", "Defeated", "Unconscious"].includes(gameCharacterStatus(c))),
    inventory: world?.entities.filter(item => player && (item.holder_id === player.id || item.worn_by_id === player.id)) || [],
    ground: world?.entities.filter(item => !item.holder_id && !item.worn_by_id && item.location_id === locationId && item.state?.hidden !== true) || [],
  };
}
export function gameActionMessage(action: AvailableAction, story: Story, recipientId = "") {
  const item = story.world?.entities.find(item => item.id === action.target_id);
  const character = story.world?.characters.find(c => c.id === action.target_id);
  if (action.kind === "give") return `I attempt to give ${item?.name || "this object"} to ${story.world?.characters.find(c => c.id === recipientId)?.name || "the selected character"}.`;
  if (action.kind === "talk") return `I talk to ${character?.name || "the selected character"}.`;
  if (action.kind === "attack") return `I attempt to attack ${character?.name || "the selected target"}.`;
  return action.message || `I attempt to ${(action.label || action.kind).replace(/^./, letter => letter.toLowerCase())}.`;
}

export function GameWorldStatus({ story, catalog, target, disabled, onSelect, onAction }: {
  story: Story; catalog?: ActionCatalog | null; target: string; disabled: boolean;
  onSelect: (id: string) => void; onAction: (action: AvailableAction) => void;
}) {
  const { player, location, nearby, recipients, inventory, ground } = gameWorldSummary(story);
  if (!story.world) return null;
  const itemActions = (id: string, kinds: string[]) => (catalog?.actions || []).filter(a => a.target_id === id && kinds.includes(a.kind));
  return <section className="game-world-status" aria-label="World and inventory">
    <div className="game-world-location"><strong>{location?.name || "Current scene"}</strong>{player && <span>{player.name}{gameCharacterStatus(player) ? ` · ${gameCharacterStatus(player)}` : ""}</span>}</div>
    {!!nearby.length && <div className="game-world-people" aria-label="Characters here">{nearby.map(character => <button type="button" key={character.id} aria-pressed={target === character.id} onClick={() => onSelect(character.id)}>{character.name}{gameCharacterStatus(character) && <small> · {gameCharacterStatus(character)}</small>}</button>)}</div>}
    <div className="game-inventory" id={`game-inventory-${story.id}`} tabIndex={-1} aria-label="Your inventory"><strong>Inventory · {inventory.length}</strong>{!inventory.length && <p>Your hands are empty. Pick up an item in this scene to carry it.</p>}
      {inventory.map(item => <div className="game-world-item" key={item.id}><button type="button" aria-pressed={target === item.id} onClick={() => onSelect(item.id)}>{item.name}<small> · {item.worn_by_id === player?.id ? "Worn" : "Carrying"}</small></button><div>{itemActions(item.id, ["drop", "give", "use", "remove"]).map(action => <button type="button" key={action.kind} disabled={disabled || action.enabled === false || (action.kind === "give" && !recipients.length)} title={action.kind === "give" && !recipients.length ? "No character here can receive an item." : action.reason || ""} onClick={() => action.kind === "give" ? onSelect(item.id) : onAction(action)}>{action.kind === "give" ? "Give…" : action.kind[0].toUpperCase() + action.kind.slice(1)}</button>)}</div></div>)}
    </div>
    {!!ground.length && <details className="game-nearby-items"><summary>Items here · {ground.length}</summary>{ground.map(item => <div className="game-world-item" key={item.id}><button type="button" aria-pressed={target === item.id} onClick={() => onSelect(item.id)}>{item.name}</button><div>{itemActions(item.id, ["take", "open", "close", "examine"]).map(action => <button type="button" key={action.kind} disabled={disabled || action.enabled === false} title={action.reason || ""} onClick={() => onAction(action)}>{action.kind === "take" ? "Pick up" : action.kind[0].toUpperCase() + action.kind.slice(1)}</button>)}</div></div>)}</details>}
  </section>;
}

export function GameActions({ story, disabled, onAction }: { story: Story; disabled: boolean; onAction: (message: string, intent: GameIntent) => void }) {
  const [target, setTarget] = useState(""), [savedCatalog, setCatalog] = useState<{ key: string; value: ActionCatalog } | null>(null), [error, setError] = useState("");
  const [recipient, setRecipient] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [extent, setExtent] = useState("step"), [speed, setSpeed] = useState("normal"), [presentation, setPresentation] = useState("continuous"), [mode, setMode] = useState("player");
  const catalogKey = `${story.id}:${story.active_branch_id}:${story.active_run_id || ""}:${story.configuration_revision || 0}`;
  const catalog = savedCatalog?.key === catalogKey ? savedCatalog.value : null;
  const selectedTarget = catalog?.targets.some(t => t.id === target) ? target : "";
  const { recipients } = gameWorldSummary(story);
  const selectedRecipient = recipients.some(c => c.id === recipient) ? recipient : "";
  const actions = (catalog?.actions || []).filter(action => !selectedTarget || action.target_id === selectedTarget);
  useEffect(() => {
    const controller = new AbortController();
    // The complete catalog keeps disappeared targets recoverable and selection instant.
    void api(`/stories/${encodeURIComponent(story.id)}/actions`, undefined, undefined, undefined, { timeoutMs: 15000, signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(value?.targets) || !Array.isArray(value?.actions)) throw new Error("Interaction list was incomplete.");
      setCatalog({ key: catalogKey, value });
      setTarget(old => value.targets.some((t: { id: string }) => t.id === old) ? old : "");
      setError("");
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [catalogKey, refreshVersion]);
  const chooseTarget = (id: string) => { setTarget(id); setRecipient(""); };
  const interact = (action: AvailableAction) => {
    if (action.kind === "inventory") { focusGameInventory(story.id); return; }
    onAction(gameActionMessage(action, story, selectedRecipient), {
      kind: action.kind, target_id: action.target_id || selectedTarget || undefined,
      ...(action.kind === "give" ? { recipient_id: selectedRecipient } : {}),
    });
  };
  const go = (direction: string) => {
    const destination = catalog?.targets.find(t => t.id === selectedTarget);
    if ((extent === "travel" || presentation === "teleport") && destination?.kind !== "location") {
      setError("Choose a connected location under Interact with before travelling or teleporting.");
      return;
    }
    const availableMove = catalog?.actions.find(action => action.kind === "move" && action.target_id === selectedTarget);
    if (availableMove?.enabled === false) {
      setError(availableMove.reason || "That destination is not available yet.");
      return;
    }
    const motion = mode === "camera" ? `The camera moves ${direction}` : `I attempt to move ${direction}`;
    const amount = { step: "a small distance", nearby: "to a nearby point", travel: "toward the next location" }[extent];
    setError("");
    onAction(`${motion} ${amount}${destination ? ` toward ${destination.name}` : ""}, at ${speed} speed. ${presentation === "cut" ? "Show the travel using a cut." : presentation === "teleport" ? "Show a deliberate teleportation if the world's rules permit it." : "Show continuous movement."}`, { kind: "move", target_id: selectedTarget || undefined, extent, speed, camera: mode, presentation, direction });
  };
  return <section className="game-context-actions" aria-label="Move and interact">
    <div className="game-navigation-core">
      <div className="game-direction-pad"><strong><Compass size={16}/> Move</strong>
        <div className="game-arrows" aria-label="Movement directions"><button type="button" disabled={disabled} aria-label="Move forward" onClick={() => go("forward")}><ArrowUp/></button><button type="button" disabled={disabled} aria-label="Move left" onClick={() => go("left")}><ArrowLeft/></button><button type="button" disabled={disabled} aria-label="Move backward" onClick={() => go("backward")}><ArrowDown/></button><button type="button" disabled={disabled} aria-label="Move right" onClick={() => go("right")}><ArrowRight/></button></div>
        <small>One click queues one scene · edit within 3s</small>
      </div>
      <div className="game-interaction-target">
        <label>Interact with<select value={selectedTarget} onChange={e => chooseTarget(e.target.value)}><option value="">Current scene</option>{(catalog?.targets || []).map(t => <option key={t.id} value={t.id}>{t.name} · {t.kind}</option>)}</select></label>
        {actions.some(a => a.kind === "give" && a.enabled !== false) && (recipients.length ? <label>Give to<select value={selectedRecipient} onChange={e => setRecipient(e.target.value)}><option value="">Choose a character…</option>{recipients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label> : <p className="game-help">No character here can receive an item.</p>)}
        <div className="game-action-buttons" aria-label="Available interactions">{actions.map((action, i) => <button type="button" key={`${action.kind}:${action.target_id || ""}:${i}`} disabled={disabled || action.enabled === false || (action.kind === "give" && !selectedRecipient)} title={action.reason || ""} onClick={() => interact(action)}>{action.label || action.kind}</button>)}</div>
      </div>
    </div>
    {error && <p role="status" className="game-help">{error} You can still write a move. <button type="button" onClick={() => setRefreshVersion(value => value + 1)}>Refresh interactions</button></p>}
    <GameWorldStatus story={story} catalog={catalog} target={selectedTarget} disabled={disabled} onSelect={chooseTarget} onAction={interact}/>
    <details className="game-motion-options"><summary>Movement options <span>{extent === "step" ? "A step" : extent === "nearby" ? "Nearby" : "Travel"} · {speed}</span></summary>
      <div className="game-movement-settings"><label>Move<select value={mode} onChange={e => setMode(e.target.value)}><option value="player">My character</option><option value="camera">Camera only</option></select></label><label>Distance<select value={extent} onChange={e => setExtent(e.target.value)}><option value="step">A step</option><option value="nearby">Nearby</option><option value="travel">Travel</option></select></label><label>Motion speed<select value={speed} onChange={e => setSpeed(e.target.value)}><option value="slow">Slow</option><option value="normal">Normal</option><option value="fast">Fast</option></select></label><label>Show movement<select value={presentation} onChange={e => setPresentation(e.target.value)}><option value="continuous">Continuous</option><option value="cut">Cut</option><option value="teleport">Teleportation</option></select></label></div>
      <p className="game-help">Movement guides the generated scene. It is not a measured 3D simulation.</p>
    </details>
  </section>;
}

export function GameStages({ turn }: { turn: StoryTurn }) {
  const stage = ["rendering", "observing", "succeeded", "inspection_failed", "awaiting_acceptance"].includes(turn.status) ? 3 : turn.status === "assets" ? 2 : 1;
  return <ol className="game-three-stages" aria-label="Scene progress">{["Write the response", "Prepare references", "Render & inspect"].map((name, i) => <li key={name} aria-current={stage === i + 1 ? "step" : undefined} className={stage === i + 1 ? "active" : stage > i + 1 ? "complete" : ""}><b>{i + 1}</b><span>{name}</span></li>)}</ol>;
}

export function GameReceipt({ turn }: { turn: StoryTurn }) {
  const [receipt, setReceipt] = useState<any>(null), [error, setError] = useState(""), [open, setOpen] = useState(false);
  const refresh = async () => { try { const r = turn.receipt || (turn.run_id ? await api(`/video/runs/${encodeURIComponent(turn.run_id)}/receipt`) : null); setReceipt(r); setError(r ? "" : "No video has been queued for this turn yet."); } catch(e) { setError((e as Error).message); } };
  return <details className="game-receipt" onToggle={e => { if (e.currentTarget.open && !open) void refresh(); setOpen(e.currentTarget.open); }}><summary>What ran · prompt & references</summary>
    <p className="game-help">Turn {turn.id}{turn.run_id ? ` · Video ${turn.run_id}` : " · Not rendered yet"}</p>
    {receipt && <><pre>{typeof receipt.prompt === "string" ? receipt.prompt : receipt.compiled_prompt || "Prompt is included in the receipt below."}</pre><details><summary>Queue receipt</summary><pre>{JSON.stringify(receipt, null, 2)}</pre></details></>}
    {error && <p className="game-help" role="status">{error}</p>}<button className="quiet" onClick={() => void refresh()}><RefreshCw size={14}/> Refresh receipt</button>
  </details>;
}
