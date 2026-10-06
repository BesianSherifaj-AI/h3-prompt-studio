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

function renderStudio(p: Project, overrides: Partial<SimpleStudioProps> = {}) {
  const noop = () => {}, asyncNoop = async () => {};
  const props: SimpleStudioProps = { project: p, update: noop, checkpointUpdate: noop,
    onRestore: noop, onReplacePhoto: asyncNoop, onAddFiles: asyncNoop, onGenerate: noop,
    onBuild: noop, busy: "", progress: "", error: "", notice: "", result: null,
    currentPrompt: "", resultFresh: false, referenceMap: [], onCopy: noop, onSave: noop,
    onAdvanced: noop, onProjects: noop, onNew: noop, onConnections: noop, onFiles: noop,
    onUndo: noop, canUndo: false, connectionOnline: false, onSendToComfy: noop,
    canReturn: false, onContinue: noop, comfyPanel:<div>Video fixture</div>, settingsPanel:<div>Render settings fixture</div> };
  return renderToStaticMarkup(<SimpleStudio {...props} {...overrides} />);
}
function renderCount(p: Project) {
  const html = renderStudio(p);
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

describe('Simple editor workspace', () => {
  it('keeps all editor tools in three accessible panels beside the video without the separate-clip shortcut', () => {
    const html=renderStudio(project([photo('face')]));
    expect(html).toContain('role="tablist" aria-label="Scene editor"');
    expect(html).toContain('id="simple-tab-story" type="button" role="tab" aria-selected="true"');
    expect(html).toContain('>Write</button>');
    expect(html).toContain('id="simple-panel-photos" hidden=""');
    expect(html).toContain('id="simple-panel-settings" hidden=""');
    expect(html).toContain('Render settings fixture');
    expect(html).toContain('aria-label="Video and continuation"');
    expect(html).toContain('Video fixture');
    expect(html).toContain('Timing &amp; continuous filming');
    expect(html).not.toContain('Continue the story in another clip');
    expect(html).not.toContain('Start next');
    expect(html).not.toContain('Plan a separate scene');
    const writing=html.split('id="simple-panel-story"')[1].split('</section>')[0];
    expect(writing).toContain('Video length'); expect(writing).toContain('Shape');
  });
  it('starts every project on Write, with photos optional', () => {
    const html=renderStudio(project([]));
    expect(html).toContain('id="simple-tab-story" type="button" role="tab" aria-selected="true"');
    expect(html).toContain('id="simple-panel-photos" hidden=""');
    expect(html).toContain('Photos · optional');
  });
});


describe('Video writing workflow', () => {
  it('leaves project identity and saving to the shared project bar', () => {
    const html=renderStudio(project([]), {savedStatus:'Not saved',onSaveProject:()=>{}});
    expect(html).not.toContain('aria-label="Project name"');
    expect(html).not.toContain('Saved projects');
    expect(html).not.toContain('Save now');
    expect(html).not.toContain('Saved automatically');
    expect(html).toContain('aria-label="Video creation steps"');
  });
  it('keeps optional tools closed and puts the prompt before video playback on mobile', () => {
    const html=renderStudio(project([]), {modelPicker:<div>Local model fixture</div>});
    expect(html).toContain('<details class="simple-writing-tools">');
    expect(html).toContain('<details class="simple-scene-controls">');
    expect(html).toContain('<details class="simple-ai-settings">');
    expect(html).toContain('<details class="simple-editor-tools">');
    expect(html.indexOf('id="simple-result"')).toBeLessThan(html.indexOf('id="simple-video"'));
    expect(html).toContain('Qwen 3.8 27B');
  });
  it('cannot prepare an empty idea or export a stale prompt', () => {
    const p=project([]);p.story.text='';
    const html=renderStudio(p, {currentPrompt:'An older prompt',resultFresh:false});
    expect(html).toMatch(/class="simple-generate" disabled=""/);
    expect(html).toMatch(/disabled=""><svg[^]*?<\/svg> Copy prompt<\/button>/);
    expect(html).toContain('Your idea changed. Prepare the prompt again');
    expect(html).not.toContain('<span class="simple-ready">');
  });
});
