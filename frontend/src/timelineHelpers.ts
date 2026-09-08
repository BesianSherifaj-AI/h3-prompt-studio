import { newShot, uid } from "./model";
import type { Project, Shot } from "./model";
import { setSimpleMode } from "./simple";
import { setDirectorValue } from "./shotDirections";
import type { DirectedShot } from "./shotDirections";

export type TimelineRange = { id: string; index: number; start: number; end: number; duration: number };
const ms = (value: number) => Math.round(value * 1000);
const seconds = (value: number) => value / 1000;
export const formatTime = (value: number) => Number(value.toFixed(3)).toString();

export function timelineRanges(project: Project): TimelineRange[] {
  let cursor = 0;
  return project.shots.map((shot, index) => {
    const start = cursor;
    cursor += ms(shot.duration);
    return { id: shot.id, index, start: seconds(start), end: seconds(cursor), duration: shot.duration };
  });
}

function syncTimes(p: Project): void {
  for (const range of timelineRanges(p)) {
    const shot = p.shots[range.index] as Shot & { start?: number; end?: number };
    if ("start" in shot) shot.start = range.start;
    if ("end" in shot) shot.end = range.end;
  }
  (p.simple ??= {}).directed = true;
}

/** Move one shared boundary: all the other scene start/end times stay put. */
export function setTimelineBoundary(p: Project, incomingShotId: string, at: number): void {
  const ranges = timelineRanges(p);
  const index = p.shots.findIndex((shot) => shot.id === incomingShotId);
  if (index < 1) throw new Error("The first scene starts at 0 seconds.");
  const before = ranges[index - 1];
  const after = ranges[index];
  if (!Number.isFinite(at) || ms(at) < ms(before.start) + 250 || ms(at) > ms(after.end) - 250)
    throw new Error(`Choose a time from ${formatTime(before.start + 0.25)} to ${formatTime(after.end - 0.25)} seconds, leaving both scenes time to play.`);
  p.shots[index - 1].duration = seconds(ms(at) - ms(before.start));
  p.shots[index].duration = seconds(ms(after.end) - ms(at));
  syncTimes(p);
}

/** A new beat keeps the setup, while exact speech stays only in its original scene. */
export function splitSceneAt(p: Project, shotId: string, at: number, transition: "cut" | "continuous" = "cut"): string {
  if (p.shots.length >= 6) throw new Error("This clip already has six scenes. Start another clip for more story.");
  const range = timelineRanges(p).find((item) => item.id === shotId);
  if (!range) throw new Error("Choose a scene to split.");
  if (!Number.isFinite(at) || ms(at) < ms(range.start) + 250 || ms(at) > ms(range.end) - 250)
    throw new Error("Split inside the scene, leaving at least 0.25 seconds on each side.");
  const original = p.shots[range.index] as DirectedShot;
  const copy = structuredClone(original) as DirectedShot;
  copy.id = uid();
  copy.duration = seconds(ms(range.end) - ms(at));
  copy.action = "";
  copy.performance = "";
  copy.dialogue = [];
  original.duration = seconds(ms(at) - ms(range.start));
  // The former ending still belongs at the end of this interval.
  original.final_state = "";
  original.director_locks = (original.director_locks || []).filter((path) => path !== "final_state");
  p.shots.splice(range.index + 1, 0, copy);
  setDirectorValue(p, copy.id, "transition", transition);
  if (transition === "cut") (p.simple ??= {}).continuous_take = false;
  syncTimes(p);
  return copy.id;
}

export function splitTimelineAt(p: Project, at: number, transition: "cut" | "continuous" = "cut"): string {
  const ranges = timelineRanges(p);
  if (!Number.isFinite(at) || at <= 0 || at >= p.duration)
    throw new Error(`Choose a split time between 0 and ${formatTime(p.duration)} seconds.`);
  const existing = ranges.find((range) => ms(range.start) === ms(at));
  if (existing) {
    setDirectorValue(p, existing.id, "transition", transition);
    if (transition === "cut") (p.simple ??= {}).continuous_take = false;
    return existing.id;
  }
  const range = ranges.find((item) => ms(at) > ms(item.start) && ms(at) < ms(item.end));
  if (!range) throw new Error("That time is outside the current timeline.");
  return splitSceneAt(p, range.id, at, transition);
}

export function twoScenesInFirstFiveSeconds(p: Project): void {
  if (p.shots.length !== 1) throw new Error("Use the split controls to add a scene to your existing timeline.");
  const window = Math.min(5, p.duration);
  if (p.duration > window) splitTimelineAt(p, window, "cut");
  splitTimelineAt(p, window / 2, "cut");
}

