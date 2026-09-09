import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GameSoundtrack, GameVoiceInput } from "./GameAudio";
import { blankGameProject, prepareGameConfiguration, emptyWorld } from "./gameConfiguration";
import { DEFAULT_STORY_SETTINGS } from "./storyTypes";

describe("Game audio remains explicit and preserves video", () => {
  it("microphone controls do not submit the surrounding move form or request audio on opening", () => {
    const upload = vi.fn(), text = vi.fn();
    const markup = renderToStaticMarkup(<GameVoiceInput onUpload={upload} onAsset={vi.fn()} onText={text}/>);
    expect(markup).toContain('type="button"'); expect(markup).toContain("Record my speech");
    expect(markup).toContain("never sends a move automatically"); expect(upload).not.toHaveBeenCalled(); expect(text).not.toHaveBeenCalled();
  });
  it("does not show a mixing action for disconnected soundtrack assets", () => {
    const project = blankGameProject(); project.assets = [{ id: "a", name: "Music", media_type: "audio", role: "context", audio_use: "soundtrack", enabled: false } as any];
    expect(renderToStaticMarkup(<GameSoundtrack runId="run" project={project}/>)).toBe("");
  });
  it("preserves explicit soundtrack placement and supports a video soundtrack without using it as H3 conditioning", () => {
    const project = blankGameProject(); project.assets = [{ id: "a", name: "Music clip", media_type: "video", role: "context", audio_use: "soundtrack", enabled: true } as any];
    project.soundtrack_tracks = [{ asset_id: "a", offset_seconds: 1, gain: .4, fade_in: .2 }];
    const prepared = prepareGameConfiguration({ project, world: emptyWorld(), guides: [], settings: { ...DEFAULT_STORY_SETTINGS }, premise: "", player_name: "Mira", player_character_id: "" });
    expect(prepared.project.soundtrack_tracks[0]).toMatchObject({ asset_id: "a", offset_seconds: 1, gain: .4, fade_in: .2, enabled: true });
    expect(prepared.project.assets[0].role).toBe("context");
    const markup = renderToStaticMarkup(<GameSoundtrack runId="run" project={prepared.project}/>); expect(markup).toContain("Create soundtrack version"); expect(markup).not.toContain("<video");
  });
});
