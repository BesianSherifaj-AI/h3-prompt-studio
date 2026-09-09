import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ImagePlus, Plus, RotateCcw, X } from "lucide-react";
import { api } from "./api";
import { newShot, retime, uid, type Asset, type Project } from "./model";
import { SceneDirector } from "./SceneDirector";
import { REF_SPEED_LORA, FRAME_SPEED_LORA } from "./recipeLoras";
import { addGameAssets, assignGameAsset, characterFromSubject, ensurePlayer, removeGameCharacter, replaceGameAsset, updateCharacter } from "./gameConfiguration";
import type { GameCharacter, ImageGeneratorModel, StoryConfiguration } from "./storyTypes";
import "./GameEditor.css";
import { setDirectorValue } from "./shotDirections";
import { PIXEL_STYLE } from "./storyTypes";
import { GameImageSettings } from "./GameImageSettings";

type Props = { value: StoryConfiguration; onChange: (next: StoryConfiguration) => void; onSave: () => void; onClose: () => void; onStopApply?: () => void;
  dirty: boolean; saving: boolean; busy: boolean; onUploadFiles?: (files: File[]) => Promise<Asset[]>; modelPicker?: ReactNode; generators: ImageGeneratorModel[]; generatorsLoading?: boolean; generatorsChecked?: boolean; generatorErrors?: string[]; onRefreshGenerators?: () => void; initialTab?: string };
