import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { newShot, retime, type Project, type SceneContract } from "./model";
import SceneContinuity from "./SceneContinuity";
import {
  addSceneActor,
  addSceneObject,
  clearSceneContract,
  editSceneContract,
  pruneSceneActors,
} from "./sceneContinuityState";
import {
  duplicateScene,
  moveScene,
  setPersonVisibility,
} from "./shotDirections";
import { createContinuation, splitSceneAt } from "./timelineHelpers";
import { prepareSimpleProject, simpleInstructions } from "./simple";

function project(): Project {
  return {
    schema_version: 1,
    id: "project",
    title: "One cup",
    mode: "t2va",
    duration: 10,
    aspect_ratio: "16:9",
    profile: "director",
    authoring_mode: "full",
    story: { text: "Mira lifts the cup while Nora waits.", locked: true },
    style: {},
    assets: [
      {
        id: "cup",
        name: "Red cup",
        media_type: "image",
        role: "context",
        semantic_role: "object",
        enabled: true,
        description: "Red ceramic with one white stripe",
        locked_order: false,
        observation: "",
        approved_observation: "",
      },
    ],
    subjects: [
      { id: "mira", name: "Mira", description: "", asset_ids: [] },
      { id: "nora", name: "Nora", description: "", asset_ids: [] },
    ],
    shots: [
      {
        ...newShot(10),
        id: "shot",
        visible_subject_ids: ["mira", "nora"],
        director_locks: ["camera.framing"],
      },
    ],
    soundscape: "",
    music: "",
    custom_instructions: "",
  };
}
function generated(p = project()): Project {
  p.shots[0].scene_contract = {
    actors: [
      {
        subject_id: "mira",
        activity: "act",
        start: "Seated left",
        action: "Lifts the cup with her right hand",
        end: "Holding the cup near her chest",
      },
      {
        subject_id: "nora",
        activity: "hold",
        start: "Seated right",
        action: "Watches; hands stay on knees",
        end: "Still seated right",
      },
    ],
    objects: [
      {
        entity_id: "cup",
        name: "Red cup",
        description: "Red ceramic with one white stripe",
        count: 1,
        start: "On the table",
        end: "In Mira’s right hand",
      },
    ],
    environment: "Warm café; table fixed in place",
    background_activity: "No other foreground movement",
  };
  p.shots[0].scene_contract_source = "generated";
  return p;
}

