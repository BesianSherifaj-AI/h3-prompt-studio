import { describe, expect, it } from "vitest";
import { newShot } from "./model";
import type { Project } from "./model";
import {
  applyApproach,
  approachPreview,
  CAMERA_APPROACHES,
  projectSummary,
  snapshotProject,
} from "./templates";

function project(): Project {
  const shot = newShot(15);
  shot.id = "existing-shot";
  shot.action = "Mira gives Nora the blue box.";
  shot.visible_subject_ids = ["mira", "nora"];
  shot.dialogue = [
    {
      id: "line-1",
      speaker_id: "mira",
      text: "This is yours.",
      emotion: "warm",
    },
    {
      id: "line-2",
      speaker_id: "nora",
      text: "Thank you!",
      emotion: "surprised",
    },
  ];
  return {
    schema_version: 1,
    id: "source",
    title: "Two people",
    mode: "ref2va",
    duration: 15,
    aspect_ratio: "16:9",
    profile: "concise",
    authoring_mode: "full",
    story: { text: "Mira gives Nora a blue box in the atelier.", locked: true },
    style: { vibe: "warm" },
    assets: [
      {
        id: "face-m",
        name: "Mira",
        media_type: "image",
        role: "reference_image",
        semantic_role: "face",
        enabled: true,
        locked_order: true,
        description: "Mira face",
        observation: "Mira",
        approved_observation: "Mira face",
      },
      {
        id: "dress",
        name: "Blue dress",
        media_type: "image",
        role: "reference_image",
        semantic_role: "wardrobe",
        enabled: true,
        locked_order: false,
        description: "Blue dress",
        observation: "Blue dress",
        approved_observation: "Blue dress",
      },
      {
        id: "box",
        name: "Box",
        media_type: "image",
        role: "context",
        semantic_role: "object",
        enabled: true,
        locked_order: false,
        description: "Box",
        observation: "Box",
        approved_observation: "Box",
        simple_owner_id: "mira",
      },
    ],
    subjects: [
      {
        id: "mira",
        name: "Mira",
        asset_ids: ["face-m", "dress"],
        description: "",
      },
      { id: "nora", name: "Nora", asset_ids: [], description: "" },
    ],
    shots: [shot],
    soundscape: "Quiet room.",
    music: "",
    custom_instructions: "Keep the line exact.",
    simple: {
      approach: "faithful",
      person_actions: { mira: "Hand over the box." },
    },
  };
}

