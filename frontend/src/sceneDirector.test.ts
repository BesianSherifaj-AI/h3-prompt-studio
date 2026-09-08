import { describe, expect, it } from "vitest";
import { newShot } from "./model";
import type { Project } from "./model";
import {
  appendScene,
  appendSceneAction,
  applyCameraSetup,
  duplicateScene,
  moveScene,
  setDirectorValue,
  setPersonVisibility,
  setSceneDuration,
} from "./shotDirections";
import type { DirectedShot } from "./shotDirections";

function project(durations = [5, 5, 5]): Project {
  return {
    schema_version: 1,
    id: "p",
    title: "Scene direction",
    mode: "ref2va",
    duration: durations.reduce((a, b) => a + b, 0),
    aspect_ratio: "16:9",
    profile: "concise",
    authoring_mode: "full",
    story: { text: "Mira shows Nora a gift.", locked: true },
    style: {},
    assets: [],
    subjects: [
      { id: "mira", name: "Mira", asset_ids: [], description: "" },
      { id: "nora", name: "Nora", asset_ids: [], description: "" },
    ],
    shots: durations.map((duration, i) => ({
      ...newShot(duration),
      id: `scene-${i}`,
      action: `Action ${i}`,
      visible_subject_ids: ["mira"],
      dialogue: [
        {
          id: `line-${i}`,
          speaker_id: "mira",
          text: `Faleminderit! Exact line ${i}.`,
          locked: true,
        },
      ],
    })),
    soundscape: "",
    music: "",
    custom_instructions: "",
  };
}
const totalMs = (p: Project) =>
  p.shots.reduce((sum, shot) => sum + Math.round(shot.duration * 1000), 0);

describe("per-scene direction", () => {
  it("locks only a chosen field and lets AI choose it again without clearing other choices", () => {
    const p = project();
    setDirectorValue(p, "scene-1", "camera.framing", "wide");
    setDirectorValue(p, "scene-1", "camera.movement", "tracking");
    setDirectorValue(p, "scene-1", "camera.framing", "close-up");
    const shot = p.shots[1] as DirectedShot;
    expect(shot.director_locks).toEqual(["camera.framing", "camera.movement"]);
    expect(shot.camera.framing).toBe("close-up");
    setDirectorValue(p, shot.id, "camera.framing", "");
    expect(shot.camera.framing).toBe("");
    expect(shot.camera.movement).toBe("tracking");
    expect(shot.director_locks).toEqual(["camera.movement"]);
    expect(p.shots[0].camera.framing).toBe("medium");
  });

  it("keeps a cut and a close-up independent of the earlier wide scene", () => {
    const p = project();
    setDirectorValue(p, "scene-0", "camera.framing", "wide");
    setDirectorValue(p, "scene-1", "transition", "cut");
    setDirectorValue(p, "scene-1", "camera.framing", "close-up");
    expect(p.shots.map((s) => s.camera.framing)).toEqual([
      "wide",
      "close-up",
      "medium",
    ]);
    expect(p.shots[1].transition).toBe("cut");
    expect((p.shots[1] as DirectedShot).director_locks).toContain("transition");
  });

  it("camera setup does not change exact dialogue, action, timing, or other camera choices", () => {
    const p = project();
    const before = structuredClone(p.shots[0]);
    applyCameraSetup(p, "scene-0", "detail");
    expect(p.shots[0].camera.framing).toBe("extreme close-up");
    expect(p.shots[0].dialogue).toEqual(before.dialogue);
    expect(p.shots[0].action).toBe(before.action);
    expect(p.shots[0].duration).toBe(before.duration);
    expect(p.shots[0].camera.height).toBe(before.camera.height);
  });

  it("moving a named person off screen updates both rosters without losing their line", () => {
    const p = project();
    const line = structuredClone(p.shots[0].dialogue);
    setPersonVisibility(p, "scene-0", "mira", "offscreen");
    expect(p.shots[0].visible_subject_ids).toEqual([]);
    expect(p.shots[0].offscreen_subject_ids).toEqual(["mira"]);
    expect(p.shots[0].dialogue).toEqual(line);
    expect((p.shots[0] as DirectedShot).director_locks).toEqual([
      "visible_subject_ids",
      "offscreen_subject_ids",
    ]);
    setPersonVisibility(p, "scene-0", "mira", "visible");
    expect(p.shots[0].visible_subject_ids).toEqual(["mira"]);
    expect(p.shots[0].offscreen_subject_ids).toEqual([]);
  });

  it("represents an intentional empty visible roster and can hand it back to AI", () => {
    const p = project();
    setDirectorValue(p, "scene-0", "visible_subject_ids", []);
    expect((p.shots[0] as DirectedShot).director_locks).toContain(
      "visible_subject_ids",
    );
    setDirectorValue(p, "scene-0", "visible_subject_ids", [], false);
    expect((p.shots[0] as DirectedShot).director_locks).not.toContain(
      "visible_subject_ids",
    );
  });
});

