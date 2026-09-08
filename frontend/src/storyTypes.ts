import type { VideoJob } from "./VideoWorkspace";

export type StoryChoice = { title: string; message: string };
export type StoryPlan = {
  action: string;
  characters?: { name: string; description: string; voice: string }[];
  dialogue: { speaker: string; text: string }[];
  transition: "continue" | "cut";
  setting: string;
  final_state: string;
  asset_requests: Record<string, unknown>[];
  choices: StoryChoice[];
  [key: string]: unknown;
};
export type StoryTurnStatus =
  | "planning"
  | "awaiting_review"
  | "assets"
  | "rendering"
  | "observing"
  | "succeeded"
  | "failed"
  | "uncertain"
  | "cancelled";
export type StoryTurn = {
  id: string;
  request_id: string;
  message: string;
  status: StoryTurnStatus;
  stage?: string;
  error?: string;
  plan?: StoryPlan;
  run_id?: string;
  video?: VideoJob;
  created_at: number | string;
  [key: string]: unknown;
};
export type StorySettings = {
  review_before_render: boolean;
  duration: number;
  image_model?: string;
  resolution: string;
  steps: number;
  style?: string;
  [key: string]: unknown;
};
export type Story = {
  id: string;
  title: string;
  mode: "game" | "studio";
  player_name: string;
  premise: string;
  settings: StorySettings;
  active_branch_id: string;
  active_run_id?: string;
  turns: StoryTurn[];
  clips: VideoJob[];
  choices: StoryChoice[];
  jobs?: VideoJob[];
  observed_state?: string | Record<string, unknown>;
  project_id: string;
  [key: string]: unknown;
};
export type ImageGeneratorModel = {
  id: string;
  name: string;
  available?: boolean;
  compatible?: boolean;
  reason?: string;
  description?: string;
  [key: string]: unknown;
};
export type StoryTicket = {
  storyId: string;
  path: string;
  body: Record<string, unknown>;
  requestId: string;
};
export const DEFAULT_STORY_SETTINGS: StorySettings = {
  review_before_render: false,
  duration: 5,
  resolution: "0.3",
  steps: 8,
  style: "Cinematic and natural",
};
export const RUNNING_STORY_STATUSES = new Set<StoryTurnStatus>([
  "planning",
  "assets",
  "rendering",
  "observing",
  "uncertain",
]);
export function storyTurnPending(turn?: StoryTurn | null) {
  return (
    !!turn &&
    (RUNNING_STORY_STATUSES.has(turn.status) ||
      turn.status === "awaiting_review")
  );
}
export function storyTurnLabel(turn?: StoryTurn | null) {
  if (!turn) return "Ready for your first move";
  return (
    {
      planning: "Planning the next moment",
      awaiting_review: "Review your next scene",
      assets: "Preparing scene references",
      rendering: "Creating your video",
      observing: "Reading the new ending",
      succeeded: "Your turn",
      failed: "This turn needs attention",
      uncertain: "Checking the previous request",
      cancelled: "Turn cancelled",
    }[turn.status] ||
    turn.stage ||
    "Updating story"
  );
}
export function storyChoices(story?: Story | null): StoryChoice[] {
  const latest = story?.turns?.at(-1);
  const raw = story?.choices?.length
    ? story.choices
    : latest?.plan?.choices || [];
  const seen = new Set<string>();
  return raw
    .filter(
      (c) =>
        typeof c?.title === "string" &&
        typeof c?.message === "string" &&
        c.title.trim() &&
        c.message.trim() &&
        !seen.has(c.message.trim()) &&
        !!seen.add(c.message.trim()),
    )
    .slice(0, 3)
    .map((c) => ({ title: c.title.trim(), message: c.message.trim() }));
}
export function storyVideos(story?: Story | null): VideoJob[] {
  if (!story) return [];
  const seen = new Set<string>();
  return [
    ...(story.jobs || []),
    ...(story.clips || []),
    ...(story.turns || []).flatMap((t) => (t.video ? [t.video] : [])),
  ].filter(
    (c) =>
      c.status === "succeeded" &&
      !!c.video_url &&
      !seen.has(c.id) &&
      !!seen.add(c.id),
  );
}
export function storyCurrentVideo(story?: Story | null): VideoJob | undefined {
  if (!story) return undefined;
  const clips = storyVideos(story);
  return (
    clips.find(
      (c) =>
        c.id === story.active_run_id && c.status === "succeeded" && c.video_url,
    ) ||
    [...(story.clips || [])]
      .reverse()
      .find((c) => c.status === "succeeded" && c.video_url)
  );
}
export function normalizeImageGenerators(
  value: unknown,
): ImageGeneratorModel[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value
    .flatMap((item): ImageGeneratorModel[] => {
      if (typeof item === "string" && item.trim())
        return [
          {
            id: item,
            name: item.replace(/\.safetensors$/i, "").replaceAll("_", " "),
            available: true,
            compatible: true,
          },
        ];
      if (item && typeof item.id === "string" && item.id.trim())
        return [
          {
            ...item,
            name: typeof item.name === "string" ? item.name : item.id,
          },
        ];
      return [];
    })
    .filter((item) => !seen.has(item.id) && !!seen.add(item.id));
}
export function validStoryTicket(
  value: unknown,
  storyId?: string,
): value is StoryTicket {
  const ticket = value as StoryTicket;
  return (
    !!ticket &&
    typeof ticket.storyId === "string" &&
    (!storyId || ticket.storyId === storyId) &&
    /^[a-zA-Z0-9_-]{1,200}$/.test(ticket.storyId) &&
    typeof ticket.path === "string" &&
    ticket.path.startsWith(`/stories/${encodeURIComponent(ticket.storyId)}/`) &&
    /^(turns|branch|turns\/[a-zA-Z0-9_-]+\/(approve|retry|cancel|reroll))$/.test(
      ticket.path.slice(
        `/stories/${encodeURIComponent(ticket.storyId)}/`.length,
      ),
    ) &&
    typeof ticket.requestId === "string" &&
    /^[a-zA-Z0-9-]{16,80}$/.test(ticket.requestId) &&
    !!ticket.body &&
    typeof ticket.body === "object" &&
    ticket.body.request_id === ticket.requestId
  );
}
