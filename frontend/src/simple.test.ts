import { describe, expect, it } from "vitest";
import { newShot } from "./model";
import type { Asset, Project, Subject } from "./model";
import {
  getAssetPerson,
  getPeople,
  prepareSimpleProject,
  removeSimpleAsset,
  renamePerson,
  setAssetPerson,
  setAssetType,
  setKeyframe,
  setPersonAction,
  setSimpleMode,
  setSimpleSceneCount,
  simpleInstructions,
  simpleIssues,
} from "./simple";

const asset = (
  id: string,
  semantic_role = "other",
  role = "reference_image",
  enabled = true,
): Asset => ({
  id,
  name: `${id}.png`,
  media_type: "image",
  role,
  semantic_role,
  enabled,
  locked_order: false,
  description: "",
  observation: "Original caption",
  approved_observation: "Approved caption",
});
const person = (id: string, asset_ids: string[] = []): Subject => ({
  id,
  name: id,
  asset_ids,
  description: "",
});
const project = (assets: Asset[] = []): Project => ({
  schema_version: 1,
  id: "project",
  title: "Simple test",
  mode: "ref2va",
  duration: 15,
  aspect_ratio: "16:9",
  profile: "director",
  authoring_mode: "manual",
  story: { text: "Mira gives Nora a box.", locked: true },
  style: {},
  assets,
  subjects: [],
  shots: [newShot(15)],
  soundscape: "",
  music: "",
  custom_instructions: "Keep this exact.",
});
const conditioned = (p: Project) =>
  p.assets
    .filter((a) => a.enabled && a.role !== "context")
    .map((a) => [a.id, a.role]);

