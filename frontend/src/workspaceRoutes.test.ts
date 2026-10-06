import { describe, expect, it } from "vitest";
import { resolveWorkspace, workspaceHref } from "./workspaceRoutes";
describe("Workspace navigation", () => {
  it("uses explicit routes before remembered workspace and supports old Game links", () => {
    expect(resolveWorkspace("/studio", "game")).toBe("studio");
    expect(resolveWorkspace("/game/", "studio")).toBe("game");
    expect(resolveWorkspace("/?game", "studio")).toBe("game");
    expect(resolveWorkspace("/", "game")).toBe("game");
    expect(resolveWorkspace("/", "invalid")).toBe("video");
    expect(resolveWorkspace('/video/', 'studio')).toBe('video');
    expect(resolveWorkspace('/studio', 'video')).toBe('studio');
    expect(resolveWorkspace('/')).toBe('video');
  });
  it("opens continuation links in Video and preserves bridge parameters", () => {
    expect(resolveWorkspace("/?continue_mmh3=clip.mmh3", "game")).toBe("video");
    expect(resolveWorkspace('/studio?continue_seed=1', 'game')).toBe('video');
    expect(workspaceHref("video", "/?continue_mmh3=clip.mmh3&project=123&embedded=1", true)).toBe("/video?continue_mmh3=clip.mmh3&project=123&embedded=1");
    expect(workspaceHref("game", "/studio?continue_mmh3=clip.mmh3&project=123&embedded=1#old")).toBe("/game?embedded=1");
    expect(workspaceHref("game", "/?game")).toBe("/game");
    expect(workspaceHref("game", "/?game=saved-story", true)).toBe("/game?game=saved-story");
  });
});