describe("scene continuity ownership and visibility", () => {
  it("renders a collapsed readable draft without edits, requests, or ownership changes", () => {
    const p = generated(),
      before = structuredClone(p),
      update = vi.fn();
    const html = renderToStaticMarkup(
      <SceneContinuity
        project={p}
        shot={p.shots[0]}
        index={0}
        update={update}
      />,
    );
    expect(html).toContain('<details class="scene-continuity">');
    expect(html).toContain("AI draft");
    expect(html).toContain("Stay in place");
    expect(html).toContain('aria-label="Scene 1 Nora allowed small movement"');
    expect(html).toContain('aria-label="Scene 1 Object 1 count"');
    expect(html).toContain('min="1" max="100" step="1"');
    expect(html).toContain("Red ceramic with one white stripe");
    expect(html).not.toContain('open=""');
    expect(update).not.toHaveBeenCalled();
    expect(p).toEqual(before);
  });

  it("preserves partial generated contracts during normalization, retiming and reversible moves", () => {
    const p = project();
    p.shots[0].scene_contract = {
      environment: "Keep the room exactly as shown",
    };
    p.shots[0].scene_contract_source = "generated";
    const before = structuredClone(p.shots[0]);
    const prepared = prepareSimpleProject(p);
    prepared.shots = retime(prepared.shots, 12);
    prepared.shots.push({ ...newShot(3), id: "other" });
    moveScene(prepared, "shot", 1);
    moveScene(prepared, "shot", -1);
    expect(prepared.shots[0].scene_contract).toEqual(before.scene_contract);
    expect(prepared.shots[0].scene_contract_source).toBe("generated");
    expect(prepared.shots[0].director_locks).toEqual(before.director_locks);
    expect(p.shots[0]).toEqual(before);
  });

  it("takes ownership only on a real edit, preserving every other generated field", () => {
    const p = generated(),
      before = structuredClone(p.shots[0].scene_contract);
    editSceneContract(p, "shot", (d) => {
      d.environment = before?.environment;
    });
    expect(p.shots[0].scene_contract_source).toBe("generated");
    editSceneContract(p, "shot", (d) => {
      d.objects![0].count = 2;
    });
    editSceneContract(p, "shot", (d) => {
      d.objects![0].count = 2;
    });
    expect(p.shots[0].scene_contract).toEqual({
      ...before,
      objects: [{ ...before!.objects![0], count: 2 }],
    });
    expect(p.shots[0].scene_contract_source).toBeUndefined();
    expect(p.shots[0].director_locks).toEqual([
      "camera.framing",
      "scene_contract",
    ]);
  });

  it("adds only known visible actors once, and removes staging when a person goes off screen", () => {
    const p = project();
    p.shots[0].visible_subject_ids = ["mira"];
    p.shots[0].offscreen_subject_ids = ["nora"];
    addSceneActor(p, "shot", "missing");
    addSceneActor(p, "shot", "nora");
    expect(p.shots[0].scene_contract).toBeUndefined();
    addSceneActor(p, "shot", "mira");
    addSceneActor(p, "shot", "mira");
    expect(p.shots[0].scene_contract?.actors).toHaveLength(1);
    editSceneContract(p, "shot", (d) => {
      d.environment = "Café";
    });
    setPersonVisibility(p, "shot", "mira", "offscreen");
    expect(p.shots[0].scene_contract).toEqual({
      actors: [],
      environment: "Café",
    });
    expect(p.shots[0].offscreen_subject_ids).toContain("mira");
  });

  it("prunes a deleted character without losing the remaining person's directions or objects", () => {
    const p = generated(),
      before = structuredClone(p.shots[0].scene_contract);
    p.subjects = p.subjects.filter((person) => person.id !== "nora");
    pruneSceneActors(p, "shot");
    expect(p.shots[0].scene_contract).toEqual({
      ...before,
      actors: [before!.actors![0]],
    });
    expect(p.shots[0].scene_contract_source).toBeUndefined();
  });

  it("gives manual props distinct stable identities and reuses reference identity once", () => {
    const p = project();
    addSceneObject(p, "shot");
    addSceneObject(p, "shot");
    addSceneObject(p, "shot", "cup");
    addSceneObject(p, "shot", "cup");
    addSceneObject(p, "shot", "not-an-asset");
    const objects = p.shots[0].scene_contract!.objects!;
    expect(objects).toHaveLength(3);
    expect(new Set(objects.map((object) => object.entity_id)).size).toBe(3);
    expect(objects[2]).toMatchObject({
      entity_id: "cup",
      name: "Red cup",
      count: 1,
      description: "Red ceramic with one white stripe",
    });
    expect(prepareSimpleProject(p).shots[0].scene_contract?.objects).toEqual(
      objects,
    );
  });

  it("clears only continuity and its lock when the user hands direction back to AI", () => {
    const p = generated();
    p.shots[0].director_locks?.push("scene_contract", "final_state");
    clearSceneContract(p.shots[0]);
    expect(p.shots[0].scene_contract).toBeUndefined();
    expect(p.shots[0].scene_contract_source).toBeUndefined();
    expect(p.shots[0].director_locks).toEqual([
      "camera.framing",
      "final_state",
    ]);
  });

  it("asks for one action beat without compressing structured staging away", () => {
    const instructions = simpleInstructions(generated());
    expect(instructions).toContain(
      "Keep authored scene_contract directions exactly",
    );
    expect(instructions).toContain("one coherent beat");
    expect(instructions).toContain("object identity and counts");
    expect(instructions).not.toContain("one short sentence");
  });
});

describe("new scenes do not replay old staging", () => {
  it("duplicates camera setup while preserving the source contract and clearing the copy", () => {
    const p = generated(),
      before = structuredClone(p.shots[0]);
    p.shots[0].director_locks?.push("scene_contract");
    duplicateScene(p, "shot");
    expect(p.shots[0].scene_contract).toEqual(before.scene_contract);
    expect(p.shots[0].scene_contract_source).toBe("generated");
    expect(p.shots[1].scene_contract).toBeUndefined();
    expect(p.shots[1].scene_contract_source).toBeUndefined();
    expect(p.shots[1].director_locks).not.toContain("scene_contract");
    expect(p.shots[1].camera).toEqual(before.camera);
  });

  it("splits with fresh new staging and no old landing in the earlier half", () => {
    const p = generated(),
      before = structuredClone(p.shots[0].scene_contract) as SceneContract;
    splitSceneAt(p, "shot", 4);
    expect(p.shots[0].scene_contract?.actors).toEqual(
      before.actors?.map((actor) => ({ ...actor, end: "" })),
    );
    expect(p.shots[0].scene_contract?.objects).toEqual(
      before.objects?.map((object) => ({ ...object, end: "" })),
    );
    expect(p.shots[0].scene_contract?.environment).toBe(before.environment);
    expect(p.shots[0].scene_contract_source).toBeUndefined();
    expect(p.shots[1].scene_contract).toBeUndefined();
    expect(p.shots[1].scene_contract_source).toBeUndefined();
  });

  it("starts a continuation with the next requested action, never the former positions or ownership", () => {
    const source = generated(),
      before = structuredClone(source);
    source.shots[0].director_locks?.push("scene_contract");
    before.shots[0].director_locks?.push("scene_contract");
    const next = createContinuation(source, {
      request: "Mira places the cup on the shelf.",
      duration: 10,
    });
    expect(next.shots[0].action).toBe("Mira places the cup on the shelf.");
    expect(next.shots[0].scene_contract).toBeUndefined();
    expect(next.shots[0].scene_contract_source).toBeUndefined();
    expect(next.shots[0].director_locks).not.toContain("scene_contract");
    expect(source).toEqual(before);
  });
});
