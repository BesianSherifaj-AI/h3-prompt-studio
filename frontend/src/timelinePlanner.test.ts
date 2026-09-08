import { describe, expect, it } from "vitest";
import { newShot } from "./model";
import type { Asset, Project } from "./model";
import { continuationEnding, createContinuation, setContinuousTake, setTimelineBoundary, splitSceneAt, splitTimelineAt, timelineRanges, twoScenesInFirstFiveSeconds } from "./timelineHelpers";

function project(durations = [15]): Project {
  return {
    schema_version: 1, id: "original", title: "Gift story", mode: "ref2va", duration: durations.reduce((a, b) => a + b, 0),
    aspect_ratio: "16:9", profile: "concise", authoring_mode: "full", story: { text: "Mira gives Nora a box.", locked: true },
    style: { vibe: "warm" }, assets: [], subjects: [{ id: "mira", name: "Mira", asset_ids: ["face", "dress"], description: "" }],
    shots: durations.map((duration, i) => ({ ...newShot(duration), id: `shot${i}`, action: `Action ${i}`, final_state: `Ending ${i}`, dialogue: [{ id: `line${i}`, speaker_id: "mira", text: `Exact words ${i}`, locked: true }], visible_subject_ids: ["mira"], transition: i ? "cut" : "continuous" })),
    soundscape: "quiet room", music: "piano", custom_instructions: "Only say the supplied lines.",
  };
}
const asset = (id: string, role = "reference_image", semantic_role = "face", enabled = true): Asset => ({
  id, name: id, role, semantic_role, enabled, media_type: "image", locked_order: false,
  description: "", observation: "", approved_observation: "", prompt_tag: id,
});

describe("explicit scene timing", () => {
  it("moves only the two scenes touching a boundary, preserving exact speech and all other boundaries", () => {
    const p = project([3, 2, 5, 5]);
    const speech = structuredClone(p.shots.map((s) => s.dialogue));
    setTimelineBoundary(p, "shot1", 2.5);
    expect(timelineRanges(p).map(({ start, end }) => [start, end])).toEqual([[0, 2.5], [2.5, 5], [5, 10], [10, 15]]);
    expect(p.shots.map((s) => s.dialogue)).toEqual(speech);
  });
  it.each([NaN, Infinity, -1, 0, 5, 6])("rejects invalid or overlapping boundary %s without mutating", (time) => {
    const p = project([2.5, 2.5, 10]);
    const before = structuredClone(p);
    expect(() => setTimelineBoundary(p, "shot1", time)).toThrow();
    expect(p).toEqual(before);
  });
  it("keeps explicitly stored start/end fields aligned and handles millisecond rounding", () => {
    const p = project([2.5, 2.5]);
    Object.assign(p.shots[0], { start: 0, end: 2.5 });
    Object.assign(p.shots[1], { start: 2.5, end: 5 });
    setTimelineBoundary(p, "shot1", 1.3333);
    expect(p.shots[0]).toMatchObject({ duration: 1.333, start: 0, end: 1.333 });
    expect(p.shots[1]).toMatchObject({ duration: 3.667, start: 1.333, end: 5 });
  });
  it("splits 0–5 into two scenes while leaving the rest of a 15-second clip intact", () => {
    const p = project([5, 10]);
    const rest = structuredClone(p.shots[1]);
    const inserted = splitSceneAt(p, "shot0", 2.5);
    expect(timelineRanges(p).map(({ start, end }) => [start, end])).toEqual([[0, 2.5], [2.5, 5], [5, 15]]);
    expect(p.shots[0].dialogue[0].text).toBe("Exact words 0");
    expect(p.shots[1]).toMatchObject({ id: inserted, action: "", dialogue: [], transition: "cut", final_state: "Ending 0" });
    expect(p.shots[0].final_state).toBe("");
    expect(p.shots[2]).toEqual(rest);
  });
  it("adds boundaries at 2.5 and 5 seconds without repeating the original dialogue", () => {
    const p = project();
    twoScenesInFirstFiveSeconds(p);
    expect(p.shots.map((s) => s.duration)).toEqual([2.5, 2.5, 10]);
    expect(p.shots.flatMap((s) => s.dialogue).map((line) => line.text)).toEqual(["Exact words 0"]);
    expect(p.shots.map((s) => s.final_state)).toEqual(["", "", "Ending 0"]);
  });
  it("creates exactly two scenes for a five-second clip", () => {
    const p = project([5]);
    twoScenesInFirstFiveSeconds(p);
    expect(p.shots.map((s) => s.duration)).toEqual([2.5, 2.5]);
  });
  it("changes an existing boundary's transition without duplicating scenes", () => {
    const p = project([5, 10]);
    const id = splitTimelineAt(p, 5, "continuous");
    expect(id).toBe("shot1");
    expect(p.shots).toHaveLength(2);
    expect(p.shots[1].transition).toBe("continuous");
  });
  it("refuses a seventh scene and sub-minimum splits without damaging dialogue", () => {
    const p = project([1, 1, 1, 1, 1, 10]);
    const before = structuredClone(p);
    expect(() => splitTimelineAt(p, 0.5)).toThrow("six scenes");
    expect(p).toEqual(before);
    const short = project([0.4, 4.6]);
    expect(() => splitTimelineAt(short, 0.2)).toThrow("0.25");
    expect(short.shots).toHaveLength(2);
  });
  it("makes a continuous take without deleting scene beats, speech, timing or chosen camera views", () => {
    const p = project([5, 5, 5]);
    p.shots[1].camera.framing = "close-up";
    const before = structuredClone(p.shots);
    setContinuousTake(p);
    expect(p.simple.continuous_take).toBe(true);
    p.shots.forEach((shot, index) => {
      expect(shot).toMatchObject({ ...before[index], transition: "continuous" });
      expect((shot as any).director_locks).toContain("transition");
    });
  });
});

