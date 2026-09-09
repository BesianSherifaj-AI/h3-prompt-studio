import { describe, expect, it } from "vitest";
import {
  DEFAULT_STORY_SETTINGS,
  normalizeImageGenerators,
  storyChoices,
  storyCurrentVideo,
  storyTurnPending,
  storyVideos,
  validStoryTicket,
  type Story,
  type StoryTicket,
  type StoryTurn,
} from "./storyTypes";
import type { VideoJob } from "./VideoWorkspace";

const clip = (id: string, overrides: Partial<VideoJob> = {}): VideoJob => ({
  id,
  project_id: "project",
  status: "succeeded",
  video_url: `/api/video/${id}`,
  ...overrides,
});
const story = (overrides: Partial<Story> = {}): Story => ({
  id: "story-a",
  title: "A story",
  mode: "game",
  player_name: "Arin",
  premise: "A note at a cafe",
  settings: { ...DEFAULT_STORY_SETTINGS },
  active_branch_id: "main",
  turns: [],
  clips: [],
  choices: [],
  project_id: "project",
  ...overrides,
});
const turn = (status: StoryTurn["status"]) => ({
  id: "turn-a",
  request_id: "request-000000000000",
  message: "I open the note",
  status,
  created_at: 1,
});

describe("Game defaults and turn safety", () => {
  it("starts with automatic rendering at three new seconds, 0.2 MP and eight steps", () => {
    expect(DEFAULT_STORY_SETTINGS).toMatchObject({
      review_before_render: false,
      generate_references: false,
      duration: 3,
      resolution: "0.2",
      steps: 8,
    });
  });
  it.each([
    "planning",
    "awaiting_review",
    "assets",
    "rendering",
    "observing",
    "uncertain",
  ] as const)("blocks another move while %s", (status) => {
    expect(storyTurnPending(turn(status))).toBe(true);
  });
  it.each(["succeeded", "failed", "cancelled"] as const)(
    "allows a new move after %s",
    (status) => {
      expect(storyTurnPending(turn(status))).toBe(false);
    },
  );
  it("keeps the exact player actions while cleaning duplicate or empty suggestions", () => {
    const value = story({
      choices: [
        { title: "Open it", message: "I open the note." },
        { title: "Same action", message: " I open the note. " },
        { title: "", message: "I leave" },
        { title: "Ask Elira", message: 'I ask Elira, "Who sent this?"' },
        { title: "Wait", message: "I wait for her reply." },
        { title: "Extra", message: "I stand up." },
      ],
    });
    expect(storyChoices(value)).toEqual([
      { title: "Open it", message: "I open the note." },
      { title: "Ask Elira", message: 'I ask Elira, "Who sent this?"' },
      { title: "Wait", message: "I wait for her reply." },
    ]);
  });
});

describe("Game take selection", () => {
  it("keeps old rerolls available while showing the active ending", () => {
    const original = clip("original"),
      alternate = clip("alternate"),
      current = clip("current");
    const value = story({
      active_run_id: "current",
      clips: [original, current],
      jobs: [original, alternate, current],
      turns: [{ ...turn("succeeded"), video: current }],
    });
    expect(storyVideos(value).map((item) => item.id)).toEqual([
      "original",
      "alternate",
      "current",
    ]);
    expect(storyCurrentVideo(value)?.id).toBe("current");
  });
  it("never substitutes a video from an unrelated branch when the active ending is unavailable", () => {
    const value = story({
      active_run_id: "pending",
      jobs: [clip("unrelated")],
      clips: [
        clip("previous"),
        clip("pending", { status: "running", video_url: null }),
      ],
    });
    expect(storyCurrentVideo(value)?.id).toBe("previous");
    expect(
      storyCurrentVideo(
        story({ active_run_id: "missing", jobs: [clip("unrelated")] }),
      ),
    ).toBeUndefined();
  });
  it("shows only successful playable takes", () => {
    expect(
      storyVideos(
        story({
          jobs: [
            clip("failed", { status: "failed" }),
            clip("empty", { video_url: "" }),
            clip("ready"),
          ],
        }),
      ).map((item) => item.id),
    ).toEqual(["ready"]);
  });
});

describe("Game image model catalog", () => {
  it("accepts the backend string catalog and preserves object compatibility flags", () => {
    expect(
      normalizeImageGenerators([
        "z_image_turbo.safetensors",
        "z_image_turbo.safetensors",
        { id: "other", name: "Other", compatible: false },
        null,
        { name: "Missing ID" },
        " ",
      ]),
    ).toEqual([
      {
        id: "z_image_turbo.safetensors",
        name: "z image turbo",
        available: true,
        compatible: true,
      },
      { id: "other", name: "Other", compatible: false },
    ]);
    expect(normalizeImageGenerators({ models: [] })).toEqual([]);
  });
});

describe("saved Game requests", () => {
  const ticket: StoryTicket = {
    storyId: "story-a",
    requestId: "12345678-1234-1234-1234-123456789abc",
    path: "/stories/story-a/turns",
    body: {
      request_id: "12345678-1234-1234-1234-123456789abc",
      message: "I open the note.",
    },
  };
  it("restores only the same story and original request ID", () => {
    expect(validStoryTicket(ticket, "story-a")).toBe(true);
    expect(validStoryTicket(ticket, "story-b")).toBe(false);
    expect(
      validStoryTicket(
        { ...ticket, body: { request_id: "different" } },
        "story-a",
      ),
    ).toBe(false);
  });
  it.each([
    "branch",
    "turns/turn-a/approve",
    "turns/turn-a/retry",
    "turns/turn-a/cancel",
    "turns/turn-a/reroll",
  ])("can safely resume %s using its saved request ID", (action) => {
    expect(
      validStoryTicket(
        { ...ticket, path: `/stories/story-a/${action}` },
        "story-a",
      ),
    ).toBe(true);
  });
  it.each([
    "/stories/story-b/turns",
    "/stories/story-a/../../models/load",
    "https://example.com",
    "/stories/story-a/delete",
    "/stories/story-a/turns?other=yes",
  ])("rejects a damaged or unrelated path %s", (path) => {
    expect(validStoryTicket({ ...ticket, path }, "story-a")).toBe(false);
  });
});
