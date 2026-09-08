import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Check, ChevronDown, Clock3, Columns2, Download, Film, Layers2, LoaderCircle, Pencil, Play, RefreshCw, Settings2, Shuffle, Sparkles, Star, Video, X } from "lucide-react";
import type { Project } from "./model";
import type { ContinuationRenderPreset } from "./quickPreview";
import { loadContinuationDraft, saveContinuationDraft } from "./continuationDraft";
import "./VideoWorkspace.css";

export type VideoJob = {
  id: string;
  request_id?: string;
  project_id: string;
  story_id?: string;
  status: "preparing" | "queued" | "running" | "succeeded" | "failed" | "uncertain";
  stage?: string;
  error?: string | null;
  warning?: string | null;
  seed?: number;
  duration?: number;
  new_seconds?: number | null;
  overlap_frames?: number | null;
  width?: number;
  height?: number;
  steps?: number | null;
  frames?: number | null;
  created_at?: number;
  elapsed_seconds?: number;
  video_url?: string | null;
  scene_video_url?: string | null;
  ending_image_url?: string | null;
  download_url?: string | null;
  continuation_source?: string | null;
  parent_run_id?: string | null;
  has_snapshot?: boolean;
  operation?: "generate" | "reroll" | "continue" | "combine";
  can_reroll?: boolean;
  can_continue?: boolean;
  can_combine?: boolean;
  continue_from_run_id?: string | null;
  title?: string;
  favorite?: boolean;
};

export type VideoWorkspaceProps = {
  project: Project;
  promptReady: boolean;
  busy: boolean | string;
  jobs: VideoJob[];
  currentJob?: VideoJob | null;
  storyId?: string;
  activeEndpointId?: string;
  storyClips?: VideoJob[];
  onBranch?: (job: VideoJob) => void | Promise<void>;
  onPlayGame?: (job: VideoJob) => void | Promise<void>;
  onSelectJob: (job: VideoJob) => void;
  onGenerate: (renderPreset?: ContinuationRenderPreset) => void | Promise<void>;
  onReroll: (job: VideoJob) => void | Promise<void>;
  onContinue: (job: VideoJob, nextIdea: string, duration: number, renderPreset?: ContinuationRenderPreset, options?: { planned?: boolean }) => void | Promise<void>;
  onUpdateTake?: (job: VideoJob, patch: { title?: string; favorite?: boolean }) => void | Promise<void>;
  onSuggest?: (job: VideoJob, duration: number, direction?: string) => Promise<ContinuationSuggestions>;
  onCombine?: (job: VideoJob) => void | Promise<void>;
  onResolve?: (job: VideoJob) => void | Promise<void>;
  advanced?: ReactNode;
};

export type ContinuationSuggestions = {
  suggestions: { title: string; idea: string }[];
  ending_image_url?: string;
  model?: string;
};

type SuggestionContext = { open: boolean; projectId: string; runId?: string; duration: number; direction: string };

/** A late suggestion must never replace another take's or an edited scene's ideas. */
export function continuationSuggestionIsCurrent(request: SuggestionContext, current: SuggestionContext) {
  return request.open && current.open && request.projectId === current.projectId && request.runId === current.runId &&
    request.duration === current.duration && request.direction === current.direction;
}

export function continuationIdeaChoices(result: ContinuationSuggestions) {
  const seen = new Set<string>();
  return (Array.isArray(result?.suggestions) ? result.suggestions : []).filter(item => {
    if (typeof item?.title !== "string" || typeof item?.idea !== "string" || !item.title.trim() || !item.idea.trim()) return false;
    const idea = item.idea.trim();
    if (seen.has(idea)) return false;
    seen.add(idea); return true;
  }).slice(0, 3).map(item => ({ title: item.title.trim(), idea: item.idea.trim() }));
}

const labels: Record<VideoJob["status"], string> = {
  preparing: "Preparing your video", queued: "Waiting for ComfyUI", running: "Rendering your video",
  succeeded: "Ready to watch", failed: "Generation stopped", uncertain: "Checking the previous request",
};

export function videoJobIsPending(job?: VideoJob | null) {
  return !!job && ["preparing", "queued", "running", "uncertain"].includes(job.status);
}

