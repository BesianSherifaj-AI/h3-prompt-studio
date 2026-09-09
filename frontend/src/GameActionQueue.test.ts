import { describe, expect, it } from "vitest";
import { queueAfterFailure, queueAfterRecovery, queueDecision, restoreMoveQueue, type MoveQueue, type QueueRecovery } from "./GameActionQueue";
import type { Story } from "./storyTypes";
const story = { active_branch_id: "branch", turns: [] } as unknown as Story;
const queue = (): MoveQueue => ({ branch: "branch", paused: false, error: "", items: [
  { id: "first", message: "I approach the door", readyAt: 3000, submitted: false },
  { id: "second", message: "I try the handle", readyAt: 3100, submitted: false },
] });
describe("separate scene queue", () => {
  it("holds the first action for three seconds and never sends while a scene is busy", () => {
    expect(queueDecision(queue(), story, false, 2999)).toBe("idle");
    expect(queueDecision(queue(), story, false, 3000)).toBe("send");
    expect(queueDecision(queue(), story, true, 9000)).toBe("idle");
  });
  it("keeps each request separate and waits for acceptance before advancing", () => {
    const q = queue(); q.items[0].submitted = true;
    expect(queueDecision(q, story, false, 9000)).toBe("waiting");
    expect(queueDecision(q, { ...story, turns: [{ id: "first", status: "observing" }] } as Story, false, 9000)).toBe("waiting");
    expect(queueDecision(q, { ...story, turns: [{ id: "first", status: "succeeded" }] } as Story, false, 9000)).toBe("complete");
    expect(q.items[1].message).toBe("I try the handle");
  });
  it("pauses on failures, cancellations and changed branches without queueing a replacement", () => {
    for (const status of ["failed", "cancelled", "uncertain", "inspection_failed"]) {
      expect(queueDecision(queue(), { ...story, turns: [{ id: "first", status }] } as Story, false, 9000)).toBe("attention");
    }
    expect(queueDecision(queue(), { ...story, active_branch_id: "other" }, false, 9000)).toBe("branch-changed");
  });
  it("restores original IDs and unconfirmed sends but never autostarts after refresh", () => {
    const q = queue(); q.items[0].submitted = true;
    const restored = restoreMoveQueue(JSON.stringify(q));
    expect(restored.paused).toBe(true);
    expect(restored.items).toEqual(q.items);
    expect(queueDecision(restored, story, false, 9000)).toBe("waiting");
    restored.items[0].submitted = false;
    expect(queueDecision(restored, story, false, 9000)).toBe("idle");
  });
  it("rejects broken persistence and does not dispatch an emptied action", () => {
    expect(restoreMoveQueue('{"items":[{}]}').items).toEqual([]);
    const q = queue(); q.items[0].message = " ";
    expect(queueDecision(q, story, false, 9000)).toBe("idle");
  });
  it("does not restore duplicate IDs or malformed queue entries", () => {
    const q = queue(); q.items[1].id = q.items[0].id;
    expect(restoreMoveQueue(JSON.stringify(q)).items).toEqual([]);
    expect(restoreMoveQueue(JSON.stringify({ ...queue(), items: [null] })).items).toEqual([]);
    expect(restoreMoveQueue(JSON.stringify({ ...queue(), error: 123 })).error).toMatch(/restored/);
  });
  it("restores only well-formed structured interactions", () => {
    const q = queue(); q.items[0].intent = { kind: "give", target_id: "key", recipient_id: "guard" };
    expect(restoreMoveQueue(JSON.stringify(q)).items[0].intent).toEqual(q.items[0].intent);
    const broken = JSON.parse(JSON.stringify(q)); broken.items[0].intent.target_id = { id: "key" };
    expect(restoreMoveQueue(JSON.stringify(broken)).items[0].intent).toBeUndefined();
  });
  it("keeps the original queue recoverable when a send fails after switching games", () => {
    const q = queue(); q.items[0].submitted = true;
    const recovered = queueAfterFailure(restoreMoveQueue(JSON.stringify(q)), "first", { message: "Wait for the previous request.", notSubmitted: true });
    expect(recovered.paused).toBe(true);
    expect(recovered.items[0].submitted).toBe(false);
    expect(recovered.items[1]).toEqual(q.items[1]);
    expect(queueDecision({ ...recovered, paused: false }, story, false, 9000)).toBe("send");
    const cleared = { ...q, items: [] };
    expect(queueAfterFailure(cleared, "first", { message: "Late failure" })).toBe(cleared);
  });
});

