import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import SimpleStudio, { type SimpleStudioProps } from "./SimpleStudio";
import { newShot, type Asset, type Project } from "./model";
import { simpleIssues } from "./simple";

function photo(id: string, role = "reference_image", enabled = true): Asset {
  return { id, name: id, media_type: "image", role, semantic_role: "other", enabled,
    locked_order: false, description: "", observation: "", approved_observation: "" };
}

function project(assets: Asset[]): Project {
  return { schema_version: 1, id: "photo-count-fixture", title: "Photo count test", mode: "ref2va",
    duration: 5, aspect_ratio: "16:9", profile: "director", authoring_mode: "manual",
    story: { text: "A lantern lights up in the garden.", locked: true }, style: {}, assets,
    subjects: [], shots: [newShot(5)], soundscape: "", music: "", custom_instructions: "" };
}

function renderCount(p: Project) {
  const noop = () => {}, asyncNoop = async () => {};
  const props: SimpleStudioProps = { project: p, update: noop, checkpointUpdate: noop,
    onRestore: noop, onReplacePhoto: asyncNoop, onAddFiles: asyncNoop, onGenerate: noop,
    onBuild: noop, busy: "", progress: "", error: "", notice: "", result: null,
    currentPrompt: "", resultFresh: false, referenceMap: [], onCopy: noop, onSave: noop,
    onAdvanced: noop, onProjects: noop, onNew: noop, onConnections: noop, onFiles: noop,
    onUndo: noop, canUndo: false, connectionOnline: false, onSendToComfy: noop,
    canReturn: false, onContinue: noop };
  const html = renderToStaticMarkup(<SimpleStudio {...props} />);
  return html.match(/<span class="simple-count">([^<]+)<\/span>/)?.[1];
}

describe("Simple mode photo summary", () => {
  it("separates eight video references from two inspiration photos without raising the nine-reference limit", () => {
    const p = project([...Array.from({ length: 8 }, (_, i) => photo(`reference-${i}`)),
      photo("planning-location", "context"), photo("planning-style", "context"),
      photo("unused-reference", "reference_image", false),
      { ...photo("audio-guide", "context"), media_type: "audio" }]);
    expect(renderCount(p)).toBe("8 video references · 2 inspiration");
    expect(simpleIssues(p).some(message => message.includes("nine reference"))).toBe(false);
    p.assets.push(photo("ninth"));
    expect(simpleIssues(p).some(message => message.includes("nine reference"))).toBe(false);
    p.assets.push(photo("tenth"));
    expect(simpleIssues(p).some(message => message.includes("nine reference"))).toBe(true);
  });

  it("uses readable singular and text-only counts and excludes disabled inspiration", () => {
    expect(renderCount(project([photo("face"), photo("idea", "context", false)]))).toBe("1 photo in use");
    expect(renderCount(project([photo("face"), photo("idea", "context")]))).toBe("1 video reference · 1 inspiration");
    const p = project([photo("idea", "context")]); p.mode = "t2va";
    expect(renderCount(p)).toBe("0 video references · 1 inspiration");
    expect(renderCount(project([]))).toBe("0 photos in use");
  });
});
