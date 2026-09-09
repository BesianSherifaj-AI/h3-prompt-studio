import { describe, it, expect } from "vitest";
import { addGameAssets, assignGameAsset, blankGameProject, characterFromSubject, configurationFromStory, emptyWorld, ensurePlayer, prepareGameConfiguration, removeGameCharacter, replaceGameAsset, updateCharacter } from "./gameConfiguration";
import { DEFAULT_STORY_SETTINGS, type StoryConfiguration, type Story } from "./storyTypes";
import type { Asset } from "./model";
const config = (): StoryConfiguration => ({ project: blankGameProject(), world: emptyWorld(), guides: [], settings: { ...DEFAULT_STORY_SETTINGS }, premise: "Explore the abandoned lighthouse", player_name: "Mira", player_character_id: "" });
const asset = (id: string): Asset => ({ id, name: id, media_type: "image", semantic_role: "face", role: "reference_image", enabled: true, locked_order: false, description: "", observation: "", approved_observation: "" });
describe("independent, persistent Game configuration", () => {
  it("starts blank with editable pixel style and no inherited world, people, or motion", () => {
    const p = blankGameProject();
    expect(p.assets).toEqual([]); expect(p.subjects).toEqual([]); expect(p.story.text).toBe("");
    expect(p.style.notes).toContain("2D pixel art"); expect(p.shots[0].camera.framing).toBe(""); expect(p.comfy_render.continuation_source).toBeUndefined();
    expect(p.mode).toBe("t2va"); expect(DEFAULT_STORY_SETTINGS.resolution).toBe("0.2");
  });
  it("creates one stable player and preserves it on subsequent saves", () => {
    const first = ensurePlayer(config()), second = ensurePlayer(first);
    expect(first.player_character_id).toBeTruthy(); expect(second.player_character_id).toBe(first.player_character_id);
    expect(second.world.characters).toHaveLength(1); expect(second.world.characters[0].control).toBe("player");
    expect(second.project.subjects[0].id).toBe(first.player_character_id);
  });
  it("imports existing subjects as independent NPCs without duplicating the chosen player", () => {
    const c = config(); c.project.subjects = [{ id: "npc", name: "Sol", description: "Guard", asset_ids: [] }, { id: "mira", name: "Mira", description: "Explorer", asset_ids: [] }];
    const result = ensurePlayer(c); expect(result.world.characters).toHaveLength(2); expect(result.player_character_id).toBe("mira");
    expect(result.world.characters.find(x => x.id === "npc")?.control).toBe("npc");
  });
  it("allows a large library while new uploads remain disconnected", () => {
    const c = config(), result = addGameAssets(c, Array.from({ length: 15 }, (_, i) => asset(`photo-${i}`)));
    expect(result.project.assets).toHaveLength(15); expect(result.project.assets.every(a => !a.enabled)).toBe(true); expect(c.project.assets).toEqual([]);
    expect(addGameAssets(result, [asset("photo-0")]).project.assets).toHaveLength(15);
  });
  it("binds clothes to the wearer while object owner is not automatically the holder", () => {
    const c = ensurePlayer(config()); c.project.assets = [asset("coat"), asset("key")];
    const withCoat = assignGameAsset(c, "coat", { semantic_role: "wardrobe", simple_owner_id: c.player_character_id });
    expect(withCoat.project.subjects[0].asset_ids).toContain("coat"); expect(withCoat.world.entities[0].worn_by_id).toBe(c.player_character_id);
    expect(withCoat.project.assets[0].simple_owner_id).toBeUndefined();
    expect(assignGameAsset(withCoat, "coat", { name: "Renamed coat" }).project.subjects[0].asset_ids).toContain("coat");
    const withKey = assignGameAsset(withCoat, "key", { semantic_role: "object", simple_owner_id: c.player_character_id });
    const key = withKey.world.entities.find(x => x.asset_ids.includes("key")); expect(key?.owner_id).toBe(c.player_character_id); expect(key?.holder_id).toBeUndefined();
  });
  it("renames characters without replacing their stable ID or behavior", () => {
    const c = ensurePlayer(config()); const result = updateCharacter(c, c.player_character_id, { name: "Mira Vale", personality: "Curious, impatient", goals: "Find the beacon" });
    expect(result.player_name).toBe("Mira Vale"); expect(result.project.subjects[0].name).toBe("Mira Vale"); expect(result.world.characters[0].goals).toBe("Find the beacon"); expect(c.player_name).toBe("Mira");
  });
  it("preserves unrelated render fields when changing pixel preview, steps, shape and multiple LoRAs", () => {
    const c = config(); c.project.comfy_render = { custom_backend: "keep", seed: 55 }; c.settings = { ...c.settings, experimental_preview: true, resolution: "0.2", duration: 3, steps: 16, aspect_ratio: "9:16", loras: [{ name: "one", strength: 1 }, { name: "two", strength: .5 }] };
    const result = prepareGameConfiguration(c); expect(result.project.duration).toBe(3); expect(result.project.aspect_ratio).toBe("9:16"); expect(result.project.comfy_render).toMatchObject({ custom_backend: "keep", seed: 55, resolution: "0.2", experimental_preview: true, steps: 16 }); expect(result.project.comfy_render.loras).toHaveLength(2);
    expect(result.project.shots.reduce((sum, shot) => sum + shot.duration, 0)).toBe(3);
    expect(result.world.current_location_id).toBeNull();
  });
  it("replaces the default pixel direction when the user chooses a different style", () => {
    const c = config(); c.settings.style = "Natural live action";
    const result = prepareGameConfiguration(c);
    expect(result.project.style.notes).toBe("Natural live action");
    expect(result.project.style.visual_style).toBe("Natural live action");
  });
  it("restores normalized world text without losing branch state or asset IDs", () => {
    const c = ensurePlayer(config()); const char = { ...characterFromSubject(c.project.subjects[0]), goals: ["Find Sol", "Return home"], private_knowledge: [{ fact: "The key is fake" }] };
    const story = { project: c.project, player_name: "Mira", player_character_id: c.player_character_id, settings: c.settings, premise: c.premise, world: { ...c.world, characters: [char], rules: ["No magic"] }, guides: [{ id: "g", revision: 2, text: "Keep it tense", scope: "persistent", enabled: true }] } as unknown as Story;
    const result = configurationFromStory(story, blankGameProject()); expect(result.world.characters[0].goals).toBe("Find Sol\nReturn home"); expect(result.world.characters[0].private_knowledge).toBe("The key is fake"); expect(result.world.rules).toBe("No magic"); expect(result.guides[0].revision).toBe(2);
  });
  it("replaces an image across current bindings without altering the original configuration", () => {
    const c = ensurePlayer(config()); c.project.assets = [asset("face")]; c.project.subjects[0].asset_ids = ["face"]; c.world.characters[0].asset_ids = ["face"];
    const result = replaceGameAsset(c, "face", asset("new-face")); expect(result.project.assets[0].id).toBe("new-face"); expect(result.world.characters[0].asset_ids).toEqual(["new-face"]); expect(c.project.assets[0].id).toBe("face");
  });
  it("removes an NPC without leaving missing holder references or changing history", () => {
    const c = ensurePlayer(config()); c.world.characters.push({ ...characterFromSubject({ id: "theo", name: "Theo", description: "", asset_ids: ["face"] }) });
    c.project.assets = [{ ...asset("face"), simple_owner_id: "theo" }];
    c.world.entities = [{ id: "key", name: "Key", kind: "object", owner_id: "theo", holder_id: "theo", asset_ids: [], affordances: [], state: {} }];
    c.project.subjects.push({ id: "theo", name: "Theo", description: "", asset_ids: ["face"] });
    c.project.shots[0].visible_subject_ids = [c.player_character_id, "theo"];
    c.project.shots[0].scene_contract = { environment: "Lighthouse", actors: [{ subject_id: "theo", activity: "hold", start: "Seated by the door", action: "Watches", end: "Still seated" }] };
    c.project.shots[0].scene_contract_source = "generated";
    const result = removeGameCharacter(c, "theo"); expect(result.world.entities[0].holder_id).toBeNull(); expect(result.project.assets[0].enabled).toBe(false); expect(c.world.entities[0].holder_id).toBe("theo");
    expect(result.project.shots[0].visible_subject_ids).toEqual([c.player_character_id]);
    expect(result.project.shots[0].scene_contract).toEqual({ environment: "Lighthouse", actors: [] });
    expect(c.project.shots[0].scene_contract.actors).toHaveLength(1);
    expect(() => removeGameCharacter(c, c.player_character_id)).toThrow("Choose a different player");
  });
  it("rejects invalid or duplicate tags instead of silently renaming a saved reference", () => {
    const c = config(); c.project.assets = [{ ...asset("key"), prompt_tag: "bad_tag" }];
    expect(() => prepareGameConfiguration(c)).toThrow("single hyphens"); c.project.assets[0].prompt_tag = "key"; c.project.assets.push({ ...asset("second"), prompt_tag: "key" }); expect(() => prepareGameConfiguration(c)).toThrow("used twice");
  });
});
