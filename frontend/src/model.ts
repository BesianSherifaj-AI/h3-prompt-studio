import { matchRecipeLoraToMode } from "./recipeLoras";

export type Asset = {
  id: string;
  name: string;
  media_type: string;
  role: string;
  semantic_role: string;
  enabled: boolean;
  locked_order: boolean;
  description: string;
  observation: string;
  approved_observation: string;
  duration?: number;
  width?: number;
  height?: number;
  [key: string]: any;
};
export type Subject = {
  id: string;
  name: string;
  asset_ids: string[];
  description: string;
};
export type SceneActor = {
  subject_id: string;
  activity: "act" | "hold";
  start: string;
  action: string;
  end: string;
};
export type SceneObject = {
  entity_id: string;
  name: string;
  description: string;
  count: number;
  start: string;
  end: string;
};
export type SceneContract = {
  actors?: SceneActor[];
  objects?: SceneObject[];
  environment?: string;
  background_activity?: string;
};
export type Shot = {
  id: string;
  duration: number;
  action: string;
  setting: string;
  camera: Record<string, string>;
  performance: string;
  final_state: string;
  visible_subject_ids: string[];
  offscreen_subject_ids: string[];
  dialogue: any[];
  sound: string;
  transition: string;
  scene_contract?: SceneContract;
  scene_contract_source?: "generated" | "authored";
  director_locks?: string[];
};
export type Project = {
  schema_version: number;
  id: string;
  title: string;
  mode: string;
  duration: number;
  aspect_ratio: string;
  profile: string;
  authoring_mode: string;
  story: { text: string; locked: boolean };
  style: Record<string, string>;
  assets: Asset[];
  subjects: Subject[];
  shots: Shot[];
  soundscape: string;
  music: string;
  custom_instructions: string;
  [key: string]: any;
};
export const uid = () => crypto.randomUUID();
export const newShot = (duration = 5): Shot => ({
  id: uid(),
  duration,
  action: "",
  setting: "",
  camera: {
    framing: "medium",
    movement: "static",
    height: "eye level",
    speed: "slow",
    focus: "",
  },
  performance: "",
  final_state: "",
  visible_subject_ids: [],
  offscreen_subject_ids: [],
  dialogue: [],
  sound: "",
  transition: "continuous",
});
export const MODES = [
  ["ref2va", "Ref2VA · references"],
  ["fl2va", "FL2VA · first + last"],
  ["i2va", "I2VA · first frame"],
  ["l2va", "L2VA · last frame"],
  ["t2va", "T2VA · text only"],
];
export const ROLES = [
  ["face", "Face / identity"],
  ["character", "Character"],
  ["background", "Background / location"],
  ["object", "Object / product"],
  ["palette", "Color palette"],
  ["style", "Visual style"],
  ["wardrobe", "Wardrobe"],
  ["pose", "Pose / composition"],
  ["other", "Other"],
];
export const PROFILE_OPTIONS = [
  ["director", "Official + director"],
  ["official", "MiniMax official"],
  ["concise", "Compact official"],
  ["custom", "Custom direction"],
];
export function retime(shots: Shot[], duration: number): Shot[] {
  const total = shots.reduce((n, s) => n + Math.max(0.01, s.duration || 0), 0);
  const result = shots.map((s) => ({
    ...s,
    duration:
      Math.round((Math.max(0.01, s.duration || 0) / total) * duration * 1000) /
      1000,
  }));
  if (result.length)
    result[result.length - 1].duration =
      Math.round(
        (duration - result.slice(0, -1).reduce((n, s) => n + s.duration, 0)) *
          1000,
      ) / 1000;
  return result;
}
export function switchMode(project: Project, mode: string): Project {
  const next = structuredClone(project);
  matchRecipeLoraToMode(next, mode);
  next.mode = mode;
  // Context includes deliberately enabled AI-only guides. Mode changes must
  // never promote those guides or reactivate a disabled library reference.
  const eligible = next.assets.filter((a) => a.enabled && a.role !== "context");
  const images = eligible.filter((a) => a.media_type === "image");
  if (mode === "ref2va") {
    for (const a of eligible) {
      a.role = "reference_" + a.media_type;
    }
    return next;
  }
  let first = images.find((a) => a.role === "first_frame");
  let last = images.find((a) => a.role === "last_frame");
  if (mode === "fl2va") {
    const remaining = images.filter((a) => a !== first && a !== last);
    first ||= remaining.shift();
    last ||= remaining.shift();
  } else if (mode === "i2va") {
    first ||= images[0];
    last = undefined;
  } else if (mode === "l2va") {
    last ||= images[0];
    first = undefined;
  } else {
    first = undefined;
    last = undefined;
  }
  for (const a of eligible) {
    a.enabled = a === first || a === last;
    if (a === first) a.role = "first_frame";
    if (a === last) a.role = "last_frame";
  }
  return next;
}