describe("scene timing and arrangement", () => {
  it("retimes other scenes proportionally while preserving exact source contents", () => {
    const p = project([3, 4, 8]);
    const contents = p.shots.map(({ duration: _, ...shot }) =>
      structuredClone(shot),
    );
    setSceneDuration(p, "scene-0", 6);
    expect(p.shots.map((s) => s.duration)).toEqual([6, 3, 6]);
    expect(p.shots.map(({ duration: _, ...shot }) => shot)).toEqual(contents);
    expect(totalMs(p)).toBe(15000);
    expect(p.simple.directed).toBe(true);
  });

  it("survives many fractional timing edits with no gaps, negative lengths, or speech changes", () => {
    const p = project([1.667, 1.667, 1.666]);
    const lines = p.shots.map((s) => structuredClone(s.dialogue));
    for (let i = 0; i < 50; i++) {
      setSceneDuration(p, `scene-${i % 3}`, (i % 9) * 0.347);
      expect(totalMs(p)).toBe(5000);
      expect(p.shots.every((s) => s.duration > 0)).toBe(true);
    }
    expect(p.shots.map((s) => s.dialogue)).toEqual(lines);
  });

  it("clamps an overlong selection to leave time for the other scenes", () => {
    const p = project([1, 1, 1]);
    setSceneDuration(p, "scene-1", 999);
    expect(p.shots[1].duration).toBe(2.5);
    expect(p.shots[0].duration).toBe(0.25);
    expect(p.shots[2].duration).toBe(0.25);
    expect(totalMs(p)).toBe(3000);
    const snapshot = structuredClone(p);
    setSceneDuration(p, "scene-1", Number.NaN);
    expect(p).toEqual(snapshot);
  });

  it("a single scene keeps the whole video length", () => {
    const p = project([5]);
    setSceneDuration(p, "scene-0", 2);
    expect(p.shots[0].duration).toBe(5);
  });

  it("moves complete cards with their dialogue and restores them exactly when moved back", () => {
    const p = project();
    setDirectorValue(p, "scene-1", "transition", "cut");
    const scenes = structuredClone(p.shots);
    moveScene(p, "scene-1", -1);
    expect(p.shots.map((s) => s.id)).toEqual(["scene-1", "scene-0", "scene-2"]);
    expect(p.shots[0].dialogue).toEqual(scenes[1].dialogue);
    expect(totalMs(p)).toBe(15000);
    moveScene(p, "scene-1", 1);
    expect(p.shots).toEqual(scenes);
    moveScene(p, "scene-0", -1);
    expect(p.shots).toEqual(scenes);
  });

  it("duplicates setup with a new id and split duration but keeps exact speech only once", () => {
    const p = project([5.001, 4.999]);
    setDirectorValue(p, "scene-0", "camera.framing", "wide");
    const lines = structuredClone(p.shots[0].dialogue);
    duplicateScene(p, "scene-0");
    expect(p.shots).toHaveLength(3);
    expect(p.shots[0].duration).toBe(2.501);
    expect(p.shots[1].duration).toBe(2.5);
    expect(p.shots[1].id).not.toBe(p.shots[0].id);
    expect(p.shots[1].camera.framing).toBe("wide");
    expect(p.shots[1].transition).toBe("cut");
    expect(p.shots[1].dialogue).toEqual([]);
    expect(p.shots[0].dialogue).toEqual(lines);
    expect(totalMs(p)).toBe(10000);
    p.shots[1].camera.framing = "close-up";
    expect(p.shots[0].camera.framing).toBe("wide");
  });

  it("adds a blank scene without changing existing ids, actions, or exact dialogue", () => {
    const p = project([5]);
    const original = structuredClone(p.shots[0]);
    appendScene(p);
    expect(p.shots).toHaveLength(2);
    expect(p.shots[0].id).toBe(original.id);
    expect(p.shots[0].action).toBe(original.action);
    expect(p.shots[0].dialogue).toEqual(original.dialogue);
    expect(p.shots[1].action).toBe("");
    expect(p.shots[1].dialogue).toEqual([]);
    expect(p.shots[1].camera.framing).toBe("");
    expect(totalMs(p)).toBe(5000);
  });

  it("limits the simple editor to six scenes without deleting or changing existing ones", () => {
    const p = project([1, 1, 1, 1, 1, 1]);
    const before = structuredClone(p);
    appendScene(p);
    duplicateScene(p, "scene-0");
    expect(p).toEqual(before);
  });
});

describe("simple action builder", () => {
  it("adds named actions without UUIDs and leaves existing direction and exact speech intact", () => {
    const p = project();
    p.assets.push({
      id: "secret-object-uuid",
      name: "red gift box",
      media_type: "image",
      role: "reference_image",
      semantic_role: "object",
      enabled: true,
      locked_order: false,
      description: "",
      observation: "",
      approved_observation: "",
    });
    const line = structuredClone(p.shots[0].dialogue);
    appendSceneAction(p, "scene-0", "nora", "picks up", "secret-object-uuid");
    expect(p.shots[0].action).toBe("Action 0 Nora picks up red gift box.");
    expect(p.shots[0].action).not.toContain("uuid");
    expect(p.shots[0].visible_subject_ids).toContain("nora");
    expect(p.shots[0].dialogue).toEqual(line);
    appendSceneAction(p, "scene-0", "mira", "smiles and waves.");
    expect(p.shots[0].action).toContain("Mira smiles and waves.");
    expect(p.shots[0].action).not.toContain("..");
  });
});