describe("explicit player-selection recovery", () => {
  const recovery: QueueRecovery = { sourceId: "first", failedTurnId: "failed-turn", blockedStage: "player-selection",
    message: "Move forward as the selected person.", intent: { kind: "move", direction: "forward" } };
  const failedStory = (extra: Record<string, unknown> = {}) => ({ ...story,
    turns: [{ id: "failed-turn", request_id: "first", status: "failed", ...extra }] }) as Story;

  it("replaces the failed head with a fresh request and preserves every later move in order", () => {
    const q = queue(); q.items[0].submitted = true; q.paused = true; q.error = "Identify your player.";
    const before = structuredClone(q);
    const next = queueAfterRecovery(q, failedStory(), recovery, 1000, "replacement")!;
    expect(next.items).toHaveLength(2);
    expect(next.items[0]).toEqual({ id: "replacement", message: recovery.message, intent: recovery.intent,
      readyAt: 4000, submitted: false, recoverySourceId: "first" });
    expect(next.items[1]).toEqual(q.items[1]);
    expect(next.paused).toBe(false);
    expect(next.error).toBe("");
    expect(q).toEqual(before);
    expect(queueDecision(next, failedStory(), false, 3999)).toBe("idle");
    expect(queueDecision(next, failedStory(), false, 4000)).toBe("send");
    expect(queueDecision(next, failedStory(), true, 9000)).toBe("idle");
  });

  it("prepends a missing failed move ahead of later queued entries without duplicating recovery", () => {
    const q = queue(); q.items.shift();
    const next = queueAfterRecovery(q, failedStory(), recovery, 10, "replacement")!;
    expect(next.items.map(item => item.id)).toEqual(["replacement", "second"]);
    expect(queueAfterRecovery(next, failedStory(), recovery, 20, "duplicate")).toBeNull();
    const restored = restoreMoveQueue(JSON.stringify(next));
    expect(restored.items[0].recoverySourceId).toBe("first");
    expect(restored.paused).toBe(true);
    expect(queueAfterRecovery(restored, failedStory(), recovery, 30, "after-refresh")).toBeNull();
  });

  it("recovers an unsent preflight head without requiring a fabricated server turn", () => {
    const q = queueAfterFailure(queue(), "first", { notSubmitted: true, message: "Pick the player." });
    const next = queueAfterRecovery(q, story, { ...recovery, failedTurnId: undefined }, 500, "new-preflight")!;
    expect(next.items.map(item => item.id)).toEqual(["new-preflight", "second"]);
    expect(next.items[0].submitted).toBe(false);
    expect(next.paused).toBe(false);
    expect(queueAfterRecovery(next, story, { ...recovery, failedTurnId: undefined }, 600, "duplicate")).toBeNull();
  });

  it.each(["planning", "rendering", "uncertain", "inspection_failed", "awaiting_acceptance", "succeeded", "cancelled"])(
    "does not replace a submitted %s turn", status => {
      const q = queue(); q.items[0].submitted = true;
      expect(queueAfterRecovery(q, failedStory({ status }), recovery, 0, "replacement")).toBeNull();
    });

  it("never treats an unacknowledged submitted request as an unsent preflight", () => {
    const q = queue(); q.items[0].submitted = true;
    expect(queueAfterRecovery(q, story, { ...recovery, failedTurnId: undefined }, 0, "replacement")).toBeNull();
    expect(queueAfterRecovery(q, story, recovery, 0, "replacement")).toBeNull();
    q.items[0].submitted = false;
    expect(queueAfterRecovery(q, failedStory(), { ...recovery, failedTurnId: undefined }, 0, "replacement")).toBeNull();
  });

  it.each([{ run_id: "video" }, { video: { id: "video" } }, { project_id: "render-project" },
    { asset_jobs: ["image-job"] }, { created_assets: [{ id: "image" }] }])("does not replace a failed turn with saved child work: %j", extra => {
      expect(queueAfterRecovery(queue(), failedStory(extra), recovery, 0, "replacement")).toBeNull();
    });

  it("requires the exact failed request and current branch, and never skips an unresolved head", () => {
    expect(queueAfterRecovery(queue(), failedStory({ request_id: "other" }), recovery, 0, "replacement")).toBeNull();
    expect(queueAfterRecovery({ ...queue(), branch: "other" }, failedStory(), recovery, 0, "replacement")).toBeNull();
    expect(queueAfterRecovery(queue(), failedStory({ branch_id: "other" }), recovery, 0, "replacement")).toBeNull();
    const q = queue(); q.items.shift(); q.items[0].submitted = true;
    expect(queueAfterRecovery(q, failedStory(), recovery, 0, "replacement")).toBeNull();
  });

  it("keeps the 12-move limit while allowing replacement of an existing head", () => {
    const q = queue();
    for (let i = 2; i < 12; i++) q.items.push({ id: `later-${i}`, message: "Wait", readyAt: 3000, submitted: false });
    expect(queueAfterRecovery(q, failedStory(), recovery, 0, "replacement")?.items).toHaveLength(12);
    q.items[0].id = "different-queued-move";
    expect(queueAfterRecovery(q, failedStory(), recovery, 0, "replacement")).toBeNull();
  });

  it("does not reuse old request IDs, duplicate another move, or dispatch empty edited text", () => {
    expect(queueAfterRecovery(queue(), failedStory(), recovery, 0, "first")).toBeNull();
    expect(queueAfterRecovery(queue(), failedStory(), recovery, 0, "second")).toBeNull();
    expect(queueAfterRecovery(queue(), failedStory(), { ...recovery, message: " " }, 0, "replacement")).toBeNull();
  });
});