describe("next clip planning", () => {
  it("uses the matching speed adapter for a provided first frame without changing the source or custom LoRAs", () => {
    const p = project();
    p.assets = [asset("face"), asset("ending", "context", "pose")];
    const ref = "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors";
    const frames = "minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors";
    p.comfy_render = { resolution: "0.5", loras: [
      { name: "User style.safetensors", strength: 0.3, enabled: true },
      { name: ref, strength: 0.9, enabled: false },
    ] };
    const before = structuredClone(p);
    const next = createContinuation(p, { request: "She waves.", firstFrameAssetId: "ending" });
    expect(p).toEqual(before);
    expect(next.mode).toBe("i2va");
    expect(next.comfy_render).toEqual({ resolution: "0.5", loras: [
      before.comfy_render.loras[0], { ...before.comfy_render.loras[1], name: frames },
    ] });
  });

  it("keeps the frame speed adapter when continuation falls back to text only", () => {
    const p = project();
    p.mode = "i2va";
    p.assets = [asset("oldStart", "first_frame")];
    p.comfy_render = { loras: [{ name: "minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors", strength: 1, enabled: true }] };
    const next = createContinuation(p, { request: "Continue without a new photo." });
    expect(next.mode).toBe("t2va");
    expect(next.comfy_render).toEqual(p.comfy_render);
  });

  it("creates a separate 0–15-second clip with identity, wardrobe and tags, without repeating old actions or speech", () => {
    const p = project([5, 10]);
    p.assets = [asset("face"), asset("dress", "reference_image", "wardrobe"), { ...asset("box", "reference_image", "object"), simple_owner_id: "mira" }];
    p.simple = { person_actions: { mira: "Give the box to Nora" }, next_clip_draft: { request: "Open the box" } };
    p.simple_generation = { seconds: 5 };
    p.assistant_instructions = "Say the original words";
    p.comfy_render = { continuation_source: "mmh3/old-parent.mmh3", steps: 8 };
    const before = structuredClone(p);
    const next = createContinuation(p, { request: "Nora opens the box." });
    expect(p).toEqual(before);
    expect(next.id).not.toBe(p.id);
    expect(next.title).toBe("Gift story · Clip 2");
    expect(next.shots).toHaveLength(1);
    expect(next.shots[0]).toMatchObject({ duration: 15, action: "Nora opens the box.", dialogue: [], final_state: "" });
    expect(timelineRanges(next).map(({ start, end }) => [start, end])).toEqual([[0, 15]]);
    expect(next.story.text).toBe("Nora opens the box.");
    expect(next.subjects).toEqual(p.subjects);
    expect(next.assets.map((a) => a.prompt_tag)).toEqual(["face", "dress", "box"]);
    expect(next.style).toEqual(p.style);
    expect(next.simple.continuation).toMatchObject({ previous_project_id: p.id, previous_ending: "Ending 1", previous_duration: 15, sequence_start: 15, segment_index: 2, continuity_basis: "references_and_notes", previous_object_owners: [{ asset_id: "box", person_id: "mira" }] });
    expect(next.assets[2].simple_owner_id).toBeUndefined();
    expect(next.simple.person_actions).toEqual({});
    expect(next.simple.next_clip_draft).toBeUndefined();
    expect(next.simple_generation).toBeUndefined();
    expect(next.custom_instructions).toBe("");
    expect(next.assistant_instructions).toBe("");
    expect(next.comfy_render).toEqual({ steps: 8 });
  });
  it("tracks 15–30 then 30–40 seconds in a sequence while each clip starts its local timeline at 0", () => {
    const next = createContinuation(project(), { request: "Continue" });
    const third = createContinuation(next, { request: "Wave goodbye", duration: 10 });
    expect(third.title).toBe("Gift story · Clip 3");
    expect(third.simple.continuation.sequence_start).toBe(30);
    expect(third.duration).toBe(10);
    expect(timelineRanges(third)[0]).toMatchObject({ start: 0, end: 10 });
  });
  it("does not use the previous starting/ending frames as the new starting frame", () => {
    const p = project();
    p.mode = "fl2va";
    p.assets = [asset("oldStart", "first_frame"), asset("oldEnd", "last_frame"), asset("face", "context"), asset("guide", "context"), asset("disabled", "reference_image", "face", false)];
    p.simple = { previous_image_roles: { oldStart: "reference_image", oldEnd: "reference_image", face: "reference_image" } };
    const next = createContinuation(p, { request: "Continue" });
    expect(next.mode).toBe("ref2va");
    expect(next.assets.map((a) => [a.id, a.role, a.enabled])).toEqual([
      ["oldStart", "context", true], ["oldEnd", "context", true], ["face", "reference_image", true], ["guide", "context", true], ["disabled", "reference_image", false],
    ]);
  });
  it("uses text-only continuity when there are no genuine references, with no fabricated first frame", () => {
    const p = project();
    p.mode = "i2va";
    p.assets = [asset("oldStart", "first_frame")];
    const next = createContinuation(p, { request: "Continue" });
    expect(next.mode).toBe("t2va");
    expect(next.assets[0].role).toBe("context");
    expect(next.simple.continuation.previous_frame_asset_id).toBeUndefined();
  });
  it("uses a selected actual ending photo as the sole first frame and keeps other images as inspiration", () => {
    const p = project();
    p.assets = [asset("face"), asset("dress", "reference_image", "wardrobe"), asset("last-frame", "context", "pose", false)];
    const next = createContinuation(p, { request: "Continue", firstFrameAssetId: "last-frame", ending: "Nora holds the box" });
    expect(next.mode).toBe("i2va");
    expect(next.assets.map((a) => [a.role, a.enabled])).toEqual([["context", true], ["context", true], ["first_frame", true]]);
    expect(next.simple.continuation).toMatchObject({ previous_frame_asset_id: "last-frame", continuity_basis: "provided_last_frame", previous_ending: "Nora holds the box", ending_source: "user_note" });
  });
  it("rejects a missing selected frame or an overlong clip instead of silently changing the mode", () => {
    expect(() => createContinuation(project(), { request: "Continue", firstFrameAssetId: "missing" })).toThrow("last frame");
    expect(() => createContinuation(project(), { request: "Continue", duration: 30 })).toThrow("4 to 15");
  });
  it("falls back to a plain continuation request and planned ending, preserving one-take choice", () => {
    const p = project();
    setContinuousTake(p);
    const next = createContinuation(p, { request: "  " });
    expect(next.story.text).toBe("Continue the action naturally from the previous ending.");
    expect(next.simple.continuous_take).toBe(true);
    expect(continuationEnding(next)).toBe(next.story.text);
  });
});
