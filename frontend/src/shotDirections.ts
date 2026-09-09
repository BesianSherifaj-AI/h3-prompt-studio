import { newShot, uid } from "./model";
import type { Project, Shot } from "./model";
import { clearSceneContract, pruneSceneActors } from "./sceneContinuityState";

export type DirectedShot = Shot & { director_locks?: string[] };
export type DirectorPath =
  | "camera.framing"
  | "camera.movement"
  | "camera.height"
  | "camera.focus"
  | "transition"
  | "setting"
  | "visible_subject_ids"
  | "offscreen_subject_ids"
  | "final_state";

export const SHOT_SIZES = [
  ["wide", "Wide · people and surroundings"],
  ["long shot", "Long shot · full body"],
  ["medium", "Medium · waist up"],
  ["close-up", "Close-up · face and emotion"],
  ["extreme close-up", "Detail · an object or small feature"],
  ["over-the-shoulder", "Over the shoulder"],
  ["two-shot", "Two people together"],
] as const;
export const CAMERA_MOVES = [
  ["static", "Still camera"],
  ["pan", "Pan · turn to the side"],
  ["tracking", "Follow the action"],
  ["push-in", "Move closer"],
  ["pull-back", "Move away"],
  ["orbit", "Circle around"],
] as const;

/** An empty choice hands only this field back to AI. Other choices stay fixed. */
export function setDirectorValue(
  p: Project,
  shotId: string,
  path: DirectorPath,
  value: string | string[],
  keep = typeof value === "string" ? value.trim().length > 0 : true,
): void {
  const shot = p.shots.find((s) => s.id === shotId) as DirectedShot | undefined;
  if (!shot) return;
  if (path.startsWith("camera.")) {
    shot.camera ??= {};
    shot.camera[path.slice(7)] = typeof value === "string" ? value : "";
  } else {
    (shot as unknown as Record<string, unknown>)[path] = value;
  }
  const locks = new Set(shot.director_locks || []);
  if (keep) locks.add(path);
  else locks.delete(path);
  shot.director_locks = [...locks];
  if (path === "visible_subject_ids" || path === "offscreen_subject_ids")
    pruneSceneActors(p, shotId);
}

/** Keep named people in exactly one visibility group. An explicit empty roster is valid. */
export function setPersonVisibility(
  p: Project,
  shotId: string,
  personId: string,
  placement: "visible" | "offscreen" | "absent",
): void {
  const shot = p.shots.find((s) => s.id === shotId);
  if (!shot) return;
  const visible = shot.visible_subject_ids.filter((id) => id !== personId);
  const offscreen = shot.offscreen_subject_ids.filter((id) => id !== personId);
  if (placement === "visible") visible.push(personId);
  if (placement === "offscreen") offscreen.push(personId);
  setDirectorValue(p, shotId, "visible_subject_ids", visible);
  setDirectorValue(p, shotId, "offscreen_subject_ids", offscreen);
}

/** Work in milliseconds so edits cannot accumulate a timing gap. */
function distribute(shots: Shot[], totalMs: number): void {
  if (!shots.length) return;
  const weightTotal = shots.reduce(
    (n, s) => n + Math.max(1, s.duration * 1000 || 0),
    0,
  );
  let remaining = totalMs;
  for (let i = 0; i < shots.length; i++) {
    const ms =
      i === shots.length - 1
        ? remaining
        : Math.min(
            remaining - (shots.length - i - 1),
            Math.max(
              1,
              Math.round(
                (totalMs * Math.max(1, shots[i].duration * 1000 || 0)) /
                  weightTotal,
              ),
            ),
          );
    shots[i].duration = ms / 1000;
    remaining -= ms;
  }
}

export function setSceneDuration(
  p: Project,
  shotId: string,
  seconds: number,
): void {
  const shot = p.shots.find((s) => s.id === shotId);
  if (
    !shot ||
    !Number.isFinite(seconds) ||
    !Number.isFinite(p.duration) ||
    p.duration <= 0
  )
    return;
  (p.simple ??= {}).directed = true;
  const totalMs = Math.round(p.duration * 1000);
  if (p.shots.length === 1) {
    shot.duration = totalMs / 1000;
    return;
  }
  const others = p.shots.filter((s) => s.id !== shotId);
  const minimumMs = Math.min(250, Math.floor(totalMs / p.shots.length));
  const selectedMs = Math.min(
    totalMs - minimumMs * others.length,
    Math.max(minimumMs, Math.round(seconds * 1000)),
  );
  shot.duration = selectedMs / 1000;
  distribute(others, totalMs - selectedMs);
}

