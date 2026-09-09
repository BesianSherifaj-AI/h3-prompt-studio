import type { VideoJob } from "./VideoWorkspace";
import type { Project } from "./model";

export type GameGuide = { id: string; revision: number; text: string; scope: "next" | "persistent"; enabled: boolean };
export type GameCharacter = {
  id: string; name: string; control: "player" | "npc"; description: string;
  personality: string; goals: string; speaking_style: string; private_knowledge: string;
  relationships: Record<string, string> | string; asset_ids: string[];
  location_id?: string | null; witnessed_events?: string[];
  state?: Record<string, unknown>;
};
export type GameWorld = {
  schema_version: number; current_location_id: string | null;
  characters: GameCharacter[]; locations: Array<{id:string;name:string;description:string;exits:any[];asset_ids:string[]}>;
  entities: Array<{id:string;name:string;kind:string;location_id?:string|null;owner_id?:string|null;holder_id?:string|null;worn_by_id?:string|null;asset_ids:string[];affordances:string[];state:Record<string,unknown>}>;
  objectives: any[]; rules: string; events: any[];
};
export type GameIntent = { kind: string; target_id?: string; recipient_id?: string; extent?: string; speed?: string; camera?: string; presentation?: string; direction?: string };
export type StoryConfiguration = {
  project: Project; world: GameWorld; guides: GameGuide[]; settings: StorySettings;
  premise: string; player_name: string; player_character_id: string;
};

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
  | "cancelled"
  | "awaiting_assistant"
  | "awaiting_acceptance"
  | "inspection_failed"
  | "stopping";
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
  generate_references?: boolean;
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
  project?: Project;
  world?: GameWorld;
  guides?: GameGuide[];
  configuration_revision?: number;
  player_character_id?: string;
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
export const PIXEL_STYLE = "2D pixel art, hand-drawn 16-bit sprite animation, crisp visible square pixels, flat illustrated backgrounds, limited palette, readable silhouettes. No 3D voxel blocks, Minecraft or Roblox aesthetic.";
export const DEFAULT_STORY_SETTINGS: StorySettings = {
  review_before_render: false,
  generate_references: false,
  duration: 3,
  resolution: "0.2",
  experimental_preview: true,
  steps: 8,
  style: PIXEL_STYLE,
  aspect_ratio: "16:9",
  transition: "auto",
  assistant_provider: "lmstudio",
  fast_actions: true,
  concurrency: 1,
};
export const RUNNING_STORY_STATUSES = new Set<StoryTurnStatus>([
  "planning",
  "assets",
  "rendering",
  "observing",
  "uncertain",
  "awaiting_assistant",
  "stopping",
]);
export function storyTurnPending(turn?: StoryTurn | null) {
  return (
    !!turn &&
    (RUNNING_STORY_STATUSES.has(turn.status) ||
      ["awaiting_review", "awaiting_acceptance", "inspection_failed"].includes(turn.status))
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
      awaiting_assistant: "Waiting for a supervised assistant response",
      awaiting_acceptance: "Choose what becomes part of the story",
      inspection_failed: "Video ready · ending inspection needs attention",
      stopping: "Stopping the current turn",
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

/** Only a successfully read server inventory can diagnose missing requirements. */
export function imageGeneratorInventory(value: any): ImageGeneratorModel[] {
  const models = normalizeImageGenerators(value?.generators || value?.models);
  const listed = new Set(models.map(model => model.id));
  const missing = new Map<string, string[]>();
  for (const server of Array.isArray(value?.servers) ? value.servers : []) {
    if (!server || typeof server.model_missing !== "object" || !server.model_missing) continue;
    for (const [id, requirements] of Object.entries(server.model_missing)) {
      if (listed.has(id) || !Array.isArray(requirements)) continue;
      const names = requirements.filter((item): item is string => typeof item === "string" && !!item.trim());
      if (!names.length) continue;
      const lines = missing.get(id) || [];
      lines.push(`${typeof server.comfy_url === "string" ? server.comfy_url : "Checked ComfyUI server"}: ${names.join(", ")}`);
      missing.set(id, lines);
    }
  }
  for (const [id, requirements] of missing) models.push({
    id, name: id.replace(/\.safetensors$/i, "").replaceAll("_", " "),
    available: false, compatible: false,
    reason: `Missing requirements in the checked inventory: ${requirements.join("; ")}.`,
  });
  return models;
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
    /^(turns|branch|turns\/[a-zA-Z0-9_-]+\/(approve|retry|cancel|reroll|edit|resume|retry-inspection|accept-intended|accept-visible|stop-and-apply))$/.test(
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
