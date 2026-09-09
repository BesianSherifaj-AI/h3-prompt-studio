import { describe, expect, it } from "vitest";
import { movementNeedsPlayerChoice, playerIdentityFailure } from "./gamePlayerRecovery";
import type { Story, StoryTurn } from "./storyTypes";

describe("player choice recovery", () => {
  it("recognizes the old blocked move without treating submitted renders as identity retries", () => {
    const turn = { id: "failed", status: "failed", error: "Identify your character first: inspect this ending." } as StoryTurn;
    expect(playerIdentityFailure(turn)).toBe(true);
    expect(playerIdentityFailure({ ...turn, run_id: "existing-render" })).toBe(false);
    expect(playerIdentityFailure({ ...turn, status: "uncertain" })).toBe(false);
  });
  it("catches an unscanned empty player before queue submission and preserves camera movement", () => {
    const story = { active_run_id: "image", player_character_id: "p", world: { characters: [{ id: "p", description: "", state: {} }] }, project: {} } as Story;
    expect(movementNeedsPlayerChoice(story, { kind: "move", direction: "left" })).toBe(true);
    expect(movementNeedsPlayerChoice(story, { kind: "move", camera: "camera" })).toBe(false);
    story.world!.characters[0].description = "The person in the orange jacket";
    expect(movementNeedsPlayerChoice(story, { kind: "move", direction: "left" })).toBe(false);
  });
});