const tabs = [["cast", "Characters"], ["photos", "Photos & sound"], ["world", "World & behavior"], ["scene", "Next scene"], ["render", "Rendering"]];
export default function GameEditor({ value, onChange, onSave, onClose, onStopApply, dirty, saving, busy, onUploadFiles, modelPicker, generators, generatorsLoading, generatorsChecked, generatorErrors, onRefreshGenerators, initialTab }: Props) {
  const [tab, setTab] = useState(initialTab || "cast"), [error, setError] = useState(""), [uploading, setUploading] = useState(false);
  const [undo, setUndo] = useState<StoryConfiguration | null>(null), [catalog, setCatalog] = useState<any>(null);
  const [transcribing, setTranscribing] = useState("");
  const input = useRef<HTMLInputElement>(null), replacement = useRef(""), close = useRef<HTMLButtonElement>(null);
  const latest = useRef(value); latest.current = value;
  useEffect(() => { close.current?.focus(); }, []);
  useEffect(() => { if (value.player_name.trim() && !value.world.characters.some(c => c.id === value.player_character_id)) onChange(ensurePlayer(value)); }, [value.player_name, value.player_character_id]);
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  useEffect(() => { if (tab !== "render") return; let active = true; api("/comfy/options").then(r => { if (active) setCatalog(r); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [tab]);
  const change = (fn: (draft: StoryConfiguration) => void) => { const next = structuredClone(value); fn(next); onChange(next); };
  const updateProject = (fn: (draft: Project) => void) => change(c => fn(c.project));
  const remove = (fn: (draft: StoryConfiguration) => void) => { setUndo(structuredClone(value)); change(fn); };
  const setSetting = (key: string, val: unknown) => change(c => { c.settings[key] = val; if (["resolution", "steps", "seed", "loras", "experimental_preview"].includes(key)) c.project.comfy_render = { ...c.project.comfy_render, [key]: val }; if (key === "aspect_ratio") c.project.aspect_ratio = String(val); if (key === "duration") { c.project.duration = Number(val); c.project.shots = retime(c.project.shots, Number(val)); } });
  const setChar = (id: string, patch: Partial<GameCharacter>) => onChange(updateCharacter(value, id, patch));
  const transcribe = async (asset: Asset) => { setTranscribing(asset.id); setError(""); const projectId = value.project.id; try { const result = await api("/audio/transcribe", { asset_id: asset.id, options: { model: "small", cpu_threads: 4 } }); if (latest.current.project.id === projectId && latest.current.project.assets.some(a => a.id === asset.id)) onChange(assignGameAsset(latest.current, asset.id, { description: result.text })); } catch(e) { setError((e as Error).message); } finally { setTranscribing(""); } };
  const trackFor = (id: string) => (value.project.soundtrack_tracks || []).find((track: any) => track.asset_id === id) || {};
  const setTrack = (id: string, patch: any) => change(c => { const tracks = c.project.soundtrack_tracks || []; const old = tracks.find((track: any) => track.asset_id === id); c.project.soundtrack_tracks = [...tracks.filter((track: any) => track.asset_id !== id), { asset_id: id, ...old, ...patch }]; });
  const upload = async (files: FileList | null) => {
    if (!files?.length || !onUploadFiles) return;
    const before = structuredClone(latest.current), replaceId = replacement.current;
    setUploading(true); setError("");
    try {
      const uploaded = await onUploadFiles(Array.from(files));
      if (latest.current.project.id !== before.project.id) throw new Error("The game changed during upload. Open the original game to attach these files.");
      if (replaceId) { if (!uploaded[0]) throw new Error("No replacement was uploaded."); setUndo(before); onChange(replaceGameAsset(latest.current, replaceId, uploaded[0])); }
      else onChange(addGameAssets(latest.current, uploaded));
    } catch (e) { setError((e as Error).message); }
    finally { replacement.current = ""; setUploading(false); if (input.current) input.current.value = ""; }
  };
  const loras = (value.settings.loras || value.project.comfy_render?.loras || [{ name: value.project.mode === "ref2va" ? REF_SPEED_LORA : FRAME_SPEED_LORA, strength: 1, enabled: true }]) as any[];
  const loraNames: string[] = (catalog?.available_loras || catalog?.loras || []).map((n:any) => typeof n === "string" ? n : n.name).filter(Boolean);
  const setLora = (index: number, patch: any) => setSetting("loras", loras.map((row, i) => i === index ? { ...row, ...patch } : row));
  const labelText = (label: string, val: string, setter: (text: string) => void, rows = 3) => <label>{label}<textarea rows={rows} value={val} onChange={e => setter(e.target.value)} /></label>;
  return <aside className="game-editor" aria-label="Game editor" onKeyDown={e => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
    <header><div><span className="game-eyebrow">YOUR GAME, YOUR DIRECTION</span><h2>Edit game</h2></div><button ref={close} className="icon-button" aria-label="Close game editor" onClick={onClose}><X size={20}/></button></header>
    <nav className="game-editor-tabs" aria-label="Game editor sections">{tabs.map(([id, title]) => <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>{title}</button>)}</nav>
    <div className="game-editor-scroll">
      {busy && <p className="game-editor-notice">This turn keeps its saved settings. Your changes apply to the next turn.</p>}
      {error && <p role="alert" className="game-alert">{error}</p>}
      {undo && <button className="quiet" onClick={() => { onChange(undo); setUndo(null); }}><RotateCcw size={16}/> Undo last removal or replacement</button>}
      {tab === "cast" && <section>
        <h3>Who is in your story?</h3><p className="game-help">You control one character. The assistant plays the others using their own behavior and knowledge.</p>
        <label>I play<select value={value.player_character_id} onChange={e => change(c => { c.player_character_id = e.target.value; c.player_name = c.world.characters.find(a => a.id === e.target.value)?.name || ""; c.world.characters.forEach(a => { a.control = a.id === e.target.value ? "player" : "npc"; }); })}><option value="">Choose your character…</option>{value.world.characters.map(c => <option key={c.id} value={c.id}>{c.name || "Unnamed character"}</option>)}</select></label>
        {value.world.characters.map(char => <details key={char.id} className="game-editor-card" open><summary>{char.name || "New character"}{char.id === value.player_character_id ? " · You" : " · AI role"}</summary>
          <label>Name<input value={char.name} onChange={e => setChar(char.id, { name: e.target.value })}/></label>
          {labelText("Appearance & identity", char.description || "", text => setChar(char.id, { description: text }))}
          {labelText("Personality & behavior", char.personality || "", text => setChar(char.id, { personality: text }))}
          {labelText("What this character wants", char.goals || "", text => setChar(char.id, { goals: text }), 2)}
          <details><summary>Voice, relationships & private knowledge</summary>
            {labelText("How they speak · language, voice, manner", char.speaking_style || "", text => setChar(char.id, { speaking_style: text }), 2)}
            {labelText("Private knowledge · hidden from other characters", char.private_knowledge || "", text => setChar(char.id, { private_knowledge: text }))}
            {value.world.characters.filter(c => c.id !== char.id).map(other => <label key={other.id}>Relationship to {other.name}<input value={typeof char.relationships === "object" ? char.relationships[other.id] || "" : ""} onChange={e => setChar(char.id, { relationships: { ...(typeof char.relationships === "object" ? char.relationships : {}), [other.id]: e.target.value } })}/></label>)}
          </details>
          <button className="quiet" disabled={char.id === value.player_character_id} onClick={() => { setUndo(structuredClone(value)); onChange(removeGameCharacter(value, char.id)); }}><X size={16}/> Remove from this game</button>
        </details>)}
        <button onClick={() => change(c => { const subject = { id: uid(), name: "", description: "", asset_ids: [] }; c.project.subjects.push(subject); c.world.characters.push(characterFromSubject(subject)); })}><Plus size={16}/> Add character</button>
      </section>}
      {tab === "photos" && <section>
        <div className="game-section-title"><h3>Reusable photos & sound</h3><button disabled={uploading || !onUploadFiles} onClick={() => { replacement.current = ""; input.current?.click(); }}><ImagePlus size={16}/>{uploading ? "Uploading…" : "Add media"}</button></div>
        <input ref={input} type="file" accept="image/*,audio/*,video/*" multiple={!replacement.current} hidden onChange={e => void upload(e.target.files)}/>
        <p className="game-help">New uploads start in your library. Assign their purpose, then connect only what the next scene needs. X disconnects a reference; the original stays saved.</p>
        {!value.project.assets.length && <p className="game-empty-note">Add a face, outfit, location, object, style image, or sound.</p>}
        {value.project.assets.map(asset => <article className="game-editor-card game-media-card" key={asset.id}>
          <div className="game-media-heading">{asset.media_type === "image" ? <img src={`/api/assets/${encodeURIComponent(asset.id)}/file`} alt={asset.name} loading="lazy"/> : asset.media_type === "audio" ? <audio controls preload="metadata" src={`/api/assets/${encodeURIComponent(asset.id)}/file`}/> : <video controls preload="metadata" src={`/api/assets/${encodeURIComponent(asset.id)}/file`}/>}
            <button className="icon-button" aria-label={`Disconnect ${asset.name}`} title="Disconnect from next scene" disabled={asset.enabled === false} onClick={() => remove(c => { c.project.assets.find(a => a.id === asset.id)!.enabled = false; })}><X size={18}/></button></div>
          <label>Name<input value={asset.name} onChange={e => onChange(assignGameAsset(value, asset.id, { name: e.target.value }))}/></label>
          <label className="game-checkbox"><input type="checkbox" checked={asset.enabled !== false} onChange={e => onChange(assignGameAsset(value, asset.id, { enabled: e.target.checked }))}/><span>Connected to next scene</span></label>
          {asset.media_type === "image" ? <>
            <label>What does this photo provide?<select value={asset.semantic_role || "other"} onChange={e => onChange(assignGameAsset(value, asset.id, { semantic_role: e.target.value, role: "reference_image" }))}>{[["face","Face / identity"],["wardrobe","Clothes / outfit"],["object","Object / prop"],["background","Place / background"],["style","Visual style"],["palette","Color palette"],["pose","Pose / composition"],["other","Other"]].map(([id,title]) => <option key={id} value={id}>{title}</option>)}</select></label>
            <label>Used by / belongs to<select value={asset.simple_owner_id || value.project.subjects.find(s => s.asset_ids.includes(asset.id))?.id || ""} onChange={e => onChange(assignGameAsset(value, asset.id, { simple_owner_id: e.target.value }))}><option value="">Scene / no character</option>{value.world.characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
            {asset.semantic_role === "object" && <label>Current holder<select value={value.world.entities.find(e => e.asset_ids.includes(asset.id))?.holder_id || ""} onChange={e => { const next = assignGameAsset(value, asset.id, {}); const entity = next.world.entities.find(item => item.asset_ids.includes(asset.id)); if (entity) entity.holder_id = e.target.value; onChange(next); }}><option value="">Nobody / in the scene</option>{value.world.characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}
            <label>Reference use<select value={asset.role} onChange={e => onChange(assignGameAsset(value, asset.id, { role: e.target.value }))}><option value="reference_image">Appearance reference</option><option value="first_frame">Exact first frame</option><option value="last_frame">Exact last frame</option><option value="context">Assistant inspiration only</option></select></label>
          </> : <>
            <label>Audio use<select value={asset.audio_use === "soundtrack" ? "soundtrack" : asset.role === "context" ? "context" : "reference"} onChange={e => onChange(assignGameAsset(value, asset.id, { audio_use: e.target.value === "soundtrack" ? "soundtrack" : "reference", role: e.target.value === "reference" ? `reference_${asset.media_type}` : "context" }))}><option value="reference">H3 voice / sound reference</option><option value="soundtrack">Add to playback soundtrack</option><option value="context">Assistant context only</option></select></label>
            <label>Speaker / character<select value={asset.simple_owner_id || ""} onChange={e => onChange(assignGameAsset(value, asset.id, { simple_owner_id: e.target.value }))}><option value="">Environment / music</option>{value.world.characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
            <div className="game-field-row"><label>Clip start (seconds)<input type="number" min={0} step={0.1} value={asset.clip_start_seconds || 0} onChange={e => onChange(assignGameAsset(value, asset.id, { clip_start_seconds: Number(e.target.value) }))}/></label><label>Clip end (seconds)<input type="number" min={0} step={0.1} value={asset.clip_end_seconds ?? ""} placeholder="End of file" onChange={e => onChange(assignGameAsset(value, asset.id, { clip_end_seconds: e.target.value === "" ? undefined : Number(e.target.value) }))}/></label></div>
            {asset.media_type === "video" && <label className="game-checkbox"><input type="checkbox" checked={asset.audio_enabled !== false} onChange={e => onChange(assignGameAsset(value, asset.id, { audio_enabled: e.target.checked }))}/><span>Use this video's audio track</span></label>}
            <button disabled={!!transcribing} onClick={() => void transcribe(asset)}>{transcribing === asset.id ? "Transcribing on CPU…" : "Transcribe speech into description"}</button><p className="game-help">Uses a cached local speech model. Review the text below before saving; nothing is spoken automatically.</p>
            {asset.audio_use === "soundtrack" && <details><summary>Soundtrack placement & volume</summary><div className="game-field-row">{[["start_seconds", "Use source from (seconds)", 0], ["offset_seconds", "Place at scene time (seconds)", 0], ["gain", "Volume multiplier", 1], ["fade_in", "Fade in (seconds)", 0], ["fade_out", "Fade out (seconds)", 0]].map(([field, title, fallback]) => <label key={String(field)}>{title}<input type="number" min={0} max={field === "gain" ? 4 : undefined} step={0.1} value={trackFor(asset.id)[String(field)] ?? fallback} onChange={e => setTrack(asset.id, { [field]: Number(e.target.value) })}/></label>)}</div><label>Use source until (seconds)<input type="number" min={0} step={0.1} placeholder="End of file" value={trackFor(asset.id).end_seconds ?? ""} onChange={e => setTrack(asset.id, { end_seconds: e.target.value === "" ? null : Number(e.target.value) })}/></label><label className="game-checkbox"><input type="checkbox" checked={!!trackFor(asset.id).duck} onChange={e => setTrack(asset.id, { duck: e.target.checked })}/><span>Lower this layer when original audio is loud</span></label><p className="game-help">After a video finishes, open Add your soundtrack below the player to make a separate mixed version.</p></details>}
          </>}
          <label>Stable tag<input value={asset.prompt_tag || ""} onChange={e => onChange(assignGameAsset(value, asset.id, { prompt_tag: e.target.value.replace(/^@/, "").toLowerCase().replace(/[_ ]/g, "-") }))}/><small>Use @tag in your direction. Letters, numbers and hyphens, such as observatory-key.</small></label>
          {labelText(asset.media_type === "audio" ? "Description / exact transcript" : "What should the assistant retain?", asset.description || "", text => onChange(assignGameAsset(value, asset.id, { description: text })), 2)}
          <div className="game-button-row"><button className="quiet" disabled={uploading || !onUploadFiles} onClick={() => { replacement.current = asset.id; input.current?.click(); }}>Replace file</button><button className="quiet" onClick={() => remove(c => { c.project.assets = c.project.assets.filter(a => a.id !== asset.id); c.project.subjects.forEach(s => { s.asset_ids = s.asset_ids.filter(id => id !== asset.id); }); for (const item of [...c.world.characters, ...c.world.locations, ...c.world.entities]) item.asset_ids = item.asset_ids.filter(id => id !== asset.id); c.project.soundtrack_tracks = (c.project.soundtrack_tracks || []).filter((track: any) => track.asset_id !== asset.id); })}>Remove from library</button></div>
        </article>)}
      </section>}
      {tab === "world" && <section>
        <h3>World & ongoing direction</h3>
        {labelText("Story premise", value.premise, text => change(c => { c.premise = text; c.project.story.text = text; }))}
        {labelText("World rules & tone", value.world.rules || "", text => change(c => { c.world.rules = text; }))}
        <label>Visual style<input value={value.settings.style || ""} placeholder="Your choice · optional" onChange={e => setSetting("style", e.target.value)}/></label>
        <div className="game-button-row"><button onClick={() => setSetting("style", PIXEL_STYLE)}>2D pixel art</button><button onClick={() => setSetting("style", "Cinematic live action, natural anatomy, realistic materials and lighting")}>Realistic</button><button onClick={() => setSetting("style", "Hand-drawn 2D animation with expressive linework and flat painted backgrounds")}>Illustrated</button><button onClick={() => setSetting("style", "")}>Clear style · choose freely</button></div>
        <h3>Objectives</h3>{value.world.objectives.map((objective, i) => <div className="game-editor-card" key={objective.id || i}><label>Objective<input value={objective.title || objective.name || ""} onChange={e => change(c => { c.world.objectives[i].title = e.target.value; })}/></label><label>Status<select value={objective.status || "active"} onChange={e => change(c => { c.world.objectives[i].status = e.target.value; })}><option value="active">Active</option><option value="completed">Completed</option></select></label><button className="quiet" onClick={() => remove(c => { c.world.objectives.splice(i, 1); })}>Remove objective</button></div>)}
        <button onClick={() => change(c => { c.world.objectives.push({ id: uid(), title: "", status: "active" }); })}><Plus size={16}/> Add objective</button>
        <h3>Known places</h3><label>Current place<select value={value.world.current_location_id || ""} onChange={e => change(c => {
          const old = c.world.current_location_id || null, next = e.target.value || null;
          c.world.current_location_id = next;
          for (const person of c.world.characters) if ((person.location_id || null) === old) person.location_id = next;
          if (next !== old) c.settings.transition = "cut";
        })}><option value="">Let the opening establish it</option>{value.world.locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label><small>Changing place moves the present cast to a new scene. Other characters and unheld objects stay in their saved locations.</small>
        {value.world.locations.map((location, i) => <details className="game-editor-card" key={location.id}><summary>{location.name || "New place"}</summary><label>Name<input value={location.name} onChange={e => change(c => { c.world.locations[i].name = e.target.value; })}/></label>{labelText("Location details", location.description, text => change(c => { c.world.locations[i].description = text; }))}<p className="game-help">Connected places are recorded by the story as you explore.</p></details>)}
        <button onClick={() => change(c => { c.world.locations.push({ id: uid(), name: "", description: "", exits: [], asset_ids: [] }); })}><Plus size={16}/> Add place</button>
      </section>}
      {tab === "scene" && <section>
        <h3>Direct the next clip</h3><p className="game-help">Your chosen controls stay attached to the next request. Leave a control on AI chooses when you want the assistant to direct it.</p>
        <label>Player viewpoint<select value={value.project.game_viewpoint || "auto"} onChange={e => change(c => {
          const view = e.target.value; if (view !== (c.project.game_viewpoint || "auto")) c.settings.transition = "cut";
          c.project.game_viewpoint = view; c.project.game_player_id = c.player_character_id;
          for (const shot of c.project.shots) {
            setDirectorValue(c.project, shot.id, "camera.height", view === "overhead" ? "overhead" : view === "pov" ? "first-person eye level" : view === "third_person" ? "eye level" : "");
            setDirectorValue(c.project, shot.id, "camera.framing", view === "pov" ? "first-person POV through the player's eyes" : view === "overhead" ? "top-down view of player and surroundings" : view === "third_person" ? "third-person view following the player" : "");
            if (view === "pov") { setDirectorValue(c.project, shot.id, "visible_subject_ids", shot.visible_subject_ids.filter(id => id !== c.player_character_id)); setDirectorValue(c.project, shot.id, "offscreen_subject_ids", [...new Set([...shot.offscreen_subject_ids, c.player_character_id])]); }
            else if (view !== "auto") { setDirectorValue(c.project, shot.id, "visible_subject_ids", [...new Set([...shot.visible_subject_ids, c.player_character_id])]); setDirectorValue(c.project, shot.id, "offscreen_subject_ids", shot.offscreen_subject_ids.filter(id => id !== c.player_character_id)); }
          }
        })}><option value="auto">AI chooses</option><option value="pov">First-person POV · my eyes</option><option value="third_person">Third person · follow my character</option><option value="overhead">View from above · top-down</option></select></label>
        <label>Connection to the current ending<select value={String(value.settings.transition || "auto")} onChange={e => setSetting("transition", e.target.value)}><option value="auto">AI chooses · explain any new shot</option><option value="continue">Continue saved motion</option><option value="cut">Cut to a new shot</option></select></label>
        {value.project.shots.map((shot, i) => <details className="game-editor-card" key={shot.id} open><summary>Shot {i + 1} · {shot.duration}s</summary><SceneDirector project={value.project} shot={shot} index={i} update={updateProject}/>{labelText("Requested action · optional", shot.action || "", text => updateProject(p => { p.shots[i].action = text; }))}<button disabled={value.project.shots.length < 2} onClick={() => updateProject(p => { p.shots.splice(i, 1); p.shots = retime(p.shots, p.duration); })}>Remove shot</button></details>)}
        <button onClick={() => updateProject(p => { const s = newShot(1); s.camera = {}; p.shots.push(s); p.shots = retime(p.shots, p.duration); })}><Plus size={16}/> Add timed shot</button>
        {labelText("Scene sound", value.project.soundscape || "", text => updateProject(p => { p.soundscape = text; }), 2)}
        {labelText("Background music", value.project.music || "", text => updateProject(p => { p.music = text; }), 2)}
      </section>}
      {tab === "render" && <section>
        <h3>Video & models</h3><label className="game-checkbox"><input type="checkbox" checked={value.settings.review_before_render} onChange={e => setSetting("review_before_render", e.target.checked)}/><span>Review before rendering</span></label>
        <div className="game-field-row"><label>Aspect ratio<select value={String(value.settings.aspect_ratio || value.project.aspect_ratio)} onChange={e => setSetting("aspect_ratio", e.target.value)}>{["16:9","9:16","1:1","4:3","3:4"].map(r => <option key={r}>{r}</option>)}</select></label><label>New action length<select value={value.settings.duration} onChange={e => setSetting("duration", Number(e.target.value))}>{(value.settings.experimental_preview ? [3,4,5,7,10,13] : [4,5,7,10,13]).map(n => <option key={n} value={n}>{n} seconds</option>)}</select></label></div>
        <div className="game-field-row"><label>Resolution<select value={value.settings.resolution} onChange={e => setSetting("resolution", e.target.value)}>{(value.settings.experimental_preview ? ["0.2","0.3","0.5","0.7","1.0"] : ["0.3","0.5","0.7","1.0"]).map(r => <option key={r} value={r}>{r} MP{r === "0.2" ? " · experimental" : ""}</option>)}</select></label><label>Steps<select value={value.settings.steps} onChange={e => setSetting("steps", Number(e.target.value))}>{[4,8,16].map(n => <option key={n} value={n}>{n} steps</option>)}</select></label></div>
        <label>Seed<input type="number" min={0} step={1} max={9007199254740991} value={Number(value.settings.seed ?? value.project.comfy_render?.seed ?? 1)} onChange={e => setSetting("seed", Number(e.target.value))}/></label>
        <label className="game-checkbox"><input type="checkbox" checked={!!value.settings.experimental_preview} onChange={e => { change(c => { c.settings.experimental_preview = e.target.checked; c.project.comfy_render = { ...c.project.comfy_render, experimental_preview: e.target.checked }; if (!e.target.checked) { if (c.settings.resolution === "0.2") c.settings.resolution = "0.3"; if (c.settings.duration < 4) c.settings.duration = 5; } }); }}/><span>Experimental 0.2 MP / 3-second previews</span></label><p className="game-help">Experimental previews test speed and control. They are not a quality guarantee. A shape change may require a new shot rather than saved motion.</p>
        <h3>LoRAs · applied in order</h3>{loras.map((lora, i) => <div key={i} className="game-editor-card"><label className="game-checkbox"><input type="checkbox" checked={lora.enabled !== false} onChange={e => setLora(i, { enabled: e.target.checked })}/><span>Enable LoRA {i + 1}</span></label><label>LoRA {i + 1} file<select value={lora.name} onChange={e => setLora(i, { name: e.target.value })}><option value="">Choose installed LoRA…</option>{[...new Set([lora.name, ...loraNames].filter(Boolean))].map(name => <option key={name}>{name}</option>)}</select></label><label>Strength<input type="number" min={-4} max={4} step={0.05} value={lora.strength} onChange={e => setLora(i, { strength: Number(e.target.value) })}/></label><div className="game-button-row"><button disabled={i === 0} onClick={() => { const next = [...loras]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; setSetting("loras", next); }}>Move up</button><button aria-label={`Remove LoRA ${i + 1}`} onClick={() => setSetting("loras", loras.filter((_, j) => i !== j))}><X size={16}/> Remove</button></div></div>)}
        <button disabled={loras.length >= 8} onClick={() => setSetting("loras", [...loras, { name: "", strength: 1, enabled: true }])}><Plus size={16}/> Add LoRA</button><p className="game-help">Installed LoRAs can belong to other model families. The server validates the chosen H3 recipe before queueing.</p>
        <h3>Prompt assistant</h3>{modelPicker || <p className="game-help">The selected Studio assistant is shared with Game.</p>}
        <label className="game-checkbox"><input type="checkbox" checked={value.settings.fast_actions !== false} onChange={e => setSetting("fast_actions", e.target.checked)}/><span>Quick item and movement actions</span></label><p className="game-help">Apply known game rules directly for items and movement. Turn this off to let characters react to every action. Conversations, combat and waiting still let the characters respond.</p>
        <label>Assistant provider<select value={String(value.settings.assistant_provider || "lmstudio")} onChange={e => setSetting("assistant_provider", e.target.value)}><option value="lmstudio">LM Studio · normal play</option><option value="supervised">Supervised test · waits for agent responses</option></select></label>
        <label>Parallel assistant requests<select value={Number(value.settings.concurrency || 1)} onChange={e => setSetting("concurrency", Number(e.target.value))}>{[1,2,4].map(n => <option key={n} value={n}>{n}{n === 1 ? " · baseline" : " · benchmark first"}</option>)}</select></label>
        <GameImageSettings settings={value.settings} generators={generators} loading={generatorsLoading} checked={generatorsChecked} errors={generatorErrors} onRefresh={onRefreshGenerators} onChange={setSetting}/>
      </section>}
    </div>
    <footer><span role="status">{saving ? "Saving…" : dirty ? "Unsaved edits · also saved before Play" : "All changes saved"}</span><button className="primary" disabled={saving || uploading} onClick={onSave}><Check size={16}/> Save changes</button>{busy && dirty && onStopApply && <button disabled={saving || uploading} onClick={onStopApply}>Stop current turn & apply changes</button>}</footer>
  </aside>;
}