describe("simple picture modes", () => {
  it.each(["i2va", "fl2va", "l2va", "t2va"])("moves only the known speed adapter to %s and back, keeping the user's stack", (mode) => {
    const p = project([asset("start"), asset("end")]);
    const ref = "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors";
    const frames = "minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors";
    const stack = [
      { name: "User style.safetensors", strength: 0.3, enabled: true },
      { name: ref, strength: 0.85, enabled: false, note: "Keep my strength" },
      { name: "my_ref2va_speed_adapter.safetensors", strength: -0.2, enabled: false },
    ];
    p.comfy_render = { seed: 123, steps: 8, loras: structuredClone(stack) };
    setSimpleMode(p, mode);
    expect(p.comfy_render).toEqual({ seed: 123, steps: 8, loras: [stack[0], { ...stack[1], name: frames }, stack[2]] });
    setSimpleMode(p, "ref2va");
    expect(p.comfy_render).toEqual({ seed: 123, steps: 8, loras: stack });
  });

  it("does not create a LoRA stack or change unknown adapters or adapters within one family", () => {
    const p = project([asset("start"), asset("end")]);
    setSimpleMode(p, "i2va");
    expect(p.comfy_render).toBeUndefined();
    p.comfy_render = { loras: [{ name: "Custom model adapter.safetensors", strength: 0.75 }] };
    const before = structuredClone(p.comfy_render);
    setSimpleMode(p, "fl2va");
    expect(p.comfy_render).toEqual(before);
    setSimpleMode(p, "ref2va");
    expect(p.comfy_render).toEqual(before);
  });

  it("repairs a first-frame photo in a references project without removing its pose meaning", () => {
    const p = project([
      asset("pose", "pose", "first_frame"),
      asset("face", "face"),
      asset("dress", "wardrobe"),
    ]);
    expect(simpleIssues(p).some((s) => s.includes("mix reference"))).toBe(true);
    setSimpleMode(p, "ref2va");
    expect(conditioned(p)).toEqual([
      ["pose", "reference_image"],
      ["face", "reference_image"],
      ["dress", "reference_image"],
    ]);
    expect(p.assets[0].semantic_role).toBe("pose");
    expect(simpleIssues(p)).toEqual([]);
  });

  it("uses exactly one starting frame and keeps other photos available to the assistant", () => {
    const p = project([
      asset("face", "face"),
      asset("chosen", "pose", "first_frame"),
      asset("dress", "wardrobe"),
      asset("sketch", "pose", "context"),
      asset("off", "face", "reference_image", false),
    ]);
    setSimpleMode(p, "i2va");
    expect(conditioned(p)).toEqual([["chosen", "first_frame"]]);
    expect(
      p.assets
        .filter((a) => a.enabled && a.role === "context")
        .map((a) => a.id),
    ).toEqual(["face", "dress", "sketch"]);
    expect(p.assets.some((a) => a.enabled && a.role === "last_frame")).toBe(
      false,
    );
    setSimpleMode(p, "ref2va");
    expect(conditioned(p)).toEqual([
      ["face", "reference_image"],
      ["chosen", "reference_image"],
      ["dress", "reference_image"],
    ]);
    expect(p.assets.find((a) => a.id === "sketch")?.role).toBe("context");
    expect(p.assets.find((a) => a.id === "off")?.enabled).toBe(false);
  });

  it.each(["fl2va", "i2va", "l2va", "t2va"])(
    "preserves disabled assets and intentional guides during %s roundtrips",
    (mode) => {
      const p = project([
        asset("a"),
        asset("b"),
        asset("c"),
        asset("guide", "pose", "context"),
        asset("off", "other", "reference_image", false),
      ]);
      const originals = structuredClone(p.assets);
      setSimpleMode(p, mode);
      setSimpleMode(p, mode);
      expect(p.assets).toHaveLength(5);
      expect(p.assets[3]).toEqual(originals[3]);
      expect(p.assets[4]).toEqual(originals[4]);
      setSimpleMode(p, "ref2va");
      expect(p.assets).toEqual(originals);
    },
  );

  it("preserves audio/video as planning context in first-frame mode and restores their media roles", () => {
    const p = project([
      asset("photo"),
      { ...asset("voice"), media_type: "audio", role: "reference_audio" },
      { ...asset("clip"), media_type: "video", role: "reference_video" },
    ]);
    setSimpleMode(p, "i2va");
    expect(conditioned(p)).toEqual([["photo", "first_frame"]]);
    expect(
      p.assets.slice(1).every((a) => a.enabled && a.role === "context"),
    ).toBe(true);
    setSimpleMode(p, "ref2va");
    expect(conditioned(p)).toEqual([
      ["photo", "reference_image"],
      ["voice", "reference_audio"],
      ["clip", "reference_video"],
    ]);
  });

  it("allows selecting a replacement starting frame and selecting a guide deliberately", () => {
    const p = project([
      asset("old", "other", "first_frame"),
      asset("new"),
      asset("guide", "pose", "context"),
    ]);
    p.mode = "i2va";
    setKeyframe(p, "new", "first_frame");
    expect(conditioned(p)).toEqual([["new", "first_frame"]]);
    setKeyframe(p, "guide", "first_frame");
    expect(conditioned(p)).toEqual([["guide", "first_frame"]]);
    expect(p.assets.find((a) => a.id === "new")?.enabled).toBe(true);
  });

  it("swaps endpoints without duplicate assignments in first + last mode", () => {
    const p = project([
      asset("a", "other", "first_frame"),
      asset("b", "other", "last_frame"),
      asset("c"),
    ]);
    p.mode = "fl2va";
    setKeyframe(p, "b", "first_frame");
    expect(conditioned(p)).toEqual([
      ["a", "last_frame"],
      ["b", "first_frame"],
    ]);
    expect(simpleIssues(p)).toEqual([]);
  });

  it("reports missing endpoint photos instead of inventing one or promoting a sketch", () => {
    const p = project([asset("guide", "pose", "context")]);
    setSimpleMode(p, "i2va");
    expect(conditioned(p)).toEqual([]);
    expect(simpleIssues(p)).toContain(
      "Choose exactly one starting picture. No ending picture is needed.",
    );
  });
});

