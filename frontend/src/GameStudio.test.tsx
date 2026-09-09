import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import GameStudio, {
  GamePlanEditor,
  gameMemoryText,
  gameProjectWithUploads,
  gameReferenceIssues,
  gameReferenceOwner,
  gameReferenceKind,
  openCreatedGame,
} from "./GameStudio";
import { useStorySession } from "./useStorySession";
import {
  DEFAULT_STORY_SETTINGS,
  type Story,
  type StoryPlan,
} from "./storyTypes";
import type { Asset, Project } from "./model";
import type { VideoJob } from "./VideoWorkspace";

vi.mock("./useStorySession", () => ({ useStorySession: vi.fn() }));
const photo = (id: string): Asset => ({
  id,
  name: id,
  media_type: "image",
  role: "reference_image",
  semantic_role: "face",
  enabled: true,
  locked_order: false,
  description: "",
  observation: "",
  approved_observation: "",
});
const project = (): Project => ({
  id: "studio",
  schema_version: 1,
  title: "Studio original",
  mode: "ref2va",
  duration: 10,
  aspect_ratio: "16:9",
  profile: "director",
  authoring_mode: "manual",
  story: { text: "A mysterious note arrives at the cafe.", locked: true },
  style: {},
  assets: [photo("Arin")],
  subjects: [
    {
      id: "arin",
      name: "Arin",
      description: "The detective",
      asset_ids: ["Arin"],
    },
  ],
  shots: [],
  soundscape: "",
  music: "",
  custom_instructions: "",
});
const plan: StoryPlan = {
  action: "Arin opens the note while Elira watches.",
  characters: [
    { name: "Arin", description: "Gray jacket", voice: "Calm" },
    { name: "Elira", description: "Blue dress", voice: "Soft" },
  ],
  dialogue: [{ speaker: "Elira", text: "Who sent it?" }],
  transition: "continue",
  setting: "Cafe table",
  final_state: "The note is open in Arin's hand.",
  asset_requests: [
    {
      name: "The note",
      prompt: "A folded cream envelope",
      semantic_role: "object",
      person_name: "Arin",
      prompt_tag: "note",
    },
  ],
  choices: [
    { title: "Read it", message: "I read the note aloud." },
    { title: "Ask Elira", message: 'I ask Elira, "Do you recognize it?"' },
    { title: "Hide it", message: "I put it in my jacket." },
  ],
  continuity_lock: { object_holder: "Arin" },
};
const video = (id: string, seed = 1): VideoJob => ({
  id,
  project_id: "studio",
  status: "succeeded",
  video_url: `/api/videos/${id}/file`,
  scene_video_url: `/api/videos/${id}/scene`,
  seed,
  duration: 5,
  can_continue: true,
});
const savedStory = (): Story => ({
  id: "saved-game",
  project_id: "studio",
  mode: "game",
  title: "The note",
  premise: "A mysterious note arrives.",
  player_name: "Arin",
  settings: { ...DEFAULT_STORY_SETTINGS },
  active_branch_id: "main",
  active_run_id: "latest",
  clips: [video("opening"), video("latest", 2)],
  jobs: [video("opening"), video("latest", 2), video("alternate", 3)],
  choices: plan.choices,
  turns: [
    {
      id: "turn-1",
      request_id: "request-11111111111",
      status: "succeeded",
      message: "I open the note.",
      created_at: 1,
      plan,
      run_id: "opening",
      video: video("opening"),
    },
    {
      id: "turn-2",
      request_id: "request-22222222222",
      status: "succeeded",
      message: "I read it aloud.",
      created_at: 2,
      plan,
      run_id: "latest",
      video: video("latest", 2),
    },
  ],
  observed_state: {
    observed_state: "The note is in Arin's hand.",
    uncertainties: "The ink is hard to read.",
  },
});
const session = (story: Story | null = null) => ({
  story,
  stories: story ? [story] : [],
  selectedId: story?.id || "",
  loading: false,
  submitting: false,
  error: "",
  pendingTicket: null,
  pendingCreation: null,
  generators: [],
  defaultGenerator: "",
  generatorsLoading: false,
  generatorsChecked: true,
  generatorErrors: [],
  refreshGenerators: vi.fn(),
  selectStory: vi.fn(),
  refresh: vi.fn(),
  create: vi.fn(),
  resumeCreation: vi.fn(),
  patch: vi.fn(),
  sendTurn: vi.fn(),
  turnAction: vi.fn(),
  branch: vi.fn(),
  resumePending: vi.fn(),
  clearError: vi.fn(),
});
const render = () =>
  renderToStaticMarkup(
    <GameStudio
      project={project()}
      onStudio={() => {}}
      onUploadFiles={async () => []}
    />,
  );