describe("camera starters", () => {
  for (const approach of CAMERA_APPROACHES) {
    it(`${approach.name} preserves every photo binding, action and exact line`, () => {
      const p = project();
      const before = snapshotProject(p);
      applyApproach(p, approach.id);
      expect(p.assets).toEqual(before.assets);
      expect(p.subjects).toEqual(before.subjects);
      expect(p.story).toEqual(before.story);
      expect(p.shots[0].id).toBe("existing-shot");
      expect(p.shots[0].action).toBe(before.shots[0].action);
      expect(p.shots[0].dialogue).toEqual(before.shots[0].dialogue);
      expect(p.shots[0].visible_subject_ids).toEqual(
        before.shots[0].visible_subject_ids,
      );
      expect(
        p.shots
          .slice(1)
          .every(
            (s) =>
              s.dialogue.length === 0 && s.visible_subject_ids.length === 0,
          ),
      ).toBe(true);
      expect(p.simple.person_actions).toEqual(before.simple.person_actions);
      expect(p.simple.approach).toBe("faithful");
      expect(p.simple.directed).toBe(true);
      expect((p.shots[0] as any).director_locks).toEqual(
        expect.arrayContaining([
          "camera.framing",
          "camera.movement",
          "transition",
        ]),
      );
      expect(p.shots.reduce((sum, s) => sum + s.duration, 0)).toBe(15);
    });
  }

  it("adds wide, medium and close views, with a cut before each later view", () => {
    const p = project();
    applyApproach(p, "wide-medium-close");
    expect(p.shots.map((s) => s.camera.framing)).toEqual([
      "wide",
      "medium",
      "close-up",
    ]);
    expect(p.shots.map((s) => s.transition)).toEqual([
      "continuous",
      "cut",
      "cut",
    ]);
    expect(p.shots.map((s) => s.duration)).toEqual([5, 5, 5]);
  });

  it("never collapses authored scenes to make a continuous take", () => {
    const p = project();
    p.shots.push({
      ...newShot(5),
      action: "Nora opens the box.",
      dialogue: [{ id: "third", speaker_id: "nora", text: "I love it." }],
      transition: "cut",
    });
    p.shots[0].duration = 10;
    const before = snapshotProject(p);
    applyApproach(p, "continuous");
    expect(p.shots.map((s) => s.id)).toEqual(before.shots.map((s) => s.id));
    expect(p.shots.map((s) => s.duration)).toEqual([10, 5]);
    expect(p.shots.map((s) => s.dialogue)).toEqual(
      before.shots.map((s) => s.dialogue),
    );
    expect(p.shots.every((s) => s.transition === "continuous")).toBe(true);
  });

  it("keeps additional shots, custom focus and uneven timing when no cards are needed", () => {
    const p = project();
    p.shots[0].duration = 4;
    p.shots[0].camera.focus = "Mira's hands";
    p.shots.push(newShot(3), newShot(2), newShot(6));
    const ids = p.shots.map((s) => s.id);
    applyApproach(p, "product");
    expect(p.shots.map((s) => s.id)).toEqual(ids);
    expect(p.shots.map((s) => s.duration)).toEqual([4, 3, 2, 6]);
    expect(p.shots[0].camera.focus).toBe("Mira's hands");
    expect(p.shots[2].camera.movement).toBe("push-in");
  });

  it("rejects unknown starters without changing the draft", () => {
    const p = project();
    const before = snapshotProject(p);
    expect(() => applyApproach(p, "missing" as any)).toThrow(
      "Choose a camera starter",
    );
    expect(p).toEqual(before);
  });

  it("previews added cards and mode-specific continuity without changing the mode", () => {
    const p = project();
    p.mode = "fl2va";
    expect(approachPreview(p, "wide-medium-close")).toContain(
      "Adds 2 empty shot cards",
    );
    expect(approachPreview(p, "wide-medium-close")).toContain(
      "first-to-last-frame",
    );
    applyApproach(p, "wide-medium-close");
    expect(p.mode).toBe("fl2va");
  });
});

describe("saved recipes", () => {
  it("snapshots every editable field and identifier for a complete restore", () => {
    const p = project();
    p.simple_generation = { seconds: 12, generated_at: "today" };
    p.assets[0].file_hash = "reference-sha";
    const saved = snapshotProject(p);
    expect(saved).toEqual(p);
    p.assets[0].name = "Changed later";
    p.subjects[0].asset_ids = [];
    p.shots[0].dialogue[0].text = "Different line";
    const restored = snapshotProject(saved);
    expect(restored.assets[0].id).toBe("face-m");
    expect(restored.assets[0].name).toBe("Mira");
    expect(restored.subjects[0].asset_ids).toEqual(["face-m", "dress"]);
    expect(restored.assets[2].simple_owner_id).toBe("mira");
    expect(restored.shots[0].dialogue[0].text).toBe("This is yours.");
    restored.assets[0].approved_observation = "Edited";
    expect(saved.assets[0].approved_observation).toBe("Mira face");
  });

  it("uses small server summaries without requiring full project snapshots", () => {
    expect(
      projectSummary({
        id: "v",
        name: "Try 1",
        created_at: "",
        mode: "i2va",
        duration: 5,
        shot_count: 1,
        asset_count: 4,
      }),
    ).toBe("First frame only · 5s · 1 shot · 4 references");
    expect(
      projectSummary({
        id: "v",
        name: "Try 1",
        created_at: "",
        project: project(),
      }),
    ).toContain("15s · 1 shot · 3 references");
  });
});