describe("people, clothes and object ownership", () => {
  it("shows two people and a narrator, not autogenerated dress or room entries", () => {
    const p = project([
      asset("mira", "face"),
      asset("nora", "character"),
      asset("dress", "wardrobe"),
      asset("room", "background"),
    ]);
    p.subjects = [
      person("Mira", ["mira"]),
      person("Nora", ["nora"]),
      person("dress.png", ["dress"]),
      person("room.png", ["room"]),
      person("Narrator"),
    ];
    p.shots[0].dialogue = [
      {
        id: "line",
        speaker_id: "Narrator",
        text: "Exact line.",
        language: "English",
        locked: true,
      },
    ];
    expect(getPeople(p).map((s) => s.name)).toEqual([
      "Mira",
      "Nora",
      "Narrator",
    ]);
    const next = prepareSimpleProject(p);
    expect(next.subjects.map((s) => s.name)).toEqual([
      "Mira",
      "Nora",
      "Narrator",
    ]);
    expect(next.shots[0].dialogue).toEqual(p.shots[0].dialogue);
  });

  it("assigns each dress exclusively and keeps object ownership separate from identity", () => {
    const p = project([
      asset("face1", "face"),
      asset("face2", "face"),
      asset("dress1", "wardrobe"),
      asset("dress2", "wardrobe"),
      asset("box", "object"),
    ]);
    p.subjects = [
      person("Mira", ["face1"]),
      person("Nora", ["face2"]),
      person("dress1.png", ["dress1"]),
    ];
    setAssetPerson(p, "dress1", "Mira");
    setAssetPerson(p, "dress2", "Nora");
    setAssetPerson(p, "box", "Mira");
    expect(getAssetPerson(p, "dress1")?.name).toBe("Mira");
    expect(getAssetPerson(p, "dress2")?.name).toBe("Nora");
    expect(getAssetPerson(p, "box")?.name).toBe("Mira");
    expect(p.subjects.find((s) => s.id === "Mira")?.asset_ids).toEqual([
      "face1",
      "dress1",
    ]);
    expect(p.assets.find((a) => a.id === "box")?.simple_owner_id).toBe("Mira");
    expect(p.subjects.some((s) => s.name === "dress1.png")).toBe(false);
    setAssetPerson(p, "dress1", "Nora");
    expect(p.subjects.find((s) => s.id === "Mira")?.asset_ids).toEqual([
      "face1",
    ]);
    expect(
      p.subjects.filter((s) => s.asset_ids.includes("dress1")).map((s) => s.id),
    ).toEqual(["Nora"]);
    setAssetPerson(p, "box", "scene");
    expect(getAssetPerson(p, "box")).toBeUndefined();
  });

  it("creates a person from a face and invalidates only the approved role-specific caption", () => {
    const p = project([asset("character-mira")]);
    p.subjects = [person("character-mira.png", ["character-mira"])];
    setAssetType(p, "character-mira", "face");
    expect(getPeople(p)).toHaveLength(1);
    expect(getPeople(p)[0].name).toBe("Mira");
    expect(p.assets[0].approved_observation).toBe("");
    expect(p.assets[0].observation).toBe("Original caption");
    p.assets[0].approved_observation = "Reviewed face";
    setAssetType(p, "character-mira", "face");
    expect(p.assets[0].approved_observation).toBe("Reviewed face");
  });

  it("preserves explicitly named nonperson data while removing only generated placeholders", () => {
    const p = project([asset("dress", "other"), asset("room", "background")]);
    p.subjects = [
      {
        ...person("dress.png", ["dress"]),
        description: "Keep my authored dress details.",
      },
      person("room.png", ["room"]),
    ];
    setAssetType(p, "dress", "wardrobe");
    const next = prepareSimpleProject(p);
    expect(next.subjects).toHaveLength(1);
    expect(next.subjects[0].description).toBe(
      "Keep my authored dress details.",
    );
    expect(next.subjects[0].asset_ids).toEqual(["dress"]);
  });

  it("retains a speaker identity and exact dialogue when their only photo is removed", () => {
    const p = project([asset("face", "face")]);
    p.subjects = [person("Mira", ["face"])];
    p.shots[0].dialogue = [
      {
        id: "line",
        speaker_id: "Mira",
        text: "Shiko çfarë solla.",
        language: "Albanian",
        locked: true,
        delivery: "Warm",
      },
    ];
    const dialogue = structuredClone(p.shots[0].dialogue);
    removeSimpleAsset(p, "face");
    expect(getPeople(p).map((s) => s.id)).toEqual(["Mira"]);
    expect(p.subjects[0].asset_ids).toEqual([]);
    expect(p.shots[0].dialogue).toEqual(dialogue);
    renamePerson(p, "Mira", "Mira Rivers");
    expect(p.shots[0].dialogue).toEqual(dialogue);
  });

  it("prunes old nonperson rosters without removing dialogue-speaker subjects", () => {
    const p = project([
      asset("dress", "wardrobe"),
      asset("room", "background"),
    ]);
    p.subjects = [person("dress.png", ["dress"]), person("room.png", ["room"])];
    p.shots[0].visible_subject_ids = ["dress.png", "room.png"];
    p.shots[0].dialogue = [
      {
        id: "line",
        speaker_id: "dress.png",
        text: "Keep me.",
        language: "English",
      },
    ];
    const next = prepareSimpleProject(p);
    expect(next.subjects.map((s) => s.id)).toEqual(["dress.png"]);
    expect(next.shots[0].visible_subject_ids).toEqual(["dress.png"]);
  });
});

