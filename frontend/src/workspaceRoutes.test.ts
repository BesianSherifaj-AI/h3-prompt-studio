import { describe, expect, it } from "vitest";
import { resolveWorkspace, workspaceHref } from "./workspaceRoutes";
describe("Workspace navigation", () => {
  it("uses explicit routes before remembered workspace and supports old Game links", () => {
    expect(resolveWorkspace("/studio", "game")).toBe("studio");
    expect(resolveWorkspace("/game/", "studio")).toBe("game");
    expect(resolveWorkspace("/?game", "studio")).toBe("game");
    expect(resolveWorkspace("/", "game")).toBe("game");
    expect(resolveWorkspace("/", "invalid")).toBe("studio");
  });
  it("opens continuation links in Studio and preserves bridge parameters", () => {
    expect(resolveWorkspace("/?continue_mmh3=clip.mmh3", "game")).toBe("studio");
    expect(workspaceHref("studio", "/?continue_mmh3=clip.mmh3&project=123&embedded=1", true)).toBe("/studio?continue_mmh3=clip.mmh3&project=123&embedded=1");
    expect(workspaceHref("game", "/studio?continue_mmh3=clip.mmh3&project=123&embedded=1#old")).toBe("/game?embedded=1");
    expect(workspaceHref("game", "/?game")).toBe("/game");
    expect(workspaceHref("game", "/?game=saved-story", true)).toBe("/game?game=saved-story");
  });
});
