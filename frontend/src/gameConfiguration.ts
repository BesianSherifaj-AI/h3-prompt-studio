import { newShot, retime, uid, type Asset, type Project } from "./model";
import { ensurePromptTags, renamePromptTag, replacePhoto, validTag } from "./tags";
import { pruneSceneActors } from "./sceneContinuityState";
import { DEFAULT_STORY_SETTINGS, type GameCharacter, type GameWorld, type Story, type StoryConfiguration } from "./storyTypes";

export const emptyWorld = (): GameWorld => ({ schema_version: 1, current_location_id: null, characters: [], locations: [], entities: [], objectives: [], rules: "", events: [] });

/** New games do not inherit another project's content or continuation state. */
export function blankGameProject(): Project {
  const shot = newShot(DEFAULT_STORY_SETTINGS.duration);
  shot.camera = { framing: "", movement: "", height: "", speed: "", focus: "" };
  return { schema_version: 1, id: uid(), title: "New game", mode: "t2va", duration: DEFAULT_STORY_SETTINGS.duration,
    aspect_ratio: "16:9", profile: "director", authoring_mode: "assisted", story: { text: "", locked: false },
    style: { notes: DEFAULT_STORY_SETTINGS.style || "" }, assets: [], subjects: [], shots: [shot], soundscape: "", music: "", custom_instructions: "",
    comfy_render: { resolution: "0.2", experimental_preview: true, steps: 8, quality: "fast", save_mmh3: true } };
}
export function characterFromSubject(subject: Project["subjects"][number], playerId = ""): GameCharacter {
  return { ...subject, control: subject.id === playerId ? "player" : "npc", personality: "", goals: "", speaking_style: "", private_knowledge: "", relationships: {}, location_id: null, witnessed_events: [] };
}
export function configurationFromStory(story: Story, fallback: Project): StoryConfiguration {
  const project = structuredClone(story.project || fallback);
  const playerId = story.player_character_id || project.subjects.find(s => s.name === story.player_name)?.id || "";
  const world = { ...emptyWorld(), ...structuredClone(story.world || {}) };
  const asText = (v: any) => Array.isArray(v) ? v.map(x => typeof x === "string" ? x : x.fact || "").filter(Boolean).join("\n") : typeof v === "string" ? v : "";
  world.rules = asText(world.rules);
  world.characters = world.characters.map(c => ({ ...c, goals: asText(c.goals), private_knowledge: asText(c.private_knowledge) }));
  if (!world.characters.length) world.characters = project.subjects.map(s => characterFromSubject(s, playerId));
  return { project, world, guides: structuredClone(story.guides || []), settings: { ...DEFAULT_STORY_SETTINGS, ...story.settings }, premise: story.premise || "", player_name: story.player_name || "", player_character_id: playerId };
}
export function ensurePlayer(config: StoryConfiguration): StoryConfiguration {
  const next = structuredClone(config), name = next.player_name.trim();
  if (!name) return next;
  let subject = next.project.subjects.find(s => s.id === next.player_character_id) || next.project.subjects.find(s => s.name.toLowerCase() === name.toLowerCase());
  if (!subject) { subject = { id: uid(), name, description: "", asset_ids: [] }; next.project.subjects.push(subject); }
  subject.name = name;
  next.player_character_id = subject.id;
  for (const person of next.project.subjects) if (!next.world.characters.some(c => c.id === person.id)) next.world.characters.push(characterFromSubject(person, subject.id));
  if (!next.world.characters.some(c => c.id === subject.id)) next.world.characters.push(characterFromSubject(subject, subject.id));
  next.world.characters = next.world.characters.map(c => ({ ...c, name: c.id === subject.id ? name : c.name, control: c.id === subject.id ? "player" : "npc" }));
  next.project.story = { ...next.project.story, text: next.premise };
  return next;
}
export function updateCharacter(config: StoryConfiguration, id: string, patch: Partial<GameCharacter>): StoryConfiguration {
  const next = structuredClone(config);
  next.world.characters = next.world.characters.map(c => c.id === id ? { ...c, ...patch } : c);
  const char = next.world.characters.find(c => c.id === id);
  if (!char) return next;
  let subject = next.project.subjects.find(c => c.id === id);
  if (!subject) { subject = { id, name: char.name, description: char.description, asset_ids: char.asset_ids }; next.project.subjects.push(subject); }
  Object.assign(subject, { name: char.name, description: char.description, asset_ids: char.asset_ids });
  if (id === next.player_character_id) next.player_name = char.name;
  return next;
}
export function removeGameCharacter(config: StoryConfiguration, id: string): StoryConfiguration {
  if (id === config.player_character_id) throw new Error("Choose a different player before removing your character.");
  const next = structuredClone(config), character = next.world.characters.find(c => c.id === id);
  next.world.characters = next.world.characters.filter(c => c.id !== id);
  next.project.subjects = next.project.subjects.filter(c => c.id !== id);
  for (const shot of next.project.shots) {
    shot.visible_subject_ids = shot.visible_subject_ids.filter(sid => sid !== id);
    shot.offscreen_subject_ids = shot.offscreen_subject_ids.filter(sid => sid !== id);
    pruneSceneActors(next.project, shot.id);
  }
  for (const asset of next.project.assets) if (asset.simple_owner_id === id || character?.asset_ids.includes(asset.id)) { asset.enabled = false; delete asset.simple_owner_id; delete asset.person_name; }
  for (const entity of next.world.entities) for (const field of ["owner_id", "holder_id", "worn_by_id"] as const) if (entity[field] === id) entity[field] = null;
  for (const other of next.world.characters) if (typeof other.relationships === "object") delete other.relationships[id];
  return next;
}
export function assignGameAsset(config: StoryConfiguration, id: string, patch: Partial<Asset>): StoryConfiguration {
  const next = structuredClone(config), asset = next.project.assets.find(a => a.id === id);
  if (!asset) return next;
  if (typeof patch.prompt_tag === "string" && validTag(patch.prompt_tag) && !next.project.assets.some(a => a.id !== id && a.prompt_tag === patch.prompt_tag)) { renamePromptTag(next.project, id, patch.prompt_tag); next.premise = next.project.story.text; }
  const previousOwner = asset.simple_owner_id || next.project.subjects.find(s => s.asset_ids.includes(id))?.id || "";
  Object.assign(asset, patch);
  const owner = String(Object.hasOwn(patch, "simple_owner_id") ? patch.simple_owner_id || "" : previousOwner);
  for (const person of next.project.subjects) {
    person.asset_ids = person.asset_ids.filter(a => a !== id);
    if (person.id === owner && (["face", "character", "wardrobe"].includes(asset.semantic_role) || asset.media_type === "audio")) person.asset_ids.push(id);
  }
  asset.person_name = next.project.subjects.find(c => c.id === owner)?.name || "";
  if (asset.semantic_role !== "object") delete asset.simple_owner_id;
  for (const char of next.world.characters) char.asset_ids = next.project.subjects.find(s => s.id === char.id)?.asset_ids || char.asset_ids;
  if (["object", "wardrobe"].includes(asset.semantic_role)) {
    let entity = next.world.entities.find(e => e.asset_ids.includes(id));
    if (!entity) { entity = { id: uid(), name: asset.name, kind: asset.semantic_role, asset_ids: [id], affordances: asset.semantic_role === "wardrobe" ? ["look", "wear", "remove"] : ["look", "take", "give", "use"], state: {} }; next.world.entities.push(entity); }
    entity.owner_id = owner;
    if (asset.semantic_role === "wardrobe") entity.worn_by_id = owner;
  }
  return next;
}
export function addGameAssets(config: StoryConfiguration, uploaded: Asset[]): StoryConfiguration {
  const next = structuredClone(config), seen = new Set(next.project.assets.map(a => a.id));
  for (const asset of uploaded) if (!seen.has(asset.id)) { next.project.assets.push({ ...asset, enabled: false }); seen.add(asset.id); }
  ensurePromptTags(next.project);
  return next;
}
export function replaceGameAsset(config: StoryConfiguration, id: string, uploaded: Asset): StoryConfiguration {
  const next = structuredClone(config);
  const old = next.project.assets.find(a => a.id === id);
  if (!old || old.media_type !== uploaded.media_type) throw new Error("Choose a replacement of the same media type.");
  if (old.media_type === "image") replacePhoto(next.project, id, structuredClone(uploaded));
  else next.project.assets = next.project.assets.map(a => a.id === id ? { ...uploaded, name: old.name, role: old.role, semantic_role: old.semantic_role, enabled: old.enabled, prompt_tag: old.prompt_tag, description: old.description, audio_use: old.audio_use, simple_owner_id: old.simple_owner_id, observation: "", approved_observation: "" } : a);
  for (const char of next.world.characters) char.asset_ids = char.asset_ids.map(a => a === id ? uploaded.id : a);
  for (const item of [...next.world.locations, ...next.world.entities]) item.asset_ids = item.asset_ids.map(a => a === id ? uploaded.id : a);
  if (Array.isArray(next.project.soundtrack_tracks)) next.project.soundtrack_tracks = next.project.soundtrack_tracks.map((track: any) => track.asset_id === id ? { ...track, asset_id: uploaded.id } : track);
  return next;
}
export function prepareGameConfiguration(config: StoryConfiguration): StoryConfiguration {
  const next = ensurePlayer(config);
  const seenTags = new Set<string>();
  for (const asset of next.project.assets) {
    if (asset.prompt_tag && !validTag(asset.prompt_tag)) throw new Error(`The tag for ${asset.name} needs letters, numbers and single hyphens, for example observatory-key.`);
    if (asset.prompt_tag && seenTags.has(asset.prompt_tag)) throw new Error(`The tag @${asset.prompt_tag} is used twice. Give each reference a unique tag.`);
    if (asset.prompt_tag) seenTags.add(asset.prompt_tag);
  }
  next.world.current_location_id ||= null;
  for (const character of next.world.characters) character.location_id ||= null;
  for (const entity of next.world.entities) for (const field of ["location_id", "owner_id", "holder_id", "worn_by_id"] as const) entity[field] ||= null;
  next.project.duration = next.settings.duration;
  if (Math.abs(next.project.shots.reduce((total, shot) => total + shot.duration, 0) - next.project.duration) > 0.001) next.project.shots = retime(next.project.shots, next.project.duration);
  next.project.aspect_ratio = String(next.settings.aspect_ratio || next.project.aspect_ratio);
  next.project.comfy_render = { ...next.project.comfy_render, ...Object.fromEntries(["resolution", "steps", "seed", "loras", "experimental_preview"].filter(k => next.settings[k] !== undefined).map(k => [k, next.settings[k]])) };
  next.project.style = { ...next.project.style, visual_style: next.settings.style || "", notes: next.settings.style || "" };
  const tracks = Array.isArray(next.project.soundtrack_tracks) ? next.project.soundtrack_tracks : [];
  next.project.soundtrack_tracks = next.project.assets.filter(a => ["audio", "video"].includes(a.media_type) && a.audio_use === "soundtrack").map(a => ({
    asset_id: a.id, start_seconds: 0, end_seconds: null, offset_seconds: 0, gain: 1, fade_in: 0, fade_out: 0, duck: false,
    ...tracks.find((t: any) => t.asset_id === a.id), enabled: a.enabled !== false,
  }));
  ensurePromptTags(next.project);
  return next;
}
