import type { GameIntent, Story, StoryTurn } from "./storyTypes";

export function playerIdentityFailure(turn?: StoryTurn) {
  return !!turn && turn.status === "failed" && !turn.run_id && !turn.plan &&
    /^(Identify your character first|Choose your character to start moving)/i.test(turn.error || "");
}

export function movementNeedsPlayerChoice(story: Story, intent?: GameIntent) {
  if (!story.active_run_id || story.project?.game_viewpoint === "pov" || intent?.kind !== "move" ||
      intent.camera === "camera" || intent.target_id) return false;
  const player = story.world?.characters.find(person => person.id === story.player_character_id);
  return !String(player?.state?.visual_anchor || player?.description || "").trim();
}
