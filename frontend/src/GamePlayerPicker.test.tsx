import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GamePlayerPicker, playerPickerRequests, playerPickerScope, samePickerFrame, selectablePlayers } from "./GamePlayerPicker";
import type { SceneCatalog } from "./GameScenePanel";
import { DEFAULT_STORY_SETTINGS, type Story } from "./storyTypes";
const story = (): Story => ({ id: "s", mode: "game", active_run_id: "ending", active_branch_id: "branch", configuration_revision: 3, title: "Street", player_name: "Alex", player_character_id: "player", settings: { ...DEFAULT_STORY_SETTINGS }, project_id: "p", premise: "", turns: [], choices: [], clips: [{ id: "ending", project_id: "p", status: "succeeded", video_url: "/api/video/runs/ending/video", ending_image_url: "/api/video/runs/ending/ending" }] });
const scope = () => playerPickerScope(story());
const catalog = (): SceneCatalog => ({ run_id: "ending", branch_id: "branch", configuration_revision: 3, status: "ready", targets: [
  { id: "a", kind: "person", label: "Purple shirt", description: "Glasses", position: "Left", known_id: null, identity_status: "unidentified", actions: [] },
  { id: "b", kind: "person", label: "Mara", description: "Jacket", position: "Right", known_id: "npc", identity_status: "known", actions: [] },
  { id: "c", kind: "door", label: "Door", description: "Wood", position: "Behind", known_id: null, identity_status: "unidentified", actions: [] },
] });
describe("one-choice player picker", () => {
  it("explains how to start when there is no ending instead of finding people forever", () => {
    const empty = { ...story(), active_run_id: null, clips: [] };
    const html = renderToStaticMarkup(<GamePlayerPicker story={empty} request={{ nonce: "empty" }} scope={playerPickerScope(empty)} onCancel={vi.fn()} onRestart={vi.fn()} onChosen={vi.fn()}/>);
    expect(html).toContain("Create your first scene");
    expect(html).not.toContain("Finding people");
    expect(html).not.toContain("Describe my character instead");
  });
  it("shows the actual saved ending and keeps the full move pending without choosing or sending on render", () => {
    const chosen = vi.fn();
    const html = renderToStaticMarkup(<GamePlayerPicker story={story()} request={{ nonce: "once", message: "Original detailed move", intent: { kind: "move", direction: "left", extent: "nearby", speed: "slow", presentation: "continuous", camera: "player" } }} scope={scope()} onCancel={vi.fn()} onRestart={vi.fn()} onChosen={chosen}/>);
    expect(html).toContain('role="dialog"');
    expect(html).toContain("Who are you playing?");
    expect(html).toContain("Choose once. Your move continues after you pick.");
    expect(html).toContain('src="/api/video/runs/ending/ending"');
    expect(html).toContain("Describe my character instead");
    expect(html).toContain("Cancel this move");
    expect(chosen).not.toHaveBeenCalled();
  });
  it("allows only fresh people who are unbound or already the player", () => {
    expect(selectablePlayers(catalog(), scope(), "player").map(person => person.id)).toEqual(["a"]);
    expect(selectablePlayers({ ...catalog(), status: "stale" }, scope(), "player")).toEqual([]);
    expect(selectablePlayers({ ...catalog(), configuration_revision: 2 }, scope(), "player")).toEqual([]);
    expect(selectablePlayers({ ...catalog(), run_id: "old" }, scope(), "player")).toEqual([]);
    expect(selectablePlayers({ ...catalog(), status: "pending_review" }, scope(), "player")).toEqual([]);
  });
  it("freezes both the accepted frame and settings before binding", () => {
    expect(samePickerFrame(scope(), story())).toBe(true);
    for (const change of [{ active_run_id: "new" }, { active_branch_id: "new" }, { configuration_revision: 4 }]) expect(samePickerFrame(scope(), { ...story(), ...change })).toBe(false);
  });
  it("coalesces a repeated scan click and reuses an uncertain request UUID", async () => {
    let reject!: (reason: Error) => void;
    const call = vi.fn().mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; })).mockResolvedValue({ status: "pending" });
    const requests = playerPickerRequests(scope(), call);
    const first = requests.scan();
    expect(requests.scan()).toBe(first);
    expect(call).toHaveBeenCalledTimes(1);
    const body = structuredClone(call.mock.calls[0][1]);
    reject(new Error("Response lost")); await expect(first).rejects.toThrow("Response lost");
    await requests.scan();
    expect(call.mock.calls[1][1]).toEqual(body);
    await requests.scan(true);
    expect(call.mock.calls[2][1].request_id).not.toBe(body.request_id);
  });
  it("preserves exact candidate and appearance binding requests across an unreadable response", async () => {
    const call = vi.fn().mockRejectedValueOnce(new Error("Lost")).mockResolvedValue(story());
    const requests = playerPickerRequests(scope(), call);
    await expect(requests.bind({ appearance: "The person on the left in purple" })).rejects.toThrow("Lost");
    const first = structuredClone(call.mock.calls[0][1]);
    await requests.bind({ appearance: "The person on the left in purple" });
    expect(call.mock.calls[1][1]).toEqual(first);
    expect(first).toMatchObject({ run_id: "ending", branch_id: "branch", configuration_revision: 3, appearance: "The person on the left in purple" });
    expect(first).not.toHaveProperty("candidate_id");
    await requests.bind({ candidate_id: "a" });
    expect(call.mock.calls[2][1]).toHaveProperty("candidate_id", "a");
    expect(call.mock.calls[2][1]).not.toHaveProperty("appearance");
  });
  it("does not submit a second binding while the same person is being saved", async () => {
    let finish!: (value: unknown) => void;
    const call = vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const requests = playerPickerRequests(scope(), call);
    const first = requests.bind({ candidate_id: "a" });
    expect(requests.bind({ candidate_id: "a" })).toBe(first);
    expect(call).toHaveBeenCalledTimes(1);
    finish(story()); await first;
  });
});