beforeEach(() => {
  vi.mocked(useStorySession).mockReturnValue(session());
});

describe("separate Game screen", () => {
  it.each([undefined, null, {}, { observed_state: "" }, { observed_state: "  " }, { observed_state: 42 }])("keeps visible-result acceptance disabled without a usable inspection: %j", (observation) => {
    const value = savedStory();
    value.turns.push({ id: "inspection", request_id: "inspection-request", status: "inspection_failed", message: "Open the note.", created_at: 3, plan, observation });
    vi.mocked(useStorySession).mockReturnValue(session(value));
    const html = render();
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Use visible result<\/button>/);
    expect(html).toContain("Retry ending inspection first so there is a visible result to use.");
    expect(html).toMatch(/<button>Retry ending inspection<\/button>/);
    expect(html).toMatch(/<button>Use intended story<\/button>/);
  });
  it("allows keeping a successfully inspected visible outcome", () => {
    const value = savedStory();
    value.turns.push({ id: "inspection", request_id: "inspection-request", status: "awaiting_acceptance", message: "Open the note.", created_at: 3, plan, observation: { observed_state: "The note lies open on the table." } });
    vi.mocked(useStorySession).mockReturnValue(session(value));
    expect(render()).toMatch(/<button(?![^>]*disabled)[^>]*>Use visible result<\/button>/);
  });
  it("shows a ready player when a later cancelled turn leaves a completed ending available", () => {
    const value = savedStory();
    value.turns.push({
      id: "cancelled-later",
      request_id: "cancelled-later-request",
      status: "cancelled",
      message: "Try a different direction.",
      created_at: 3,
      plan,
    });
    vi.mocked(useStorySession).mockReturnValue(session(value));
    const html = render();
    const status = html.match(
      /<span class="game-status[^"]*" role="status">([\s\S]*?)<\/span>/,
    )?.[1];
    expect(status).toContain("Ready for your next move");
    expect(status).not.toContain("Turn cancelled");
    expect(html).toContain(
      "Turn cancelled. Your last completed ending is kept.",
    );
    expect(html).toContain('src="/api/videos/latest/scene"');
  });
  it("keeps a running turn's progress in the player even when a previous ending is playable", () => {
    const value = savedStory();
    value.turns.push({
      id: "rendering-next",
      request_id: "rendering-next-request",
      status: "rendering",
      message: "Continue the conversation.",
      created_at: 3,
      plan,
    });
    vi.mocked(useStorySession).mockReturnValue(session(value));
    const html = render();
    const status = html.match(
      /<span class="game-status[^"]*" role="status">([\s\S]*?)<\/span>/,
    )?.[1];
    expect(status).toContain("Creating your video");
    expect(status).not.toContain("Ready for your next move");
  });
  it("opens with a simple setup and does not call generation or model actions", () => {
    const state = session();
    vi.mocked(useStorySession).mockReturnValue(state);
    const html = render();
    expect(html).toContain("What is this story about?");
    expect(html).toContain("Start game");
    expect(html).not.toContain("Arin");
    expect(html).not.toContain("A mysterious note arrives at the cafe.");
    expect(html).toContain("Import current Studio cast &amp; photos");
    expect(html).toContain("8 steps");
    expect(html).not.toMatch(
      /<label class="game-checkbox"><input[^>]* checked/,
    );
    expect(html).toContain("Bring your characters");
    expect(html).toContain("My own character");
    expect(html).toContain("Pixel preview · ~0.2 MP / 3 seconds");
    expect(html).toContain("Add photos");
    expect(state.create).not.toHaveBeenCalled();
    expect(state.sendTurn).not.toHaveBeenCalled();
  });
  it("keeps the video, three actual choices, conversation, and whole-film export together", () => {
    vi.mocked(useStorySession).mockReturnValue(session(savedStory()));
    const html = render();
    expect(html).toContain('src="/api/videos/latest/scene"');
    expect(html).toContain('href="/api/videos/latest/file"');
    expect(html).toContain("View original generated clip");
    expect(html).toContain("Clip details &amp; original output");
    expect(html).toContain("Your next move");
    for (const choice of plan.choices) expect(html).toContain(choice.title);
    expect(html).toContain('href="/api/stories/saved-game/video"');
    expect(html).toContain("Play whole story");
    expect(html).toContain('aria-label="Saved takes"');
    expect(html).toContain("Seed 3");
    expect(html).toContain("Who sent it?");
    const rerolls = [
      ...html.matchAll(
        /<button\b[^>]*>(?:(?!<\/button>)[\s\S])*?Another take(?:(?!<\/button>)[\s\S])*?<\/button>/g,
      ),
    ].map((match) => match[0]);
    expect(rerolls).toHaveLength(2);
    expect(rerolls[0]).toContain('disabled=""');
    expect(rerolls[1]).not.toContain('disabled=""');
  });
  it("shows and locks the original unconfirmed setup instead of displaying a new draft", () => {
    const original = project();
    original.assets = [photo("Elira-original")];
    original.subjects = [
      {
        id: "elira",
        name: "Elira",
        asset_ids: ["Elira-original"],
        description: "",
      },
    ];
    const pendingCreation = {
      requestId: "original-request",
      body: {
        project: original,
        mode: "game" as const,
        premise: "The original mystery on the train.",
        player_name: "Elira",
        source_run_id: "original-studio-run",
        settings: {
          ...DEFAULT_STORY_SETTINGS,
          duration: 7,
          steps: 4,
          style: "Original style",
        },
        request_id: "original-request",
      },
    };
    const state = { ...session(), pendingCreation };
    vi.mocked(useStorySession).mockReturnValue(state);
    const html = render();
    expect(html).toContain("Resume original creation");
    expect(html).toMatch(
      /<fieldset[^>]*disabled=""[^>]*aria-label="Opening setup"/,
    );
    expect(html).toContain("The original mystery on the train.");
    expect(html).toContain("Elira-original");
    expect(html).not.toContain("A mysterious note arrives at the cafe.");
    expect(html).toContain('value="Original style"');
    expect(html).toContain("7 seconds");
    expect(html).toContain("4 steps");
    expect(html).toContain("Continue from your Studio video");
    expect(html).not.toContain("Check connection");
    expect(state.create).not.toHaveBeenCalled();
    expect(state.resumeCreation).not.toHaveBeenCalled();
    expect(state.sendTurn).not.toHaveBeenCalled();
  });
  it("opens a newly confirmed game using its returned duration and id", async () => {
    const created = savedStory();
    created.active_run_id = undefined;
    created.turns = [];
    created.settings.duration = 7;
    const sendTurn = vi.fn().mockResolvedValue({});
    await openCreatedGame(created, sendTurn);
    expect(sendTurn).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining("Begin the story."),
      7,
      undefined,
      created.id,
    );
  });
  it("does not add another opening to a recovered Studio source or any existing turn", async () => {
    const created = savedStory(),
      sendTurn = vi.fn();
    created.turns = [];
    await openCreatedGame(created, sendTurn);
    created.active_run_id = undefined;
    for (const status of [
      "planning",
      "failed",
      "cancelled",
      "succeeded",
    ] as const) {
      created.turns = [{ ...savedStory().turns[0], status }];
      await openCreatedGame(created, sendTurn);
    }
    expect(sendTurn).not.toHaveBeenCalled();
  });
  it("asks for explicit approval when a saved turn needs review", () => {
    const value = savedStory();
    value.turns[1].status = "awaiting_review";
    vi.mocked(useStorySession).mockReturnValue(session(value));
    const html = render();
    expect(html).toContain("Render this scene");
    expect(html).toContain("Review or cancel the scene above");
    expect(html).toMatch(
      /<button class="primary" disabled="">(?:(?!<\/button>)[\s\S])*Queue my move/,
    );
  });
  it("keeps a failed response visible and names the saved-turn recovery", () => {
    const value = savedStory();
    value.turns[1].status = "failed";
    value.turns[1].error = "The local server disconnected.";
    vi.mocked(useStorySession).mockReturnValue(session(value));
    const html = render();
    expect(html).toContain("Check saved render");
    expect(html).toContain("The local server disconnected.");
    expect(html).toContain(plan.action);
  });
  it("keeps movement and the composer at the player, with history behind a separate view", () => {
    const state = session(savedStory());
    vi.mocked(useStorySession).mockReturnValue(state);
    const html = render();
    expect(html).toContain('aria-label="Game views"');
    expect(html).toContain('aria-label="Story conversation" hidden=""');
    const video = html.indexOf('aria-label="Game video"');
    const movement = html.indexOf('aria-label="Move forward"');
    const composer = html.indexOf('id="game-next-move"');
    const history = html.indexOf('aria-label="Story conversation"');
    expect(video).toBeLessThan(movement);
    expect(movement).toBeLessThan(composer);
    expect(composer).toBeLessThan(history);
    expect(html).toContain('<section class="game-context-actions"');
    expect(html).not.toContain('<details class="game-context-actions"');
    expect(html).toContain('Movement options');
    expect(state.sendTurn).not.toHaveBeenCalled();
    expect(state.turnAction).not.toHaveBeenCalled();
  });
  it("offers pre-plan AI recovery directly in Play without creating a new turn", () => {
    const value = savedStory();
    value.turns.push({id: "failed-plan", request_id: "failed-plan-request", status: "failed", message: "I move forward.", created_at: 3, error: "The assistant response was interrupted."});
    const state = session(value);
    vi.mocked(useStorySession).mockReturnValue(state);
    const html = render();
    const start = html.indexOf('class="game-attention-link"');
    const attention = html.slice(start, html.indexOf('class="game-player-meta"', start));
    expect(attention).toContain('class="primary"');
    expect(attention).toContain("Retry AI response");
    expect(attention).toContain("Review details");
    expect(state.sendTurn).not.toHaveBeenCalled();
    expect(state.turnAction).not.toHaveBeenCalled();
  });
});