/** A transition belongs to the incoming scene, but the new first scene has no preceding cut. */
export function moveScene(p: Project, shotId: string, direction: -1 | 1): void {
  const from = p.shots.findIndex((s) => s.id === shotId);
  const to = from + direction;
  if (from < 0 || to < 0 || to >= p.shots.length) return;
  (p.simple ??= {}).directed = true;
  const [shot] = p.shots.splice(from, 1);
  p.shots.splice(to, 0, shot);
  // Do not rewrite transitions: moving back should recover every chosen setting.
  // The compiler does not emit an incoming transition for the first scene.
}

export function duplicateScene(p: Project, shotId: string): void {
  const at = p.shots.findIndex((s) => s.id === shotId);
  if (at < 0 || p.shots.length >= 6) return;
  (p.simple ??= {}).directed = true;
  const source = p.shots[at];
  const copy = structuredClone(source) as DirectedShot;
  copy.id = uid();
  // Repeating exact speech is rarely intended by copying a camera setup.
  copy.dialogue = [];
  // A new beat needs new positions and ownership; replaying the old contract can undo its action.
  clearSceneContract(copy);
  const splitMs = Math.round(source.duration * 1000);
  source.duration = Math.ceil(splitMs / 2) / 1000;
  copy.duration = Math.floor(splitMs / 2) / 1000;
  copy.transition = "cut";
  copy.director_locks = [
    ...new Set([...(copy.director_locks || []), "transition"]),
  ];
  p.shots.splice(at + 1, 0, copy);
  if (copy.duration < 0.25) distribute(p.shots, Math.round(p.duration * 1000));
}

export function appendScene(p: Project): void {
  if (p.shots.length >= 6) return;
  (p.simple ??= {}).directed = true;
  const shot = newShot(p.duration / (p.shots.length + 1)) as DirectedShot;
  shot.camera = { framing: "", movement: "", height: "", focus: "", speed: "" };
  shot.transition = p.shots.length ? "cut" : "continuous";
  if (p.shots.length) shot.director_locks = ["transition"];
  p.shots.push(shot);
  distribute(p.shots, Math.round(p.duration * 1000));
}

export const CAMERA_SETUPS = [
  { id: "place", label: "Show the place", framing: "wide", movement: "static" },
  {
    id: "emotion",
    label: "Show emotion",
    framing: "close-up",
    movement: "static",
  },
  {
    id: "conversation",
    label: "Two people talking",
    framing: "two-shot",
    movement: "static",
  },
  {
    id: "follow",
    label: "Follow someone",
    framing: "medium",
    movement: "tracking",
  },
  {
    id: "detail",
    label: "Show an object",
    framing: "extreme close-up",
    movement: "static",
  },
] as const;

export function applyCameraSetup(
  p: Project,
  shotId: string,
  setupId: string,
): void {
  const setup = CAMERA_SETUPS.find((s) => s.id === setupId);
  if (!setup) return;
  setDirectorValue(p, shotId, "camera.framing", setup.framing);
  setDirectorValue(p, shotId, "camera.movement", setup.movement);
}

/** Use ordinary names in the draft; the compiler owns final H3 reference numbering. */
export function appendSceneAction(
  p: Project,
  shotId: string,
  personId: string,
  action: string,
  objectId = "",
): void {
  const shot = p.shots.find((s) => s.id === shotId);
  const person = p.subjects.find((s) => s.id === personId);
  if (!shot || !person || !action.trim()) return;
  const object = p.assets.find((a) => a.id === objectId);
  const words = `${person.name || "The person"} ${action.trim()}${object ? ` ${object.name || "the referenced object"}` : ""}`;
  const sentence = /[.!?]$/.test(words) ? words : `${words}.`;
  shot.action = [shot.action.trim(), sentence].filter(Boolean).join(" ");
  if (
    !shot.visible_subject_ids.includes(personId) &&
    !shot.offscreen_subject_ids.includes(personId)
  ) {
    shot.visible_subject_ids.push(personId);
  }
}
