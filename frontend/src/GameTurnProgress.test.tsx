import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GameTurnProgress } from "./GameTurnProgress";
import type { MoveQueue } from "./GameActionQueue";
import type { StoryTurn } from "./storyTypes";
const queue = (): MoveQueue => ({ branch: "b", paused: false, error: "", items: [{ id: "move", message: "I move forward.", readyAt: 3000, submitted: false }] });
const turn = (status: StoryTurn["status"]): StoryTurn => ({ id: "move", request_id: "move", message: "I move forward.", status, created_at: 1 });
describe("movement feedback", () => {
  it("replaces legacy identity failures with a direct character choice, then shows the recovered move countdown", () => {
    const failed = { ...turn("failed"), error: "Identify your character first: inspect this ending and select This is me." };
    const empty = { ...queue(), items: [] };
    const html = renderToStaticMarkup(<GameTurnProgress turn={failed} queue={empty} now={0} onReview={vi.fn()} onChoosePlayer={vi.fn()}/>);
    expect(html).toContain("Choose character &amp; continue");
    expect(html).not.toContain("Review failed move");
    expect(html).not.toContain("inspect this ending");
    const resumed = queue(); resumed.items[0].id = "new-request";
    const next = renderToStaticMarkup(<GameTurnProgress turn={failed} queue={resumed} now={0} onReview={vi.fn()} onChoosePlayer={vi.fn()}/>);
    expect(next).toContain("Move queued · starts in 3s");
    expect(next).not.toContain("Choose character &amp; continue");
  });
  it("acknowledges a click immediately throughout the edit countdown", () => {
    const html = renderToStaticMarkup(<GameTurnProgress queue={queue()} now={0} onReview={vi.fn()}/>);
    expect(html).toContain("Move queued · starts in 3s");
    expect(html).toContain("I move forward.");
    expect(html).toContain('aria-current="step">Queued');
  });
  it("keeps sending visible while the request acknowledgement is pending", () => {
    const value = queue(); value.items[0].submitted = true;
    const html = renderToStaticMarkup(<GameTurnProgress queue={value} now={4000} onReview={vi.fn()}/>);
    expect(html).toContain("waiting for acknowledgement");
    expect(html).not.toContain("Ready for your next move");
  });
  it.each([['planning', 'Writing'], ['rendering', 'Rendering']] as const)("shows the actual %s stage", (status, label) => {
    const html = renderToStaticMarkup(<GameTurnProgress turn={turn(status)} queue={{ ...queue(), items: [] }} now={4000} onReview={vi.fn()}/>);
    expect(html).toContain(`aria-current="step">${label}`);
  });
  it("shows continuity mismatch and explicitly explains why later movements are waiting", () => {
    const value = turn("awaiting_acceptance"); value.observation = { continuity_checks: [{ status: "mismatch", detail: "Two unestablished people are visible beside the player." }] };
    const review = vi.fn();
    const html = renderToStaticMarkup(<GameTurnProgress turn={value} queue={queue()} now={4000} onReview={review}><button>Use visible result</button></GameTurnProgress>);
    expect(html).toContain("New video ready · your review is needed");
    expect(html).toContain("Two unestablished people are visible");
    expect(html).toContain("waiting for your review");
    expect(html).toContain("Use visible result");
    expect(review).not.toHaveBeenCalled();
  });
  it("does not promise AI writing or inspection for deterministic movement", () => {
    const value = { ...turn("planning"), planning_mode: "deterministic_movement" };
    const html = renderToStaticMarkup(<GameTurnProgress turn={value} queue={{ ...queue(), items: [] }} now={4000} onReview={vi.fn()}/>);
    expect(html).toContain("Preparing movement");
    expect(html).toContain("No language model is called for this turn");
    expect(html).not.toContain(">Writing</li>");
  });
});
