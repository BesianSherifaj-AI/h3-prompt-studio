import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { api, ApiError } from "./api";
import type { Project } from "./model";
import { DEFAULT_STORY_SETTINGS, type Story } from "./storyTypes";
import {
  readPendingStoryCreation,
  useStorySession,
  type PendingStoryCreation,
} from "./useStorySession";

vi.mock("./api", async () => ({
  ...(await vi.importActual<typeof import("./api")>("./api")),
  api: vi.fn(),
}));

const CREATE = "h3-game:pending-create";
const project = (): Project => ({
  id: "original-project",
  schema_version: 1,
  title: "Original",
  mode: "ref2va",
  duration: 5,
  aspect_ratio: "16:9",
  profile: "director",
  authoring_mode: "manual",
  story: { text: "The original opening.", locked: true },
  style: {},
  assets: [],
  subjects: [],
  shots: [],
  soundscape: "",
  music: "",
  custom_instructions: "",
});

describe("Game turn acknowledgement and recovery", () => {
  async function selectedSession() {
    const session = captureSession();
    vi.mocked(api).mockResolvedValueOnce(story());
    await session.selectStory(story().id);
    vi.mocked(api).mockReset();
    return session;
  }
  const savedTurn = (requestId: string) => ({
    id: requestId, request_id: requestId, status: "planning" as const,
    message: "I open the door.", created_at: 1,
  });
  const storedTicket = () => JSON.parse(localStorage.getItem("h3-game:pending:confirmed-game") || "null");

  it("retains the exact original body after an unreadable acknowledgement and later edits", async () => {
    const session = await selectedSession();
    const plan = { action: "I open the door.", dialogue: [], transition: "continue" as const,
      setting: "Street", final_state: "The door is open.", choices: [], asset_requests: [] };
    vi.mocked(api).mockResolvedValueOnce({ accepted: true }).mockResolvedValueOnce(story());
    await expect(session.sendTurn("I open the door.", 3, plan, undefined, undefined, "stable-request-original")).rejects.toThrow("not confirmed");
    const original = structuredClone(storedTicket());
    expect(original.requestId).toBe("stable-request-original");
    plan.action = "An edited instruction must not replace an uncertain request.";
    vi.mocked(api).mockResolvedValueOnce(savedTurn(original.requestId)).mockResolvedValueOnce(story());
    await session.resumePending();
    expect(vi.mocked(api).mock.calls[2][1]).toEqual(original.body);
    expect(storedTicket()).toBeNull();
  });

  it("reconciles a lost acknowledgement without pausing a successfully saved move or sending it twice", async () => {
    const session = await selectedSession(), turn = savedTurn("stable-request-recovered");
    vi.mocked(api).mockRejectedValueOnce(new TypeError("Connection interrupted"))
      .mockResolvedValueOnce({ ...story(), turns: [turn] });
    await expect(session.sendTurn(turn.message, 3, undefined, undefined, undefined, turn.id)).resolves.toEqual(turn);
    expect(storedTicket()).toBeNull();
    expect(vi.mocked(api).mock.calls.filter(call => call[1] !== undefined)).toHaveLength(1);
  });

  it("a confirmed turn unlocks promptly even when its following status read stalls", async () => {
    const session = await selectedSession(), turn = savedTurn("stable-request-confirmed");
    vi.mocked(api).mockResolvedValueOnce(turn).mockImplementationOnce(() => new Promise(() => {}));
    await expect(session.sendTurn(turn.message, 3, undefined, undefined, undefined, turn.id)).resolves.toEqual(turn);
    expect(storedTicket()).toBeNull();
    await expect(session.sendTurn("A second move")).rejects.toMatchObject({ notSubmitted: true });
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("marks local overlapping sends as unsubmitted so their queue entries remain retryable", async () => {
    const session = await selectedSession();
    let finish!: (value: unknown) => void;
    vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const first = session.sendTurn("I open the door.", 3, undefined, undefined, undefined, "stable-request-first");
    await expect(session.sendTurn("I look inside.")).rejects.toMatchObject({ notSubmitted: true });
    expect(api).toHaveBeenCalledTimes(1);
    vi.mocked(api).mockResolvedValueOnce(story());
    finish(savedTurn("stable-request-first"));
    await first;
  });

  it("definitive rejections unlock correction even if the following read fails", async () => {
    const session = await selectedSession();
    vi.mocked(api).mockRejectedValueOnce(new ApiError("Choose a visible target.", 400))
      .mockRejectedValueOnce(new TypeError("Offline"));
    await expect(session.sendTurn("I open the door.")).rejects.toMatchObject({ notSubmitted: true });
    expect(storedTicket()).toBeNull();
  });

  it("an HTTP request timeout keeps its saved UUID because server acceptance is unknown", async () => {
    const session = await selectedSession();
    vi.mocked(api).mockRejectedValueOnce(new ApiError("Request timed out", 408)).mockResolvedValueOnce(story());
    await expect(session.sendTurn("I open the door.", 3, undefined, undefined, undefined, "stable-request-timeout")).rejects.toThrow("timed out");
    expect(storedTicket()?.requestId).toBe("stable-request-timeout");
  });

  it("ignores a late read from the previously selected story", async () => {
    const session = await selectedSession();
    let finish!: (value: unknown) => void;
    vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const stale = session.refresh();
    const other = { ...story(), id: "other-game", active_run_id: "other-ending" };
    vi.mocked(api).mockResolvedValueOnce(other);
    await session.selectStory(other.id);
    finish({ ...story(), active_run_id: "stale-ending" });
    await stale;
    vi.mocked(api).mockResolvedValueOnce(savedTurn("stable-request-new-story")).mockResolvedValueOnce(other);
    await session.sendTurn("Look around.", 3, undefined, undefined, undefined, "stable-request-new-story");
    expect(vi.mocked(api).mock.calls[2][0]).toBe("/stories/other-game/turns");
    expect(vi.mocked(api).mock.calls[2][1].expected_parent).toBe("other-ending");
  });
});
const pending = (): PendingStoryCreation => ({
  requestId: "original-creation-request",
  body: {
    project: project(),
    mode: "game",
    premise: "The original opening.",
    player_name: "Elira",
    source_run_id: "original-source",
    settings: { ...DEFAULT_STORY_SETTINGS, duration: 7 },
    request_id: "original-creation-request",
  },
});
const story = (): Story => ({
  id: "confirmed-game",
  project_id: "original-project",
  title: "Original",
  mode: "game",
  premise: "The original opening.",
  player_name: "Elira",
  settings: { ...DEFAULT_STORY_SETTINGS, duration: 7 },
  active_branch_id: "main",
  active_run_id: "original-source",
  turns: [],
  clips: [],
  choices: [],
  create_request_id: "original-creation-request",
});

// Server rendering captures real hook closures without a DOM, effects, or live API calls.
// The refs used to serialize and preserve requests remain available to explicit actions.
function captureSession() {
  let result: ReturnType<typeof useStorySession>;
  function Probe() {
    result = useStorySession();
    return null;
  }
  renderToStaticMarkup(<Probe />);
  return result!;
}
beforeEach(() => {
  vi.mocked(api).mockReset();
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("unconfirmed Game creation", () => {
  it("exposes a saved original creation immediately without sending a request", () => {
    localStorage.setItem(CREATE, JSON.stringify(pending()));
    const session = captureSession();
    expect(session.pendingCreation).toEqual(pending());
    expect(api).not.toHaveBeenCalled();
  });

  it("rejects a different creation and explicitly retries the exact original body and UUID", async () => {
    const session = captureSession(),
      original = project();
    const settings = { ...DEFAULT_STORY_SETTINGS, duration: 7 };
    vi.mocked(api).mockRejectedValueOnce(new TypeError("Response lost"));
    await expect(
      session.create(original, {
        premise: original.story.text,
        player_name: "Elira",
        settings,
        source_run_id: "original-source",
      }),
    ).rejects.toThrow("Response lost");
    const firstBody = structuredClone(vi.mocked(api).mock.calls[0][1]);
    original.story.text = "A changed opening that must not be sent.";
    settings.duration = 13;
    await expect(
      session.create(original, {
        premise: original.story.text,
        player_name: "Arin",
        settings,
      }),
    ).rejects.toThrow("Resume original creation");
    expect(api).toHaveBeenCalledTimes(1);
    expect(readPendingStoryCreation()?.body).toEqual(firstBody);

    vi.mocked(api)
      .mockResolvedValueOnce({ stories: [] })
      .mockResolvedValueOnce(story());
    await expect(session.resumeCreation()).resolves.toMatchObject({
      id: "confirmed-game",
    });
    expect(api).toHaveBeenNthCalledWith(2, "/stories", undefined, undefined, undefined, { timeoutMs: 15000 });
    expect(api).toHaveBeenNthCalledWith(3, "/stories", firstBody, undefined, undefined, { timeoutMs: 30000 });
    expect(localStorage.getItem(CREATE)).toBeNull();
    expect(localStorage.getItem("h3-game:selected-story")).toBe(
      "confirmed-game",
    );
  });

  it("reconciles a confirmed creation using its list request ID without posting it again", async () => {
    localStorage.setItem(CREATE, JSON.stringify(pending()));
    const session = captureSession();
    vi.mocked(api)
      .mockResolvedValueOnce({
        stories: [
          { id: "confirmed-game", create_request_id: pending().requestId },
        ],
      })
      .mockResolvedValueOnce(story());
    await expect(session.resumeCreation()).resolves.toEqual(story());
    expect(api).toHaveBeenNthCalledWith(1, "/stories", undefined, undefined, undefined, { timeoutMs: 15000 });
    expect(api).toHaveBeenNthCalledWith(2, "/stories/confirmed-game", undefined, undefined, undefined, { timeoutMs: 15000 });
    expect(api).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem(CREATE)).toBeNull();
  });

  it("keeps the original request if reconciliation fails and never submits after a failed read", async () => {
    localStorage.setItem(CREATE, JSON.stringify(pending()));
    const session = captureSession();
    vi.mocked(api).mockRejectedValueOnce(new ApiError("Reconnect first", 403));
    await expect(session.resumeCreation()).rejects.toThrow("Reconnect first");
    expect(readPendingStoryCreation()).toEqual(pending());
    expect(api).toHaveBeenCalledExactlyOnceWith("/stories", undefined, undefined, undefined, { timeoutMs: 15000 });
  });

  it("does not clear an original request when a matching list entry has an incomplete full response", async () => {
    localStorage.setItem(CREATE, JSON.stringify(pending()));
    const session = captureSession();
    vi.mocked(api)
      .mockResolvedValueOnce({
        stories: [
          { id: "confirmed-game", create_request_id: pending().requestId },
        ],
      })
      .mockResolvedValueOnce({ id: "confirmed-game" });
    await expect(session.resumeCreation()).rejects.toThrow(
      "could not be restored",
    );
    expect(readPendingStoryCreation()).toEqual(pending());
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("serializes repeated resume clicks while the original request is being checked", async () => {
    localStorage.setItem(CREATE, JSON.stringify(pending()));
    const session = captureSession();
    let finish!: (value: unknown) => void;
    vi.mocked(api).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const first = session.resumeCreation();
    await expect(session.resumeCreation()).rejects.toThrow("previous action");
    expect(api).toHaveBeenCalledTimes(1);
    vi.mocked(api).mockResolvedValueOnce(story());
    finish({ stories: [] });
    await first;
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("clears a definitively rejected POST so the setup can be corrected", async () => {
    const session = captureSession();
    vi.mocked(api).mockRejectedValueOnce(new ApiError("Invalid source", 422));
    await expect(
      session.create(project(), {
        premise: "Original",
        player_name: "Elira",
        settings: { ...DEFAULT_STORY_SETTINGS },
      }),
    ).rejects.toThrow("Invalid source");
    expect(localStorage.getItem(CREATE)).toBeNull();
    vi.mocked(api).mockResolvedValueOnce(story());
    await expect(
      session.create(project(), {
        premise: "Corrected",
        player_name: "Elira",
        settings: { ...DEFAULT_STORY_SETTINGS },
      }),
    ).resolves.toMatchObject({ id: "confirmed-game" });
    expect(vi.mocked(api).mock.calls[0][1].request_id).not.toBe(
      vi.mocked(api).mock.calls[1][1].request_id,
    );
  });

  it.each([
    "not-json",
    "null",
    JSON.stringify({ requestId: "wrong", body: pending().body }),
    JSON.stringify({ ...pending(), body: { ...pending().body, project: {} } }),
  ])("ignores malformed local creation data %s", (saved) => {
    localStorage.setItem(CREATE, saved);
    expect(readPendingStoryCreation()).toBeNull();
    expect(captureSession().pendingCreation).toBeNull();
    expect(api).not.toHaveBeenCalled();
  });
});
