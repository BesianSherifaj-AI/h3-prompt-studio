import { describe, expect, it } from "vitest";
import { queueAfterFailure, queueDecision, restoreMoveQueue, type MoveQueue } from "./GameActionQueue";
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
