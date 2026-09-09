import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { gameActionMessage, gameCharacterStatus, gameWorldSummary, GameWorldStatus, isInventoryCommand, needsPlayerIdentity } from "./GameControls";
import type { SceneCatalog } from "./GameScenePanel";
import type { GameCharacter, Story } from "./storyTypes";

const character = (id: string, location_id = "street", state = {}): GameCharacter => ({
  id, name: id, location_id, state, control: id === "player" ? "player" : "npc", description: "",
  personality: "", goals: "", speaking_style: "", private_knowledge: "", relationships: {}, asset_ids: [],
});
const item = (id: string, extra: Record<string, unknown> = {}) => ({
  id, name: id, kind: "object", location_id: "street", holder_id: null, worn_by_id: null,
  asset_ids: [], affordances: ["take"], state: {}, ...extra,
});
const scene = (): Story => ({
  id: "game", active_branch_id: "main", player_character_id: "player", world: {
    schema_version: 1, current_location_id: "street", rules: "", objectives: [], events: [],
    locations: [{ id: "street", name: "Lantern Street", description: "", exits: [], asset_ids: [] }],
    characters: [character("player"), character("Mara"), character("Remote", "tower"),
      character("Defeated guard", "street", { defeated: true }), character("Dead guard", "street", { dead: true })],
    entities: [item("Key", { holder_id: "player" }), item("Coat", { worn_by_id: "player" }), item("Coin"),
      item("Secret", { state: { hidden: true } }), item("Remote item", { location_id: "tower" }),
      item("Guard weapon", { holder_id: "Mara" })],
  },
} as unknown as Story);

describe("playable world controls", () => {
  it("asks which visible person is the player before ambiguous movement, while preserving bound and first-person play", () => {
    const story = scene();
    const visible = { status: "ready", targets: [{ kind: "person", known_id: null }, { kind: "person", known_id: null }] } as SceneCatalog;
    expect(needsPlayerIdentity(story, visible)).toBe(true);
    visible.targets[0].known_id = "player";
    expect(needsPlayerIdentity(story, visible)).toBe(false);
    visible.targets[0].known_id = null;
    story.world!.characters[0].state = { visual_anchor: "Purple shirt" };
    expect(needsPlayerIdentity(story, visible)).toBe(false);
    story.world!.characters[0].state = {};
    story.project = { game_viewpoint: "pov" } as unknown as Story["project"];
    expect(needsPlayerIdentity(story, visible)).toBe(false);
  });
  it("opens inventory locally for exact commands without swallowing narrative actions", () => {
    for (const message of ["inventory", "Show inventory", "open inventory.", " check inventory ", "my inventory", "I check my inventory", "I open my inventory!"]) expect(isInventoryCommand(message)).toBe(true);
    for (const message of ["I check my inventory and show Mara the key.", "Show inventory to the guard", "Take inventory of the city", '"I check my inventory"', "I say, “my inventory.”"]) expect(isInventoryCommand(message)).toBe(false);
  });
  it("distinguishes carried and worn inventory from visible ground items", () => {
    const summary = gameWorldSummary(scene());
    expect(summary.location?.name).toBe("Lantern Street");
    expect(summary.inventory.map(item => item.name)).toEqual(["Key", "Coat"]);
    expect(summary.ground.map(item => item.name)).toEqual(["Coin"]);
    expect(summary.nearby.map(character => character.name)).not.toContain("Remote");
    expect(summary.recipients.map(character => character.name)).toEqual(["Mara"]);
  });

  it("updates inventory after a saved handoff without treating owned items as carried", () => {
    const story = scene();
    Object.assign(story.world!.entities[0], { holder_id: "Mara", owner_id: "player" });
    expect(gameWorldSummary(story).inventory.map(item => item.name)).toEqual(["Coat"]);
  });

  it("does not offer an unusable inventory handoff when every nearby NPC is incapacitated", () => {
    const story = scene(); story.world!.characters.find(character => character.name === "Mara")!.state = { unconscious: true };
    expect(gameWorldSummary(story).recipients).toEqual([]);
    const html = renderToStaticMarkup(<GameWorldStatus story={story} target="key" disabled={false}
      onSelect={() => {}} onAction={() => {}} catalog={{ targets: [], actions: [{ kind: "give", target_id: "Key", enabled: true }] }}/>);
    expect(html).toContain('disabled="" title="No character here can receive an item.">Give…</button>');
  });

  it("names the NPC and item in structured player actions", () => {
    expect(gameActionMessage({ kind: "talk", target_id: "Mara" }, scene())).toBe("I talk to Mara.");
    expect(gameActionMessage({ kind: "give", target_id: "Key" }, scene(), "Mara")).toBe("I attempt to give Key to Mara.");
    expect(gameActionMessage({ kind: "attack", target_id: "Mara" }, scene())).toBe("I attempt to attack Mara.");
  });

  it("renders inventory, character status, and only server-offered pickup/drop actions", () => {
    const html = renderToStaticMarkup(<GameWorldStatus story={scene()} target="" disabled={false}
      onSelect={() => {}} onAction={() => {}} catalog={{ targets: [], actions: [
        { kind: "drop", target_id: "Key", enabled: true }, { kind: "give", target_id: "Key", enabled: true },
        { kind: "take", target_id: "Coin", enabled: true },
      ] }}/>);
    expect(html).toContain("Lantern Street");
    expect(html).toContain("Inventory · 2");
    expect(html).toContain("Carrying");
    expect(html).toContain("Worn");
    expect(html).toContain("Drop</button>");
    expect(html).toContain("Give…</button>");
    expect(html).toContain("Pick up</button>");
    expect(html).toContain("Defeated");
    expect(html).not.toContain("Secret");
    expect(html).not.toContain("Remote item");
  });

  it("uses the current player's location after movement and handles an empty world", () => {
    const story = scene(); story.world!.characters[0].location_id = "tower";
    expect(gameWorldSummary(story).recipients.map(character => character.name)).toEqual(["Remote"]);
    expect(gameWorldSummary(story).ground.map(item => item.name)).toEqual(["Remote item"]);
    expect(gameWorldSummary({ id: "legacy-game" } as Story).inventory).toEqual([]);
    expect(gameCharacterStatus(character("Mara", "street", { health: 2 }))).toBe("Health 2");
    expect(gameCharacterStatus(character("Mara", "street", { alive: false }))).toBe("Dead");
    expect(gameCharacterStatus(character("Mara", "street", { status: "Unconscious" }))).toBe("Unconscious");
  });
  it("distinguishes accepted local movement coordinates from a physical map", () => {
    const story = scene(); story.navigation = { version: 1, frame_id: "f", position: [0, 1], current_run_id: "run", views: [{ run_id: "run", frame_id: "f", position: [0, 1] }] };
    const html = renderToStaticMarkup(<GameWorldStatus story={story} target="" disabled={false} onSelect={() => {}} onAction={() => {}}/>);
    expect(html).toContain("Local steps · 0, 1");
    expect(html).toContain("1 saved view(s)");
    expect(html).toContain("These are steps within the scene");
  });
});
