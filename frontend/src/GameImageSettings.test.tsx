import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GameImageSettings } from "./GameImageSettings";
import { DEFAULT_STORY_SETTINGS } from "./storyTypes";
import { imageGeneratorInventory } from "./storyTypes";

describe("optional Game reference images", () => {
  it("defaults to text and existing references without requesting images or changing settings", () => {
    const onChange = vi.fn(), onRefresh = vi.fn();
    const html = renderToStaticMarkup(<GameImageSettings settings={{ ...DEFAULT_STORY_SETTINGS }} generators={[]} checked onChange={onChange} onRefresh={onRefresh}/>);
    expect(DEFAULT_STORY_SETTINGS.generate_references).toBe(false);
    expect(html).toContain("Create extra reference images");
    expect(html).not.toContain('checked=""');
    expect(html).toContain("Keep extra reference images off");
    expect(onChange).not.toHaveBeenCalled();
    expect(onRefresh).not.toHaveBeenCalled();
  });
  it("roundtrips explicit opt-in and keeps an unavailable saved generator selected", () => {
    const onChange = vi.fn();
    const html = renderToStaticMarkup(<GameImageSettings settings={{ ...DEFAULT_STORY_SETTINGS, generate_references: true, image_model: "saved-model" }} generators={[{ id: "other-model", name: "Available model", available: true }]} checked onChange={onChange}/>);
    expect(html).toContain('checked=""');
    expect(html).toContain('value="saved-model" selected=""');
    expect(html).toContain("saved-model · saved selection");
    expect(html).toContain("The saved generator is unavailable");
    expect(html).toContain("Available: Available model");
    expect(onChange).not.toHaveBeenCalled();
  });
  it("distinguishes an offline inventory from confirmed unavailable models", () => {
    const html = renderToStaticMarkup(<GameImageSettings settings={{ ...DEFAULT_STORY_SETTINGS, image_model: "saved-model" }} generators={[]} checked errors={["ComfyUI node inventory is unavailable."]} onChange={vi.fn()}/>);
    expect(html).toContain("does not confirm missing model files");
    expect(html).toContain("The saved generator could not be confirmed");
    expect(html).toContain("ComfyUI node inventory is unavailable.");
    expect(html).not.toContain("The saved generator is unavailable");
    expect(html).not.toContain("No image generator is available");
  });
  it("keeps a failed compatibility selection visible with its reason", () => {
    const html = renderToStaticMarkup(<GameImageSettings settings={{ ...DEFAULT_STORY_SETTINGS, image_model: "known-model" }} generators={[{ id: "known-model", name: "Known model", compatible: false, reason: "Native nodes unavailable." }]} checked onChange={vi.fn()}/>);
    expect(html).toContain('disabled="" selected=""');
    expect(html).toContain("Native nodes unavailable.");
  });
  it("shows checking status instead of diagnosing the empty initial catalog", () => {
    const html = renderToStaticMarkup(<GameImageSettings settings={{ ...DEFAULT_STORY_SETTINGS }} generators={[]} loading onRefresh={vi.fn()} onChange={vi.fn()}/>);
    expect(html).toContain("Checking image generator availability");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Refresh image generators<\/button>/);
    expect(html).not.toContain("No image generator is available");
  });
  it("shows the actual configured default, not the first available alternative", () => {
    const generators = imageGeneratorInventory({ models: ["alternative"], default_model: "alternative", servers: [{ comfy_url: "http://127.0.0.1:8188", model_missing: { "z_image_turbo_bf16.safetensors": ["CLIPLoader: encoder.safetensors"] } }] });
    const html = renderToStaticMarkup(<GameImageSettings settings={{ ...DEFAULT_STORY_SETTINGS }} generators={generators} checked onChange={vi.fn()}/>);
    expect(html).toContain('value="" selected="">Default · Z-Image Turbo BF16');
    expect(html).toContain("The configured default generator is unavailable");
    expect(html).toContain("CLIPLoader: encoder.safetensors");
    expect(html).toContain("Available: alternative");
  });
  it("does not disable a model available on another server or turn an offline error into missing files", () => {
    const models = imageGeneratorInventory({ models: ["working"], errors: ["Second server is offline"], servers: [
      { model_missing: { working: ["encoder"], missing: ["VAE"] } },
      { model_missing: null },
    ] });
    expect(models.find(model => model.id === "working")).toMatchObject({ available: true });
    expect(models.find(model => model.id === "missing")).toMatchObject({ available: false });
    expect(imageGeneratorInventory({ errors: ["Offline"], servers: [] })).toEqual([]);
  });
});
