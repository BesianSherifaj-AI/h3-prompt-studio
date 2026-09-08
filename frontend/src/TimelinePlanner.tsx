import { useEffect, useState } from "react";
import type { Project } from "./model";
import { setDirectorValue } from "./shotDirections";
import {
  continuationEnding, createContinuation, formatTime, setContinuousTake,
  setTimelineBoundary, splitSceneAt, splitTimelineAt, timelineRanges,
  twoScenesInFirstFiveSeconds,
} from "./timelineHelpers";
import "./TimelinePlanner.css";

type Update = (fn: (draft: Project) => void) => void;
type TimelineProps = { project: Project; update: Update; checkpointUpdate?: Update };

function BoundaryInput({ value, min, max, label, onApply }: {
  value: number; min: number; max: number; label: string; onApply: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const apply = () => {
    if (draft !== "" && Number(draft) !== value) onApply(Number(draft));
    setDraft(String(value));
  };
  return <input type="number" min={min} max={max} step={0.25} aria-label={label}
    value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={apply}
    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }} />;
}

export function TimelinePlanner({ project: p, update, checkpointUpdate }: TimelineProps) {
  const change = checkpointUpdate || update;
  const ranges = timelineRanges(p);
  const [splitAt, setSplitAt] = useState("2.5");
  const [transition, setTransition] = useState<"cut" | "continuous">("cut");
  const [error, setError] = useState("");
  useEffect(() => { setError(""); setSplitAt(String(Math.min(2.5, p.duration / 2))); }, [p.id]);
  const apply = (fn: (draft: Project) => void) => {
    try {
      // Validate before the React state updater so an invalid entry stays inline.
      fn(structuredClone(p));
      change(fn);
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const continuous = p.shots.every((shot, i) => i === 0 || shot.transition === "continuous");
  return <details className="timeline-planner">
    <summary>Timing & continuous filming <span>0–{formatTime(p.duration)} seconds</span></summary>
    <p className="timeline-help">Choose when each scene starts and ends. Two scenes can fit inside the first five seconds. A continuous take can have several timed actions without a cut.</p>
    <div className="timeline-track" aria-label="Scene timeline">
      {ranges.map((range) => <div key={range.id} className="timeline-segment" style={{ flexGrow: range.duration }}>
        <strong>Scene {range.index + 1}</strong><span>{formatTime(range.start)}–{formatTime(range.end)}s</span>
      </div>)}
    </div>
    <div className="timeline-shortcuts">
      <button type="button" onClick={() => apply(setContinuousTake)}>One continuous take · no cuts</button>
      {p.shots.length === 1 && <button type="button" onClick={() => apply(twoScenesInFirstFiveSeconds)}>2 scenes in the first {Math.min(5, p.duration)} seconds</button>}
    </div>
    {continuous && p.shots.length > 1 && <p className="timeline-state">The camera keeps filming across these scene beats. Your dialogue and camera choices stay in place.</p>}
    <div className="timeline-rows">
      {ranges.map((range, i) => <div className="timeline-row" key={range.id}>
        <strong>Scene {i + 1}</strong>
        <label><span>Starts at</span><output>{formatTime(range.start)}s</output></label>
        <label><span>Ends at</span>{i < ranges.length - 1
          ? <BoundaryInput label={`Scene ${i + 1} ends at`} value={range.end} min={range.start + 0.25} max={ranges[i + 1].end - 0.25}
            onApply={(value) => apply((d) => setTimelineBoundary(d, ranges[i + 1].id, value))} />
          : <output>{formatTime(range.end)}s</output>}</label>
        {i > 0 ? <label className="timeline-cut"><span>From previous scene</span><select aria-label={`Timeline scene ${i + 1} change`}
          value={p.shots[i].transition === "continuous" ? "continuous" : "cut"}
          onChange={(e) => apply((d) => { setDirectorValue(d, range.id, "transition", e.target.value); if (e.target.value === "cut") (d.simple ??= {}).continuous_take = false; })}>
          <option value="cut">Cut to this scene</option><option value="continuous">Keep filming · no cut</option>
        </select></label> : <span className="timeline-first">The video starts here</span>}
        <button type="button" disabled={p.shots.length >= 6 || range.duration < 0.5} aria-label={`Split scene ${i + 1} in two`}
          onClick={() => apply((d) => splitSceneAt(d, range.id, (range.start + range.end) / 2, transition))}>Split in two</button>
      </div>)}
    </div>
    <div className="timeline-split">
      <label><span>Add a scene at (seconds)</span><input type="number" aria-label="Add scene at seconds" min={0.25} max={p.duration - 0.25} step={0.25} value={splitAt} onChange={(e) => setSplitAt(e.target.value)} /></label>
      <label><span>How it starts</span><select aria-label="New scene transition" value={transition} onChange={(e) => setTransition(e.target.value as "cut" | "continuous")}>
        <option value="cut">Cut to a new shot</option><option value="continuous">Keep filming · new action</option>
      </select></label>
      <button type="button" disabled={!splitAt || p.shots.length >= 6} onClick={() => apply((d) => splitTimelineAt(d, Number(splitAt), transition))}>Add scene here</button>
    </div>
    <p className="timeline-help">Changing an end time adjusts only the next scene. Splitting keeps spoken lines in the original scene; add the next action in its new card below. Each clip is up to 15 seconds.</p>
    {error && <p className="timeline-error" role="alert">{error}</p>}
  </details>;
}

type ContinuationProps = { project: Project; update: Update; onContinue: (next: Project) => void; busy?: boolean };
export function ContinuationPlanner({ project: p, update, onContinue, busy = false }: ContinuationProps) {
  const saved = p.simple?.next_clip_draft || {};
  const request = saved.request || "";
  const ending = saved.ending ?? continuationEnding(p);
  const duration = Number(saved.duration || 15);
  const firstFrameAssetId = saved.first_frame_asset_id || "";
  const [error, setError] = useState("");
  const priorStart = Number(p.simple?.continuation?.sequence_start || 0);
  const nextStart = priorStart + p.duration;
  const images = p.assets.filter((asset) => asset.media_type === "image");
  const set = (key: string, value: string | number) => update((d) => { ((d.simple ??= {}).next_clip_draft ??= {})[key] = value; });
  useEffect(() => setError(""), [p.id]);
  return <details className="timeline-planner continuation-planner">
    <summary>Plan a separate scene <span>next {duration} seconds</span></summary>
    {p.simple?.continuation && <p className="timeline-state">This is clip {p.simple.continuation.segment_index}, covering {formatTime(priorStart)}–{formatTime(priorStart + p.duration)}s of your longer story.</p>}
    <p className="timeline-help">Keep this project and start a separate next clip with the same people, clothes, photo tags and style. Then use “Make my prompt” to develop what happens next.</p>
    <div className="continuation-fields">
      <label><span>Where does this clip finish?</span><textarea aria-label="Previous clip ending" rows={2} value={ending} placeholder="e.g. Nora now holds the box; both women stand by the door" onChange={(e) => set("ending", e.target.value)} /></label>
      <label><span>What happens next?</span><textarea aria-label="Next clip idea" rows={2} value={request} placeholder="e.g. Nora opens the box, Mira smiles, then they walk toward the window" onChange={(e) => set("request", e.target.value)} /></label>
      <div className="continuation-settings">
        <label><span>Next clip length</span><select aria-label="Next clip length" value={duration} onChange={(e) => set("duration", Number(e.target.value))}>
          {[5, 7, 10, 15].map((n) => <option value={n} key={n}>{n} seconds</option>)}
        </select></label>
        <label><span>Previous video's last frame · optional</span><select aria-label="Next clip starting frame" value={firstFrameAssetId} onChange={(e) => set("first_frame_asset_id", e.target.value)}>
          <option value="">Use references and continuity notes</option>
          {images.map((a) => <option value={a.id} key={a.id}>{a.name}</option>)}
        </select></label>
      </div>
    </div>
    <p className="timeline-help">For a closer visual match, add the actual last frame of your finished video to Photos and select it here. Otherwise this carries the story and references; it does not guarantee a seamless join.</p>
    <p className="timeline-help">The next clip starts with empty dialogue. Check who holds each object and add new spoken words. Your current clip remains saved separately.</p>
    <button type="button" disabled={busy} onClick={() => {
      try { const next = createContinuation(p, { request, duration, ending, firstFrameAssetId: firstFrameAssetId || undefined }); setError(""); onContinue(next); }
      catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    }}>Create separate {duration}-second scene · {formatTime(nextStart)}–{formatTime(nextStart + duration)}s</button>
    {error && <p className="timeline-error" role="alert">{error}</p>}
  </details>;
}
