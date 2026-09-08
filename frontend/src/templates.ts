import { newShot, retime } from "./model";
import type { Project } from "./model";

export type ApproachId =
  "continuous" | "wide-medium-close" | "dialogue" | "product";
export type WritingApproach = "faithful" | "concise" | "cinematic";
export type CameraApproach = {
  id: ApproachId;
  name: string;
  description: string;
  minimumShots: number;
  framings: string[];
  movement: string;
  cuts: boolean;
};

export const CAMERA_APPROACHES: CameraApproach[] = [
  {
    id: "continuous",
    name: "One continuous take",
    description:
      "Keep the camera in a medium view without cuts. Existing story beats stay in order.",
    minimumShots: 1,
    framings: ["medium"],
    movement: "static",
    cuts: false,
  },
  {
    id: "wide-medium-close",
    name: "Wide → medium → close",
    description:
      "Show the place, move closer to the action, then finish on a detail. Cuts separate the views.",
    minimumShots: 3,
    framings: ["wide", "medium", "close-up"],
    movement: "static",
    cuts: true,
  },
  {
    id: "dialogue",
    name: "Conversation coverage",
    description:
      "Alternate medium and close views with cuts. Choose the person shown on each shot card; speakers and exact words stay yours.",
    minimumShots: 2,
    framings: ["medium", "close-up"],
    movement: "static",
    cuts: true,
  },
  {
    id: "product",
    name: "Product reveal",
    description:
      "Start wide, move to a medium view, then slowly move in for a close detail. Choose the product or person in each shot.",
    minimumShots: 3,
    framings: ["wide", "medium", "close-up"],
    movement: "push-in",
    cuts: true,
  },
];

export const WRITING_APPROACHES: {
  id: WritingApproach;
  name: string;
  description: string;
}[] = [
  {
    id: "faithful",
    name: "Follow my idea closely",
    description:
      "Clarify your idea while keeping your actions, people and dialogue.",
  },
  {
    id: "concise",
    name: "Short and direct",
    description:
      "Use a compact prompt with the details needed to guide the video.",
  },
  {
    id: "cinematic",
    name: "Add cinematic detail",
    description:
      "Add visual atmosphere and performance detail while keeping your story and exact dialogue.",
  },
];

export function approachPreview(project: Project, id: ApproachId): string {
  const approach = CAMERA_APPROACHES.find((item) => item.id === id);
  if (!approach) throw new Error("Choose a camera starter from the list.");
  const added = Math.max(0, approach.minimumShots - project.shots.length);
  const timing = added
    ? ` Adds ${added} empty shot ${added === 1 ? "card" : "cards"} and shares your ${project.duration} seconds evenly across ${Math.max(project.shots.length, approach.minimumShots)} shots.`
    : " Keeps your shot count and timing.";
  const modeHint =
    project.mode === "fl2va" && approach.cuts
      ? " For a smooth first-to-last-frame change, the continuous-take starter is usually easier to direct."
      : "";
  return (
    approach.description +
    timing +
    " Replaces framing, camera movement and transitions. Keeps your photos, names, clothes, story and dialogue." +
    modeHint
  );
}

/** Camera starters never replace a story, move dialogue, or infer ownership. */
export function applyApproach(project: Project, id: ApproachId): void {
  const approach = CAMERA_APPROACHES.find((item) => item.id === id);
  if (!approach) throw new Error("Choose a camera starter from the list.");
  const needsShots = project.shots.length < approach.minimumShots;
  while (project.shots.length < approach.minimumShots) {
    // Empty cards deliberately have no cast: the user chooses who is shown.
    project.shots.push(newShot(project.duration / approach.minimumShots));
  }
  if (needsShots) {
    project.shots = retime(
      project.shots.map((shot) => ({ ...shot, duration: 1 })),
      project.duration,
    );
    let start = 0;
    for (const shot of project.shots) {
      if ("start" in shot) (shot as any).start = start;
      start = Math.round((start + shot.duration) * 1000) / 1000;
      if ("end" in shot) (shot as any).end = start;
    }
  }
  project.shots.forEach((shot, index) => {
    const framingIndex =
      id === "dialogue"
        ? index % approach.framings.length
        : Math.min(index, approach.framings.length - 1);
    shot.camera = {
      ...shot.camera,
      framing: approach.framings[framingIndex],
      movement: id === "product" && index < 2 ? "static" : approach.movement,
      speed: "slow",
    };
    shot.transition = index > 0 && approach.cuts ? "cut" : "continuous";
    (shot as any).director_locks = [
      ...new Set([
        ...((shot as any).director_locks || []),
        "camera.framing",
        "camera.movement",
        "transition",
      ]),
    ];
  });
  project.simple ??= {};
  project.simple.camera_approach = id;
  project.simple.directed = true;
}

/** Keep the complete editable recipe, including every reference and binding. */
export function snapshotProject(project: Project): Project {
  return structuredClone(project);
}

export type LibraryKind = "templates" | "versions";
export type LibraryRecord = {
  id: string;
  name: string;
  created_at: string;
  mode?: string;
  duration?: number;
  shot_count?: number;
  asset_count?: number;
  notes?: string;
  rating?: number;
  prompt_preview?: string;
  project?: Project;
  prompt?: string;
};

export function projectSummary(record: LibraryRecord): string {
  const p = record.project;
  const mode =
    (
      {
        ref2va: "Reference photos",
        i2va: "First frame only",
        fl2va: "First + last frame",
        l2va: "Last frame only",
        t2va: "Text only",
      } as Record<string, string>
    )[record.mode || p?.mode || ""] || "Video setup";
  const count = record.shot_count ?? p?.shots.length ?? 0;
  return `${mode} · ${record.duration ?? p?.duration ?? 0}s · ${count} shot${count === 1 ? "" : "s"} · ${record.asset_count ?? p?.assets.length ?? 0} references`;
}
