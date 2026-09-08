import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  normalizeStudioLinks,
  reconcileStudioLink,
  studioLinkDefinitive,
  studioLinkOperation,
  type StudioStoryLink,
} from "./useStudioStoryLinks";

const continuation: StudioStoryLink = {
  kind: "continuation",
  storyId: "story-a",
  projectId: "project-b",
  runId: "request-1",
  parent: "opening",
};
const alternate: StudioStoryLink = {
  kind: "alternate",
  storyId: "story-a",
  projectId: "project-b",
  runId: "request-2",
  originalRunId: "opening",
};
describe("persistent Studio links", () => {
  it("migrates a saved legacy continuation without losing its parent", () => {
    expect(
      normalizeStudioLinks(
        { storyId: "story-a", runId: "request-1", parent: "opening" },
        "project-b",
      ),
    ).toEqual([continuation]);
  });
  it("keeps multiple pending clips and rejects damaged scope or endpoint data", () => {
    expect(
      normalizeStudioLinks([
        continuation,
        alternate,
        continuation,
        { ...alternate, runId: "../bad" },
        { ...continuation, parent: "" },
      ]),
    ).toEqual([continuation, alternate]);
  });
  it("registers an alternate separately so it cannot be appended as a new scene", () => {
    expect(studioLinkOperation(alternate)).toEqual({
      path: "/stories/story-a/alternates",
      body: { run_id: "request-2", original_run_id: "opening" },
    });
  });
  it("waits for a running continuation without changing the accepted ending", async () => {
    const call = vi
      .fn()
      .mockResolvedValue({ id: "request-1", status: "running" });
    expect(await reconcileStudioLink(continuation, call)).toEqual({
      status: "waiting",
    });
    expect(call).toHaveBeenCalledTimes(1);
  });
  it("can reconnect a completed run solely from its persisted request ID", async () => {
    const story = { id: "story-a", active_run_id: "request-1" };
    const call = vi
      .fn()
      .mockResolvedValueOnce({ id: "request-1", status: "succeeded" })
      .mockResolvedValueOnce(story);
    expect(await reconcileStudioLink(continuation, call)).toEqual({
      status: "finished",
      story,
    });
    expect(call.mock.calls).toEqual([
      ["/video/runs/request-1"],
      [
        "/stories/story-a/attach",
        { run_id: "request-1", expected_parent: "opening" },
      ],
    ]);
  });
  it("registers a pending alternate for later selection without making it the endpoint", async () => {
    const story = { id: "story-a", active_run_id: "opening" };
    const call = vi
      .fn()
      .mockResolvedValueOnce({ id: "request-2", status: "queued" })
      .mockResolvedValueOnce(story);
    expect(
      (await reconcileStudioLink(alternate, call)).story.active_run_id,
    ).toBe("opening");
    expect(call.mock.calls[1][0]).toBe("/stories/story-a/alternates");
  });
  it("does not discard a pre-submission intent when admission has not happened yet", async () => {
    const call = vi
      .fn()
      .mockRejectedValue(new ApiError("This video run was not found.", 400));
    expect(await reconcileStudioLink(continuation, call)).toEqual({
      status: "waiting",
    });
  });
  it("distinguishes a transient connection/rate failure from a rejected parent", () => {
    expect(studioLinkDefinitive(new TypeError("Network failed"))).toBe(false);
    expect(studioLinkDefinitive(new ApiError("Unavailable", 503))).toBe(false);
    expect(studioLinkDefinitive(new ApiError("Slow down", 429))).toBe(false);
    expect(studioLinkDefinitive(new ApiError("The ending changed", 400))).toBe(
      true,
    );
  });
  it("preserves a transient attach failure for retry without making a video call", async () => {
    const call = vi
      .fn()
      .mockResolvedValueOnce({ id: "request-1", status: "succeeded" })
      .mockRejectedValueOnce(new TypeError("Network failed"));
    await expect(reconcileStudioLink(continuation, call)).rejects.toThrow(
      "Network failed",
    );
    expect(call.mock.calls.map((row) => row[0])).toEqual([
      "/video/runs/request-1",
      "/stories/story-a/attach",
    ]);
  });
});