/** A continuous take may contain several timed actions without any edit/cut. */
export function setContinuousTake(p: Project): void {
  for (const shot of p.shots) setDirectorValue(p, shot.id, "transition", "continuous");
  (p.simple ??= {}).continuous_take = true;
  p.simple.directed = true;
}

export type ContinuationOptions = {
  request: string;
  duration?: number;
  ending?: string;
  firstFrameAssetId?: string;
};

export function continuationEnding(p: Project): string {
  const last = p.shots[p.shots.length - 1];
  return last?.final_state?.trim() || last?.action?.trim() || p.story.text.trim();
}

/** Start a separate clip; never silently reuse an old starting frame as the next one. */
export function createContinuation(source: Project, options: ContinuationOptions): Project {
  const duration = options.duration ?? 15;
  if (!Number.isInteger(duration) || duration < 4 || duration > 15)
    throw new Error("Choose a next-clip length from 4 to 15 seconds.");
  const request = options.request.trim() || "Continue the action naturally from the previous ending.";
  const p = structuredClone(source);
  const frame = options.firstFrameAssetId
    ? p.assets.find((asset) => asset.id === options.firstFrameAssetId && asset.media_type === "image")
    : undefined;
  if (options.firstFrameAssetId && !frame) throw new Error("Add the previous video's last frame as a photo, then select it here.");
  const previous = source.simple?.continuation;
  const sequenceStart = Number(previous?.sequence_start || 0) + source.duration;
  const segmentIndex = Number(previous?.segment_index || 1) + 1;
  const sequenceTitle = previous?.sequence_title || source.title || "My film";
  const last = source.shots[source.shots.length - 1];
  p.id = uid();
  p.title = `${sequenceTitle} · Clip ${segmentIndex}`;
  p.duration = duration;
  p.story = { text: request, locked: true };
  p.authoring_mode = "full";
  const shot = newShot(duration) as DirectedShot;
  if (last) {
    shot.camera = structuredClone(last.camera);
    shot.setting = last.setting;
    shot.visible_subject_ids = [...last.visible_subject_ids];
    shot.offscreen_subject_ids = [...last.offscreen_subject_ids];
    shot.director_locks = (last as DirectedShot).director_locks?.filter((path) =>
      path.startsWith("camera.") || ["setting", "visible_subject_ids", "offscreen_subject_ids"].includes(path),
    );
  }
  shot.action = request;
  p.shots = [shot];
  p.simple ??= {};
  p.simple.person_actions = {};
  p.simple.continuation = {
    previous_project_id: source.id,
    previous_title: source.title,
    previous_ending: options.ending?.trim() || continuationEnding(source),
    ending_source: options.ending?.trim() ? "user_note" : "planned_ending",
    previous_duration: source.duration,
    sequence_start: sequenceStart,
    segment_index: segmentIndex,
    sequence_title: sequenceTitle,
    request,
    continuity_basis: frame ? "provided_last_frame" : "references_and_notes",
    ...(frame ? { previous_frame_asset_id: frame.id } : {}),
    previous_object_owners: source.assets.filter((a) => a.semantic_role === "object" && a.simple_owner_id)
      .map((a) => ({ asset_id: a.id, person_id: a.simple_owner_id })),
  };
  delete p.simple.next_clip_draft;
  // Reusing the old selector would branch again from an earlier segment.
  if (p.comfy_render) delete p.comfy_render.continuation_source;
  // Per-clip instructions and speech must not be replayed in the next segment.
  p.custom_instructions = "";
  p.assistant_instructions = "";
  for (const asset of p.assets) {
    if (asset.semantic_role === "object") delete asset.simple_owner_id;
    if (asset.role === "first_frame" || asset.role === "last_frame") {
      asset.role = "context";
      delete p.simple.previous_image_roles?.[asset.id];
    }
    if (["first_frame", "last_frame"].includes(p.simple.previous_image_roles?.[asset.id]))
      delete p.simple.previous_image_roles[asset.id];
  }
  if (frame) {
    for (const asset of p.assets) {
      if (asset.enabled && asset.role !== "context") {
        const memory = asset.media_type === "image" ? "previous_image_roles" : "previous_media_roles";
        (p.simple[memory] ??= {})[asset.id] = asset.role;
        asset.role = "context";
      }
    }
    frame.role = "first_frame";
    frame.enabled = true;
    setSimpleMode(p, "i2va");
  } else if (source.mode !== "t2va") {
    setSimpleMode(p, "ref2va");
    if (!p.assets.some((asset) => asset.enabled && asset.role.startsWith("reference_"))) setSimpleMode(p, "t2va");
  }
  if (source.simple?.continuous_take) setContinuousTake(p);
  delete p.simple_generation;
  delete p.bridge_source;
  delete p.bridge;
  delete p.created_at;
  delete p.updated_at;
  return p;
}
