import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import MotionLab, { motionPreviewProject, motionSeeds } from "./MotionLab";
import { blankGameProject } from "./gameConfiguration";

describe("Motion Lab paired preview", () => {
  it("preserves source references, dialogue and LoRAs in an independent three-second snapshot", () => {
    const project = blankGameProject();
    project.duration = 10;
    project.shots[0].duration = 10;
    project.shots[0].dialogue = [{ speaker_id: "player", text: "Where is the key?" }];
    project.assets = [{ id: "face", name: "Mira", media_type: "image", semantic_role: "face", role: "first_frame", enabled: true, locked_order: false, description: "Explorer", observation: "", approved_observation: "" }];
    project.comfy_render = { seed: 42, resolution: "1.0", steps: 16, loras: [{ name: "detail", strength: .6 }, { name: "motion", strength: .5 }], continuation_source: "saved-clip", continuation_overlap_frames: 39 };
    const before = structuredClone(project), preview = motionPreviewProject(project);
    expect(project).toEqual(before);
    expect(preview.duration).toBe(3);
    expect(preview.shots.reduce((total, shot) => total + shot.duration, 0)).toBe(3);
    expect(preview.comfy_render).toMatchObject({ experimental_preview: true, resolution: "0.2", steps: 16, seed: 42, loras: before.comfy_render.loras });
    expect(preview.comfy_render.continuation_source).toBeUndefined();
    expect(preview.comfy_render.continuation_overlap_frames).toBeUndefined();
    expect(preview.assets).toEqual(before.assets);
    expect(preview.shots[0].dialogue).toEqual(before.shots[0].dialogue);
    preview.assets[0].description = "Different take";
    expect(project.assets[0].description).toBe("Explorer");
  });
  it("locks pairs to bounded distinct seeds without rounding unsafe numbers", () => {
    expect(motionSeeds("0, 42 9007199254740991")).toEqual([0, 42, 9007199254740991]);
    for (const text of ["", "1,1", "-2", "2.5", "1e3", "9007199254740992", "1,2,3,4"]) expect(() => motionSeeds(text)).toThrow();
  });
  it("opens with an explicit saved-test action and distinguishes its branch behavior", () => {
    const html = renderToStaticMarkup(<MotionLab project={blankGameProject()} onClose={() => {}} />);
    expect(html).toContain("Close Motion Lab");
    expect(html).toContain("Create comparison");
    expect(html).toContain("It does not render");
    expect(html).toContain("your story stays at its current ending");
    expect(html).toContain("Experimental pixel preview");
    expect(html).not.toContain("autoplay");
  });
});