describe("Game project and response isolation", () => {
  it("binds each character and outfit while tracking props separately from identity", () => {
    const uploads = [
      photo("elira-face"),
      photo("blue-dress"),
      photo("key"),
      photo("cafe"),
      photo("mood"),
    ];
    const game = gameProjectWithUploads(project(), uploads, {
      "elira-face": { kind: "person", personName: "Elira" },
      "blue-dress": { kind: "wardrobe", personName: "elira" },
      key: { kind: "object", personName: "Arin" },
      cafe: { kind: "place" },
      mood: { kind: "inspiration" },
    });
    const elira = game.subjects.find((person) => person.name === "Elira")!;
    expect(elira.asset_ids).toEqual(["elira-face", "blue-dress"]);
    expect(
      game.subjects.find((person) => person.name === "Arin")!.asset_ids,
    ).toEqual(["Arin"]);
    const key = game.assets.find((asset) => asset.id === "key")!;
    expect(key.simple_owner_id).toBe("arin");
    expect(gameReferenceOwner(game, key)).toBe("Arin");
    expect(
      game.assets.find((asset) => asset.id === "cafe")?.semantic_role,
    ).toBe("background");
    expect(
      gameReferenceKind(game.assets.find((asset) => asset.id === "mood")!),
    ).toBe("inspiration");
    expect(gameReferenceIssues(game)).toEqual([]);
  });
  it("adding another face does not replace an existing identity", () => {
    const source = project(),
      before = structuredClone(source);
    const game = gameProjectWithUploads(source, [photo("arin-profile")], {
      "arin-profile": { kind: "person", personName: "Arin" },
    });
    expect(game.subjects[0].asset_ids).toEqual(["Arin", "arin-profile"]);
    expect(game.assets.every((asset) => asset.enabled)).toBe(true);
    expect(source).toEqual(before);
  });
  it("explicit replacement carries the assigned name and tag to the selected new image only", () => {
    const source = project();
    source.assets[0].prompt_tag = "arin-face";
    const game = gameProjectWithUploads(
      source,
      [photo("other")],
      {},
      { Arin: photo("replacement") },
    );
    expect(
      game.assets.find((asset) => asset.id === "replacement"),
    ).toMatchObject({
      name: "Arin",
      prompt_tag: "arin-face",
      semantic_role: "face",
    });
    expect(game.subjects[0].asset_ids).toEqual(["replacement"]);
    expect(game.assets.some((asset) => asset.id === "other")).toBe(true);
    expect(source.subjects[0].asset_ids).toEqual(["Arin"]);
  });
  it("can switch off or remove a photo in the Game copy without losing Studio bindings", () => {
    const source = project();
    expect(
      gameProjectWithUploads(source, [], { Arin: { enabled: false } }).assets[0]
        .enabled,
    ).toBe(false);
    const removed = gameProjectWithUploads(source, [], {
      Arin: { removed: true },
    });
    expect(removed.assets).toEqual([]);
    expect(removed.subjects[0].asset_ids).toEqual([]);
    expect(source.assets[0].enabled).toBe(true);
    expect(source.subjects[0].asset_ids).toEqual(["Arin"]);
  });
  it("updates authored tag references while preserving exact spoken words", () => {
    const source = project();
    source.assets[0].prompt_tag = "arin-face";
    source.story.text = "@arin-face enters the cafe.";
    source.shots = [
      {
        id: "shot",
        duration: 5,
        action: "@arin-face sits.",
        setting: "",
        camera: {},
        performance: "",
        final_state: "",
        visible_subject_ids: ["arin"],
        offscreen_subject_ids: [],
        dialogue: [{ speaker_id: "arin", text: "@arin-face is my tag." }],
        sound: "",
        transition: "continuous",
      },
    ];
    const game = gameProjectWithUploads(source, [], {
      Arin: { tag: "@detective-face" },
    });
    expect(game.story.text).toBe("@detective-face enters the cafe.");
    expect(game.shots[0].action).toBe("@detective-face sits.");
    expect(game.shots[0].dialogue[0].text).toBe("@arin-face is my tag.");
    expect(source.story.text).toBe("@arin-face enters the cafe.");
  });
  it("catches ambiguous tags and missing clothing owners before starting", () => {
    const game = gameProjectWithUploads(project(), [photo("dress")], {
      Arin: { tag: "same" },
      dress: { kind: "wardrobe", tag: "same" },
    });
    expect(gameReferenceIssues(game).join(" ")).toContain(
      "@same is used twice",
    );
    expect(gameReferenceIssues(game).join(" ")).toContain(
      "who wears these clothes",
    );
    const invalid = gameProjectWithUploads(project(), [], {
      Arin: { tag: "wrong tag!" },
    });
    expect(gameReferenceIssues(invalid).join(" ")).toContain(
      "use a tag such as",
    );
  });
  it("adds uploaded references only to a cloned Game project", () => {
    const source = project(),
      uploads = [photo("Elira"), photo("Arin")],
      before = structuredClone(source);
    const game = gameProjectWithUploads(source, uploads);
    expect(game.assets.map((item) => item.id)).toEqual(["Arin", "Elira"]);
    game.assets[0].name = "Changed in Game";
    game.story.text = "A different story";
    expect(source).toEqual(before);
    expect(uploads[0].name).toBe("Elira");
  });
  it("exposes exact dialogue and new image descriptions without mutating the response", () => {
    const before = structuredClone(plan);
    const html = renderToStaticMarkup(
      <GamePlanEditor
        plan={plan}
        onSave={() => {}}
        onCancel={() => {}}
        submitting={false}
        actionLabel="Render edited response"
      />,
    );
    expect(html).toContain('aria-label="Speaker 1"');
    expect(html).toContain("Who sent it?");
    expect(html).toContain('aria-label="Reference image description 1"');
    expect(html).toContain("A folded cream envelope");
    expect(plan).toEqual(before);
  });
  it("renders observed memory as readable text", () => {
    expect(
      gameMemoryText({
        summary: "Arin holds the note.",
        internal: { graph: true },
      }),
    ).toBe("Arin holds the note.");
    expect(gameMemoryText({ internal: { graph: true } })).toBe("");
  });
});
