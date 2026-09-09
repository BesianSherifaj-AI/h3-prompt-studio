import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GameScenePanel, observedSceneText, type SceneCatalog } from "./GameScenePanel";
import type { Story } from "./storyTypes";

const story = (): Story => ({ id: "game", player_name: "Alex", player_character_id: "player", active_run_id: "accepted", active_branch_id: "branch", turns: [
  { id: "old", run_id: "accepted", status: "succeeded", observation: { observed_state: "A person in purple stands on the left." } },
  { id: "new", run_id: "new-result", status: "awaiting_acceptance", observation: { observed_state: "Three people stand beside the shops." } },
], world: { characters: [{ id: "player", name: "Alex", description: "" }], entities: [] } } as unknown as Story);
const scene = (): SceneCatalog => ({ run_id: "accepted", branch_id: "branch", configuration_revision: 1, status: "ready", setting: "Pixel street", targets: [
  { id: "person-left", kind: "person", known_id: null, label: "Person in purple", description: "Purple shirt and glasses", position: "Left side of the cobblestone street", identity_status: "unidentified", actions: [{ kind: "talk", label: "Talk", enabled: true, intent: { kind: "scene_target", scene_run_id: "accepted", candidate_id: "person-left", action: "talk" } }] },
] });
describe("saved ending grounding", () => {
  it("shows the viewed clip's actual observation, never the previous accepted clip's text", () => {
    expect(observedSceneText(story(), "new-result")).toBe("Three people stand beside the shops.");
    const html = renderToStaticMarkup(<GameScenePanel story={story()} viewedRunId="new-result" scene={scene()} disabled={false} onAction={vi.fn()}/>);
    expect(html).toContain("New ending · waiting for review");
    expect(html).toContain("Three people stand beside the shops.");
    expect(html).not.toContain("Person in purple");
    expect(html).toContain("Review this new ending above the video first");
  });
  it("labels an unbound visual candidate honestly without promoting it into world inventory", () => {
    const value = story(); value.turns = [value.turns[0]];
    const action = vi.fn(), bind = vi.fn();
    const html = renderToStaticMarkup(<GameScenePanel story={value} viewedRunId="accepted" scene={scene()} disabled={false} onAction={action} onBind={bind}/>);
    expect(html).toContain("Person in purple");
    expect(html).toContain("Left side of the cobblestone street");
    expect(html).toContain("Visible · identity not established");
    expect(html).toContain("Your appearance is not identified yet");
    expect(action).not.toHaveBeenCalled(); expect(bind).not.toHaveBeenCalled();
    expect(value.world!.entities).toEqual([]);
  });
  it("offers explicit inspection for a legacy accepted ending without claiming selectable people exist", () => {
    const value = story(); value.turns = [value.turns[0]];
    const inspect = vi.fn();
    const html = renderToStaticMarkup(<GameScenePanel story={value} viewedRunId="accepted" scene={{ ...scene(), status: "unavailable", targets: [] }} disabled={false} onAction={vi.fn()} onInspect={inspect}/>);
    expect(html).toContain("no saved selectable people or objects yet");
    expect(html).toContain("Inspect this ending");
    expect(html).toContain("does not render another video");
    expect(inspect).not.toHaveBeenCalled();
  });
  it("waits for an in-flight inspection instead of offering another request", () => {
    const html = renderToStaticMarkup(<GameScenePanel story={story()} viewedRunId="accepted" scene={{ ...scene(), status: "unavailable", targets: [], inspection: { status: "running" } }} disabled={false} onAction={vi.fn()} onInspect={vi.fn()}/>);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Inspecting the ending…<\/button>/);
  });
  it("marks inherited positions stale after a movement with no language-model inspection", () => {
    const html = renderToStaticMarkup(<GameScenePanel story={story()} viewedRunId="accepted" scene={{ ...scene(), status: "stale", inspected_run_id: "earlier" }} disabled={false} onAction={vi.fn()} onBind={vi.fn()} onInspect={vi.fn()}/>);
    expect(html).toContain("positions in this new ending have not been checked");
    expect(html).toContain("Previously: Left side of the cobblestone street");
    expect(html).toContain("Inspect this ending");
    expect(html).not.toContain(">This is me</button>");
  });
});