export function elapsedVideoTime(seconds?: number) {
  if (!Number.isFinite(seconds) || seconds! < 0) return null;
  const total = Math.floor(seconds!);
  return total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`;
}

export function videoTakeTitle(job: VideoJob, number?: number) {
  return job.title?.trim() || (number ? `Take ${number}` : "Selected take");
}

export function continuationSizeLabel(job?: VideoJob | null) {
  const width = job?.width, height = job?.height;
  const known = Number.isInteger(width) && Number.isInteger(height) && width! > 0 && height! > 0;
  const pixels = known ? width! * height! : 0;
  return {
    presetSize: known && pixels >= 260000 && pixels <= 350000 ? "0.3 MP" : "source size",
    explanation: known ? `Continuation keeps this video’s ${width} × ${height} size. Preview presets change steps.` : "Continuation keeps the saved video’s size. Preview presets change steps.",
  };
}

export function videoComparisonChoices(projectId: string, jobs: VideoJob[], selected?: VideoJob | null) {
  if (!selected || selected.project_id !== projectId || selected.status !== "succeeded" || !selected.video_url || selected.operation === "combine") return [];
  const seen = new Set<string>();
  return jobs.filter(job => job.project_id === projectId && job.id !== selected.id && job.status === "succeeded" && !!job.video_url &&
    job.operation !== "combine" && !seen.has(job.id) && !!seen.add(job.id));
}

export function sceneVideoUrl(job?: VideoJob | null) {
  return job?.scene_video_url || job?.video_url || '';
}

/** The caller supplies the accepted clips in story order; no join request is needed. */
export function storyPlaylist(clips: VideoJob[] = [], storyId?: string) {
  const seen = new Set<string>();
  return clips.filter(job => job.status === 'succeeded' && sceneVideoUrl(job) && job.operation !== 'combine' &&
    (!storyId || !job.story_id || job.story_id === storyId) && !seen.has(job.id) && !!seen.add(job.id));
}

export function continuationIsPlanned(idea: string, selectedIdea: string, suggestions?: ContinuationSuggestions | null) {
  return !!selectedIdea && idea === selectedIdea && !!suggestions?.suggestions.some(choice => choice.idea === selectedIdea);
}

/** Every visible result and action belongs to the current project. */
export function videoWorkspaceState(projectId: string, jobs: VideoJob[] = [], selected?: VideoJob | null, busy: boolean | string = false,
  story: {storyId?: string; activeEndpointId?: string} = {}) {
  const seen = new Set<string>();
  const inScope = (job: VideoJob) => story.storyId ? !job.story_id || job.story_id === story.storyId : job.project_id === projectId;
  const takes = jobs.filter(job => job && inScope(job) && job.id && !seen.has(job.id) && !!seen.add(job.id));
  const current = selected && inScope(selected) ? (takes.find(job => job.id === selected.id) || (!story.storyId ? selected : null)) : null;
  // Never silently continue a history preview, or substitute another take for a missing endpoint.
  const source = story.activeEndpointId !== undefined ? takes.find(job => job.id === story.activeEndpointId) || null : story.storyId ? null : current;
  const pending = takes.find(videoJobIsPending) || (videoJobIsPending(current) ? current : null);
  const working = Boolean(busy || pending);
  const playable = current?.status === "succeeded" && !!current.video_url;
  const continuationChain = !!current?.parent_run_id && !!current?.continuation_source &&
    (current.can_combine === true || (current.can_combine === undefined && current.operation === "continue"));
  const verifiedCombinedEnding=source?.operation==='combine'&&source.can_continue===true&&
    typeof source.continue_from_run_id==='string'&&!!source.continue_from_run_id.trim()&&source.continue_from_run_id!==source.id;
  return { takes, current, source, pending, working, playable,
    showCombine: current?.operation === "combine" || continuationChain,
    canReroll: !!playable && !working && current?.has_snapshot !== false && current?.operation !== "combine" && current?.can_reroll !== false,
    canContinue: source?.status === 'succeeded' && !!source.video_url && !working && source.can_continue !== false && (verifiedCombinedEnding ||
      (!!source.continuation_source && source.has_snapshot !== false && source.operation !== "combine")),
    canCombine: !!playable && !working && continuationChain && current?.operation !== "combine" };
}

export default function VideoWorkspace({ project, promptReady, busy, jobs, currentJob, storyId, activeEndpointId, storyClips, onBranch, onPlayGame, onSelectJob, onGenerate, onReroll, onContinue, onSuggest, onCombine, onResolve, onUpdateTake, advanced }: VideoWorkspaceProps) {
  const id = useId();
  const [continuing, setContinuing] = useState(false), [idea, setIdea] = useState(""), [length, setLength] = useState(5);
  const [renderPreset, setRenderPreset] = useState<ContinuationRenderPreset>("inherit");
  const [generatePreset, setGeneratePreset] = useState<ContinuationRenderPreset>("inherit");
  const [draftScope, setDraftScope] = useState(""), [restoredDraft, setRestoredDraft] = useState(false);
  const [renaming, setRenaming] = useState(false), [takeTitle, setTakeTitle] = useState("");
  const [metadataBusy, setMetadataBusy] = useState(false), [metadataError, setMetadataError] = useState("");
  const metadataInFlight = useRef(false), metadataScope = useRef("");
  const [comparing, setComparing] = useState(false), [compareId, setCompareId] = useState("");
  const [submitting, setSubmitting] = useState(false), [actionError, setActionError] = useState("");
  const [suggested, setSuggested] = useState<ContinuationSuggestions | null>(null);
  const [suggesting, setSuggesting] = useState(false), [suggestionError, setSuggestionError] = useState("");
  const [selectedIdea, setSelectedIdea] = useState('');
  const [playback, setPlayback] = useState<'scene' | 'story'>('scene'), [playlistIndex, setPlaylistIndex] = useState(0);
  const [mediaLength, setMediaLength] = useState<{id:string;seconds:number}|null>(null);
  const autoplayNext = useRef(false), directionRef = useRef<HTMLTextAreaElement>(null);
  const suggestionRevision = useRef(0);
  const suggestionInFlight = useRef<number | null>(null);
  const state = videoWorkspaceState(project.id, jobs, currentJob, busy || submitting || suggesting, {storyId, activeEndpointId});
  const { current, source, pending, takes, working, playable, canReroll, canContinue, canCombine, showCombine } = state;
  const hasResult = takes.some(job => job.status === 'succeeded' && !!job.video_url);
  const playlist = storyPlaylist(storyClips, storyId);
  const playing = playback === 'story' ? playlist[Math.min(playlistIndex, Math.max(0, playlist.length - 1))] : current;
  const playingUrl = sceneVideoUrl(playing);
  const endingUrl = source?.ending_image_url || (draftScope === `${source?.project_id}:${source?.id}` ? suggested?.ending_image_url : undefined);
  const sourceProjectId = source?.project_id || project.id;
  const status = pending || current;
  const measuredTime = elapsedVideoTime(status?.elapsed_seconds);
  const runDuration = Number((mediaLength && mediaLength.id===current?.id ? mediaLength.seconds : current?.new_seconds ?? current?.duration ?? project.duration).toFixed(2));
  const dimensions = current?.width && current?.height ? `${current.width} × ${current.height}` : `${project.comfy_render?.resolution || "0.3"} MP`;
  const seed = current?.seed;
  const continuationSize = continuationSizeLabel(source);
  const generationPresetSize = project.comfy_render?.continuation_source ? "source size" : "0.3 MP";
  metadataScope.current = `${project.id}:${current?.id || ""}`;
  const takeNumber = (job: VideoJob) => Math.max(1, takes.length - takes.findIndex(take => take.id === job.id));
  const compareChoices = videoComparisonChoices(project.id, takes, current);
  const compareTake = compareChoices.find(job => job.id === compareId) || compareChoices[0];
  const updateTake = async (patch: { title?: string; favorite?: boolean }) => {
    if (!onUpdateTake || !current || metadataInFlight.current) return;
    const scope = metadataScope.current;
    metadataInFlight.current = true; setMetadataBusy(true); setMetadataError("");
    try { await onUpdateTake(current, patch); if (scope === metadataScope.current && patch.title !== undefined) setRenaming(false); }
    catch (error) { if (scope === metadataScope.current) setMetadataError(error instanceof Error ? error.message : "Could not save this take. Try again."); }
    finally { metadataInFlight.current = false; setMetadataBusy(false); }
  };
  const suggestionContext = useRef<SuggestionContext>({ open: continuing, projectId: sourceProjectId, runId: source?.id, duration: length, direction: idea });
  suggestionContext.current = { open: continuing, projectId: sourceProjectId, runId: source?.id, duration: length, direction: idea };
  const invalidateSuggestions = () => { suggestionRevision.current += 1; };
  const requestSuggestions = async (duration = length, direction = idea) => {
    if (!onSuggest || !source || working || suggestionInFlight.current !== null) return;
    const revision = ++suggestionRevision.current;
    suggestionInFlight.current = revision;
    const context = { open: true, projectId: sourceProjectId, runId: source.id, duration, direction };
    suggestionContext.current = context;
    setSuggesting(true); setSuggestionError("");
    try {
      const result = await onSuggest(source, duration, direction.trim() || undefined);
      if (revision !== suggestionRevision.current || !continuationSuggestionIsCurrent(context, suggestionContext.current)) return;
      const suggestions = continuationIdeaChoices(result);
      setSuggested({ ...result, suggestions });
      setSelectedIdea('');
      setRestoredDraft(false);
      if (!suggestions.length) setSuggestionError("No scene ideas came back. Refresh ideas or write what happens next below.");
    } catch (error) {
      if (revision === suggestionRevision.current && continuationSuggestionIsCurrent(context, suggestionContext.current))
        setSuggestionError(error instanceof Error ? error.message : "Scene suggestions are unavailable. You can still write your own idea below.");
    } finally {
      if (suggestionInFlight.current === revision) { suggestionInFlight.current = null; setSuggesting(false); }
    }
  };
  const changeIdea = (value: string) => {
    invalidateSuggestions(); suggestionContext.current = { ...suggestionContext.current, direction: value };
    setIdea(value); setSuggestionError("");
  };
  const runAction = async (action: () => void | Promise<void>) => {
    if (working) return;
    setActionError(""); setSubmitting(true);
    try { await action(); } catch (error) { setActionError(error instanceof Error ? error.message : "This action did not finish. Check the connection and try again."); }
    finally { setSubmitting(false); }
  };
  useEffect(() => {
    suggestionRevision.current += 1;
    const restored = source?.id ? loadContinuationDraft(sourceProjectId, source.id) : null;
    setContinuing(false); setIdea(restored?.idea || ""); setLength(Math.min(13, restored?.duration || 5)); setRenderPreset(restored?.renderPreset || "inherit"); setActionError("");
    setSelectedIdea('');
    setSuggested(restored?.suggestions || null); setSuggestionError(""); setRestoredDraft(!!restored);
    setDraftScope(`${sourceProjectId}:${source?.id || ""}`);
  }, [sourceProjectId, source?.id]);
  useEffect(() => {
    if (!source?.id || draftScope !== `${sourceProjectId}:${source.id}` || (!continuing && !idea && !suggested)) return;
    saveContinuationDraft(sourceProjectId, source.id, { idea, duration: length, renderPreset, ...(suggested ? { suggestions: suggested } : {}) });
  }, [draftScope, sourceProjectId, source?.id, continuing, idea, length, renderPreset, suggested]);
  useEffect(() => {
    setRenaming(false); setMetadataError(''); setComparing(false); setCompareId(''); setPlayback('scene'); autoplayNext.current = false;
  }, [current?.id]);
  useEffect(() => { setPlaylistIndex(0); autoplayNext.current = false; }, [storyId]);
  useEffect(() => {
    if (!continuing) return;
    directionRef.current?.focus({preventScroll:true});
    directionRef.current?.closest('.video-workspace-continue')?.scrollIntoView({behavior:'smooth',block:'nearest'});
  }, [continuing]);
  useEffect(() => { setGeneratePreset("inherit"); }, [project.id]);
  useEffect(() => () => { suggestionRevision.current += 1; }, []);

  return <section className="video-workspace" aria-label="Video workspace">
    <header className="video-workspace-heading">
      <div><span className="video-workspace-eyebrow"><Video size={14} aria-hidden="true" /> YOUR VIDEO</span><h3>Make it move.</h3></div>
      <span className="video-workspace-mode">{project.mode?.toUpperCase()}</span>
    </header>

    {current && <div className="video-workspace-take-toolbar">
      <strong className="video-workspace-current-title">{videoTakeTitle(current, takeNumber(current))}</strong>
      <div>{onUpdateTake && <><button type="button" aria-label="Rename selected take" disabled={metadataBusy} onClick={() => { setTakeTitle(current.title || ""); setRenaming(true); setMetadataError(""); }}><Pencil size={14} aria-hidden="true" /></button><button type="button" aria-label={current.favorite ? "Remove take from favorites" : "Favorite this take"} aria-pressed={!!current.favorite} disabled={metadataBusy} onClick={() => void updateTake({ favorite: !current.favorite })}><Star size={15} fill={current.favorite ? "currentColor" : "none"} aria-hidden="true" /></button></>}
      {!!compareChoices.length && <button type="button" aria-expanded={comparing} onClick={() => setComparing(value => !value)}><Columns2 size={14} aria-hidden="true" /> Compare takes</button>}</div>
    </div>}
    {renaming && current && <form className="video-workspace-rename" onSubmit={event => { event.preventDefault(); void updateTake({ title: takeTitle.trim() }); }}><label htmlFor={`${id}-take-title`}>Take name</label><input id={`${id}-take-title`} value={takeTitle} maxLength={80} placeholder={`Take ${takeNumber(current)}`} onChange={event => setTakeTitle(event.target.value)} disabled={metadataBusy} autoFocus /><button type="submit" disabled={metadataBusy}>Save name</button><button type="button" aria-label="Cancel rename" disabled={metadataBusy} onClick={() => setRenaming(false)}><X size={15} aria-hidden="true" /></button></form>}
    {metadataError && <p className="video-workspace-error" role="alert">{metadataError}</p>}

    {!!playlist.length && <div className="video-workspace-playback-tabs" role="group" aria-label="Playback view">
      <button type="button" aria-pressed={playback === 'scene' && (!source || current?.id === source.id)} onClick={() => {setPlayback('scene'); autoplayNext.current = false; if(source)onSelectJob(source);}}>Latest scene</button>
      <button type="button" aria-pressed={playback === 'story'} onClick={() => {setPlayback('story'); setPlaylistIndex(0); autoplayNext.current = false;}}>Whole story · {playlist.length} scenes</button>
      {storyId && <a href={`/api/stories/${storyId}/video`} download>Save whole story</a>}
    </div>}
    {playback === 'story' && playing && <p className="video-workspace-help" role="status">Scene {Math.min(playlistIndex + 1, playlist.length)} of {playlist.length} · {videoTakeTitle(playing)}. Plays the accepted clips in order.</p>}
    <div className={`video-workspace-preview ${playing?.status === 'succeeded' && playingUrl ? "has-video" : ""}`}>
      {playing?.status === 'succeeded' && playingUrl ? <video key={`${playing.id}:${playingUrl}`} src={playingUrl} controls playsInline preload="metadata" aria-label="Selected video"
        onEnded={() => {if(playback === 'story' && playlistIndex < playlist.length - 1) {autoplayNext.current = true; setPlaylistIndex(index => index + 1);} else autoplayNext.current = false;}}
        onLoadedMetadata={event => {if(Number.isFinite(event.currentTarget.duration))setMediaLength({id:playing.id,seconds:event.currentTarget.duration});if(autoplayNext.current) {autoplayNext.current = false; void event.currentTarget.play().catch(() => {});}}} /> :
        <div className="video-workspace-empty">
          <div className="video-workspace-preview-icon">{pending && pending.status !== "uncertain" ? <LoaderCircle className="video-workspace-spin" size={30} aria-hidden="true" /> : <Film size={30} aria-hidden="true" />}</div>
          <strong>{status ? labels[status.status] : "Your next video starts here"}</strong>
          <p>{pending ? "You can keep this page open. Your result will appear here when it is ready." : current?.status === "failed" ? "Your story and references are saved. Review the message below before generating again." : "Write your idea, add your photos, then generate a take."}</p>
        </div>}
    </div>

    <div className="video-workspace-actions">
      {hasResult && <><button className="video-workspace-generate" type="button" disabled={!canContinue} onClick={() => {
        setContinuing(true); setActionError('');
        if (!continuing && !suggested?.suggestions.length) void requestSuggestions();
      }} title="Continue the active story ending. Previewing history does not change this source."><ArrowRight size={17} aria-hidden="true" /><span>Continue from this ending</span></button>
      <button type="button" disabled={!canReroll} onClick={() => current && void runAction(() => onReroll(current))} title="Keep this take's prompt, photos and settings, and render with a new seed."><Shuffle size={17} aria-hidden="true" /><span>Try another take</span></button></>}
      <button className={!hasResult ? 'video-workspace-generate' : ''} type="button" disabled={working} onClick={() => void runAction(() => onGenerate(generatePreset))}><Play size={17} fill="currentColor" aria-hidden="true" /><span>Generate video</span></button>
    </div>
    {source && <div className="video-workspace-source" aria-label="Continuation source">
      {endingUrl && <img src={endingUrl} alt="Ending frame used for continuation" />}
      <div><strong>Continuing after {videoTakeTitle(source, takeNumber(source))}</strong><span>{source.id !== current?.id ? 'You are previewing history. Your story still continues from this ending.' : 'The saved ending and motion carry into the next scene automatically.'}</span></div>
      {onPlayGame && <button type="button" disabled={!canContinue} onClick={() => void runAction(() => onPlayGame(source))}>Play from here</button>}
    </div>}
    {storyId && !source && <p className="video-workspace-help">{activeEndpointId ? 'Loading the saved story ending. Continuation will be ready when it is available.' : 'Generate your opening scene to start this story.'}</p>}
    {onBranch && current && current.id !== source?.id && <button className="video-workspace-branch" type="button"
      disabled={working || !videoWorkspaceState(current.project_id, [current], current).canContinue}
      onClick={() => void runAction(() => onBranch(current))}>Branch from this preview</button>}

    {continuing && source && <div className="video-workspace-continue" role="region" aria-label="Continue selected video">
      <div className="video-workspace-continue-heading"><div><span className="video-workspace-eyebrow"><Sparkles size={13} aria-hidden="true" /> INTERACTIVE STORY</span><h4>What happens after this ending?</h4></div><button type="button" aria-label="Cancel continuation" className="video-workspace-close" onClick={() => {
        invalidateSuggestions(); suggestionContext.current = { ...suggestionContext.current, open: false }; setContinuing(false);
      }} disabled={submitting}><X size={17} aria-hidden="true" /></button></div>
      <p>Pick a next scene, edit it if you want, then watch the story continue. The last frame and story come from this video automatically. Your original take stays saved.</p>
      {restoredDraft && <p className="video-workspace-settings-note" role="status">Saved scene draft restored. Refresh ideas for new choices.</p>}
      {endingUrl && <figure className="video-workspace-ending"><img src={endingUrl} alt="Actual last frame of the selected video" /><figcaption><strong>Your starting point</strong><span>This ending frame is included automatically when planning the next clip.</span></figcaption></figure>}
      {onSuggest && <div className="video-workspace-suggestions" aria-label="Suggested next scenes">
        <div className="video-workspace-suggestions-heading"><strong>Ideas for the next {length} seconds</strong><button type="button" disabled={working || suggesting} onClick={() => void requestSuggestions()}><RefreshCw size={13} aria-hidden="true" /> Refresh ideas</button></div>
        {suggesting && <p className="video-workspace-suggestion-status" role="status"><LoaderCircle size={14} className="video-workspace-spin" aria-hidden="true" /> Looking at the ending and thinking of next scenes…</p>}
        {suggestionError && <p className="video-workspace-suggestion-error" role="alert">{suggestionError}</p>}
        {!!suggested?.suggestions.length && <div className="video-workspace-idea-grid">{suggested.suggestions.map((choice, index) => <button type="button" key={`${index}:${choice.idea}`} disabled={working} aria-pressed={idea === choice.idea && selectedIdea === choice.idea} onClick={() => { setSelectedIdea(choice.idea); changeIdea(choice.idea); }}><span className="video-workspace-idea-number">{index + 1}</span><strong>{choice.title}</strong><span>{choice.idea}</span></button>)}</div>}
        {suggested?.model && <small className="video-workspace-suggestion-model">Ideas by {suggested.model}</small>}
      </div>}
      <label htmlFor={`${id}-idea`}>What happens next?</label>
      <textarea ref={directionRef} id={`${id}-idea`} value={idea} onChange={event => changeIdea(event.target.value)} rows={3} maxLength={1000} disabled={working && !suggesting} placeholder="She opens the box, smiles, and turns toward the window. Keep the same room and one continuous shot." />
      <label className="video-workspace-preset" htmlFor={`${id}-continue-preset`}><span>Continuation quality<small>Quick draft checks motion. Quality preview adds detail.</small></span><select id={`${id}-continue-preset`} aria-label="Continuation quality" value={renderPreset} onChange={event => setRenderPreset(event.target.value as ContinuationRenderPreset)} disabled={working}><option value="inherit">This take’s settings</option><option value="draft">Quick draft · {continuationSize.presetSize} / 4 steps</option><option value="quality">Quality preview · {continuationSize.presetSize} / 8 steps</option></select></label>
      <p className="video-workspace-settings-note">{continuationSize.explanation}</p>
      <p className="video-workspace-settings-note">Uses this take’s saved video settings unless you choose a preview preset. Keeps the clip length you select.</p>
      <div className="video-workspace-continue-footer"><label htmlFor={`${id}-length`}><span>New action length</span><select id={`${id}-length`} aria-label="Next clip length" value={length} onChange={event => {
        const duration = Number(event.target.value); invalidateSuggestions(); setLength(duration); setSelectedIdea('');
        suggestionContext.current = { ...suggestionContext.current, duration };
        const restored = loadContinuationDraft(sourceProjectId, source.id, { duration });
        if (restored) {
          setIdea(restored.idea); setRenderPreset(restored.renderPreset); setSuggested(restored.suggestions || null); setRestoredDraft(true);
          suggestionContext.current = { ...suggestionContext.current, direction: restored.idea };
          return;
        }
        setSuggested(value => value ? { ...value, suggestions: [] } : null);
        setRestoredDraft(false);
        void requestSuggestions(duration);
      }} disabled={working}>{[4, 5, 7, 10, 13].map(seconds => <option key={seconds} value={seconds}>{seconds === 4 ? "4 seconds · quick test" : `${seconds} seconds of new action`}</option>)}</select></label><button className="video-workspace-generate" type="button" disabled={!canContinue || !idea.trim()} onClick={() => {
        invalidateSuggestions(); void runAction(() => onContinue(source, idea.trim(), length, renderPreset, {planned:continuationIsPlanned(idea, selectedIdea, suggested)}));
      }}><ArrowRight size={16} aria-hidden="true" /> Generate next {length} seconds</button></div>
      <small>{continuationIsPlanned(idea, selectedIdea, suggested) ? "Your selected suggestion is ready to render." : "AI develops your direction into actions and dialogue before rendering."} This length adds new action after the saved opening. H3 rounds to supported frames; Latest scene skips the copied opening.</small>
    </div>}


    {comparing && current && compareTake && <section className="video-workspace-compare" aria-label="Compare selected takes">
      <div className="video-workspace-compare-heading"><label htmlFor={`${id}-compare`}>Compare with<select id={`${id}-compare`} aria-label="Compare with" value={compareTake.id} onChange={event => setCompareId(event.target.value)}>{compareChoices.map(job => <option key={job.id} value={job.id}>{videoTakeTitle(job, takeNumber(job))}</option>)}</select></label><button type="button" aria-label="Close comparison" onClick={() => setComparing(false)}><X size={16} aria-hidden="true" /></button></div>
      <div className="video-workspace-compare-grid">{[current, compareTake].map((job, index) => <figure key={`${index}:${job.id}`}><video src={job.video_url!} controls playsInline preload="metadata" aria-label={index === 0 ? "Selected take comparison" : "Other take comparison"} /><figcaption><strong>{videoTakeTitle(job, takeNumber(job))}</strong><span>{job.steps && job.steps > 0 ? `${job.steps} steps · ` : ""}{Number.isSafeInteger(job.seed) ? `Seed ${job.seed}` : ""}</span></figcaption></figure>)}</div>
      <p>Play either take to compare the action and detail. Your selected take stays the same.</p>
    </section>}

    <div className="video-workspace-result-info">
      <div className="video-workspace-badges" aria-label="Video details">{current?.operation === "combine" && <span>Combined film</span>}<span>{runDuration}s</span><span>{dimensions}</span>{current?.steps != null && current.steps > 0 && <span>{current.steps} steps</span>}{Number.isSafeInteger(seed) && <span>Seed {seed}</span>}</div>
      {playable && current?.download_url && <a className="video-workspace-download" href={current.scene_video_url || current.download_url} download><Download size={14} aria-hidden="true" /> Save scene</a>}
    </div>

    {(status || busy || submitting) && <div className={`video-workspace-status ${status?.status === "failed" || status?.status === "uncertain" ? "needs-attention" : ""}`} role="status" aria-live="polite">
      {pending || busy || submitting ? <LoaderCircle size={15} className={status?.status === "uncertain" ? "" : "video-workspace-spin"} aria-hidden="true" /> : status?.status === "succeeded" ? <Check size={15} aria-hidden="true" /> : <RefreshCw size={15} aria-hidden="true" />}
      <div><strong>{busy && !pending ? (typeof busy === "string" ? busy : "Preparing your video") : submitting && !pending ? "Starting your request" : status ? labels[status.status] : "Preparing your video"}</strong>
        {status?.stage && <span>{status.stage}</span>}
        {status?.status === "uncertain" && <span>Studio is checking whether ComfyUI received the request before allowing another generation.</span>}
      </div>
      {measuredTime && <span className="video-workspace-elapsed"><Clock3 size={12} aria-hidden="true" /> {measuredTime}</span>}
    </div>}
    {(actionError || current?.error || pending?.error) && <p className="video-workspace-error" role="alert">{actionError || pending?.error || current?.error}</p>}
    {current?.warning && <p className="video-workspace-help" role="status">{current.warning}</p>}
    {status?.status==='uncertain' && onResolve && <div className="video-workspace-combine"><button type="button" disabled={!!busy||submitting} onClick={async()=>{
      setSubmitting(true);setActionError('');
      try{await onResolve(status);}catch(error){setActionError((error as Error).message);}finally{setSubmitting(false);}
    }}><RefreshCw size={15}/> Check &amp; unlock</button><span>Recover its result if available. If this request is no longer running, unlock new generations. Existing videos are kept.</span></div>}

    <label className="video-workspace-preset" htmlFor={`${id}-generate-preset`}><span>Video quality<small>{project.comfy_render?.continuation_source ? "Uses the saved clip size; preview presets change steps." : "Applies to Generate video."}</small></span><select id={`${id}-generate-preset`} aria-label="Video quality" value={generatePreset} onChange={event => setGeneratePreset(event.target.value as ContinuationRenderPreset)} disabled={working}><option value="inherit">Current video settings</option><option value="draft">Quick draft · {generationPresetSize} / 4 steps</option><option value="quality">Quality preview · {generationPresetSize} / 8 steps</option></select></label>
    {onCombine && showCombine && <div className="video-workspace-combine"><button type="button" disabled={!canCombine} onClick={() => current && void runAction(() => onCombine(current))}><Layers2 size={15} aria-hidden="true" /> Combine clips</button><span>{current?.operation === "combine" ? current.can_continue===true&&current.continue_from_run_id ? "Your joined film is ready in the player. Continue from this ending picks up from the final clip automatically." : "Your joined film is ready in the player. Select an individual take to continue its story." : "Join this continuation with its earlier clips into one video."}</span></div>}
    <p className="video-workspace-help">{!promptReady ? "Generate makes your prompt first, then renders your video." : "Generate uses your current prompt and settings."} {playable ? "Try another take keeps the selected take’s prompt, photos and settings." : "After a take finishes, compare a new seed or continue its story."}</p>
    {playable && !current?.continuation_source && !current?.warning && current?.operation !== "combine" && <p className="video-workspace-help">This take has no saved motion state. Enable Save continuation state in generation settings for videos you want to extend.</p>}

    {takes.length > 0 && <div className="video-workspace-takes"><div className="video-workspace-takes-heading"><h4>Recent takes</h4><span>{takes.length} saved {takes.length === 1 ? "run" : "runs"}</span></div><div className="video-workspace-takes-strip" aria-label="Recent video takes">
      {takes.map((job, index) => <button type="button" key={job.id} className={`video-workspace-take ${current?.id === job.id ? "selected" : ""}`} aria-label={`Select take ${takes.length - index}`} aria-pressed={current?.id === job.id} onClick={() => onSelectJob(job)}>
        <span className={`video-workspace-take-icon ${job.status}`}><span>{job.favorite ? <Star size={16} fill="currentColor" aria-label="Favorite take" /> : job.status === "succeeded" ? <Play size={16} aria-hidden="true" /> : videoJobIsPending(job) ? <Clock3 size={16} aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}</span><strong>{videoTakeTitle(job, takes.length - index)}</strong>{current?.id === job.id && <Check size={13} aria-hidden="true" />}</span>
        <span className="video-workspace-take-status">{job.status === "succeeded" ? job.operation === "combine" ? "Combined film" : "Ready" : labels[job.status]}</span><small>{job.operation === "combine" ? "Joined video" : Number.isSafeInteger(job.seed) ? `Seed ${job.seed}` : "Seed pending"}{job.duration ? ` · ${Number(job.duration.toFixed(2))}s` : ""}</small>
      </button>)}
    </div></div>}

    {playable && current?.video_url && <details className="video-workspace-raw-details"><summary>Clip details & original output</summary>
      <p>{current.scene_video_url ? 'Scene playback uses the new footage. The original output can include preserved motion from the previous clip.' : 'This is the full generated clip. Saved-state continuations can include a short preserved opening from the previous clip.'}</p>
      <a href={current.video_url} target="_blank" rel="noreferrer">View original generated clip</a>
      {current.parent_run_id && <p>The parent take is saved with this clip. Branches keep their own source ending.</p>}
    </details>}
    {advanced && <details className="video-workspace-advanced"><summary><Settings2 size={15} aria-hidden="true" /><span>Generation settings</span><ChevronDown size={15} aria-hidden="true" /></summary><div>{advanced}</div></details>}
  </section>;
}