describe("simple story planning preserves authored facts", () => {
  it("adds scenes with both people and evenly distributes the total duration", () => {
    const p = project([asset("mira", "face"), asset("nora", "face")]);
    p.subjects = [person("Mira", ["mira"]), person("Nora", ["nora"])];
    const first = p.shots[0].id;
    setSimpleSceneCount(p, 3);
    expect(p.shots.map((s) => s.duration)).toEqual([5, 5, 5]);
    expect(p.shots[0].id).toBe(first);
    expect(p.shots[1].visible_subject_ids).toEqual(["Mira", "Nora"]);
    expect(p.shots[2].visible_subject_ids).toEqual(["Mira", "Nora"]);
  });

  it("merges every removed scene’s exact dialogue into the last kept scene, preserving event order and speaker presence", () => {
    const p = project();
    p.subjects = [person("Mira"), person("Nora")];
    p.shots = [newShot(5), newShot(5), newShot(5)];
    p.shots[0].action = "Keep original first action.";
    p.shots[1].action = "Keep original second action.";
    p.shots.forEach((s, i) => {
      s.dialogue = [
        {
          id: `line${i}`,
          speaker_id: i === 2 ? "Nora" : "Mira",
          text: [
            "Shiko çfarë solla.",
            "Exactly  two spaces!",
            "Sa bukur! Faleminderit.",
          ][i],
          language: "Albanian",
          delivery: "Soft",
          locked: true,
        },
      ];
    });
    p.shots[2].offscreen_subject_ids = ["Nora"];
    const allLines = structuredClone(p.shots.flatMap((s) => s.dialogue));
    const ids = p.shots.slice(0, 2).map((s) => s.id);
    setSimpleSceneCount(p, 2);
    expect(p.shots.map((s) => s.id)).toEqual(ids);
    expect(p.shots.flatMap((s) => s.dialogue)).toEqual(allLines);
    expect(p.shots[1].dialogue).toHaveLength(2);
    expect(p.shots[1].offscreen_subject_ids).toContain("Nora");
    expect(p.shots[0].action).toBe("Keep original first action.");
    expect(p.shots[1].action).toBe("Keep original second action.");
    expect(p.shots.map((s) => s.duration)).toEqual([7.5, 7.5]);
  });

  it("prepares a clone, preserves expert profile and story, and fills only a blank single scene", () => {
    const p = project([asset("face", "face")]);
    p.subjects = [person("Mira", ["face"])];
    const original = structuredClone(p),
      next = prepareSimpleProject(p);
    expect(p).toEqual(original);
    expect(next.profile).toBe("director");
    expect(next.custom_instructions).toBe(p.custom_instructions);
    expect(next.story).toEqual(p.story);
    expect(next.authoring_mode).toBe("full");
    expect(next.shots[0].action).toBe(p.story.text);
    expect(next.shots[0].visible_subject_ids).toEqual(["Mira"]);
    p.shots[0].action = "An explicitly authored action.";
    p.shots[0].offscreen_subject_ids = ["Mira"];
    const authored = prepareSimpleProject(p);
    expect(authored.shots[0].action).toBe(p.shots[0].action);
    expect(authored.shots[0].visible_subject_ids).toEqual([]);
    expect(authored.shots[0].offscreen_subject_ids).toEqual(["Mira"]);
  });

  it("explains missing clothing owners and reference limits in plain language", () => {
    const p = project([
      asset("a", "face"),
      asset("b", "face"),
      asset("dress", "wardrobe"),
    ]);
    p.subjects = [person("Mira", ["a"]), person("Nora", ["b"])];
    expect(simpleIssues(p)).toContain(
      "Choose who wears “dress.png”, or turn this picture off.",
    );
    setAssetPerson(p, "dress", "Nora");
    expect(simpleIssues(p)).toEqual([]);
    p.assets.push(...Array.from({ length: 7 }, (_, i) => asset(`extra${i}`)));
    expect(simpleIssues(p).some((s) => s.includes("nine reference"))).toBe(
      true,
    );
    p.story.text = "  ";
    expect(simpleIssues(p)[0]).toBe(
      "Write a sentence about what you want to happen.",
    );
  });

  it("instructs the model about named actions, separate clothes, starting object owner and exact dialogue", () => {
    const p = project([
      asset("face", "face"),
      asset("dress", "wardrobe"),
      asset("box", "object"),
    ]);
    p.subjects = [person("Mira", ["face"])];
    setAssetPerson(p, "dress", "Mira");
    setAssetPerson(p, "box", "Mira");
    setPersonAction(p, "Mira", "Pass the closed box to Nora.");
    const instructions = simpleInstructions(p);
    expect(instructions).toContain(
      'Action requested for "Mira": "Pass the closed box to Nora."',
    );
    expect(instructions).toContain(
      'Clothing "dress.png" is worn by "Mira" only.',
    );
    expect(instructions).toContain('Object "box.png" starts with "Mira".');
    expect(instructions).toContain("exact text, speaker, language and scene");
    setSimpleMode(p, "i2va");
    expect(simpleInstructions(p)).toContain(
      "one actual starting frame and no ending frame",
    );
  });
});
