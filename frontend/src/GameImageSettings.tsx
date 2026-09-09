import type { ImageGeneratorModel, StorySettings } from "./storyTypes";

type Props = {
  settings: StorySettings;
  generators: ImageGeneratorModel[];
  loading?: boolean;
  checked?: boolean;
  errors?: string[];
  onRefresh?: () => void;
  onChange: (key: string, value: unknown) => void;
};

/** Capability reads never change a saved generator or opt the game into extra jobs. */
export function GameImageSettings({ settings, generators, loading = false, checked = false, errors = [], onRefresh, onChange }: Props) {
  const selected = settings.image_model || "";
  // Matches the backend's established default; catalog.default_model is only
  // the first available alternative and must never silently replace this choice.
  const effectiveModel = selected || "z_image_turbo_bf16.safetensors";
  const selectedModel = generators.find(model => model.id === effectiveModel);
  const available = generators.filter(model => model.available !== false && model.compatible !== false);
  const missingSelection = !!selected && !selectedModel;
  const unavailable = !selectedModel || selectedModel.available === false || selectedModel.compatible === false;
  return <section aria-label="Extra reference images">
    <h3>Extra reference images</h3>
    <label className="game-checkbox" title="Uses the selected image generator for extra reference images before the video. This adds generation time.">
      <input type="checkbox" checked={settings.generate_references === true} onChange={event => onChange("generate_references", event.target.checked)}/>
      <span>Create extra reference images</span>
    </label>
    <p className="game-help">Game can use your description and existing references directly. Turn this on to create extra appearances with the selected image generator before the video; this takes more time.</p>
    <label>Image generator<select value={selected} onChange={event => onChange("image_model", event.target.value || undefined)}>
      <option value="">Default · Z-Image Turbo BF16</option>
      {missingSelection && <option value={selected}>{selected} · saved selection{checked && !loading ? " · unavailable or unconfirmed" : ""}</option>}
      {generators.map(model => <option key={model.id} value={model.id} disabled={model.available === false || model.compatible === false}>{model.name}{model.available === false || model.compatible === false ? " · unavailable" : ""}</option>)}
    </select></label>
    <div role="status" aria-live="polite">
      {loading ? <p className="game-help">Checking image generator availability…</p> : !checked ? <p className="game-help">Image generator availability has not been checked.</p> : <>
        {errors.length > 0 && <><p className="game-help">Some image generator inventory could not be checked. Availability may be incomplete; this does not confirm missing model files.</p><ul className="game-help">{errors.map((error, index) => <li key={index}>{error}</li>)}</ul></>}
        {unavailable && <p className="game-help">{selected ? "The saved generator" : "The configured default generator"} {errors.length ? "could not be confirmed." : "is unavailable in the latest ComfyUI inventory."} Choose an available generator or check ComfyUI and refresh. Your selection is kept.</p>}
        {selectedModel?.reason && <p className="game-help">{selectedModel.reason}</p>}
        {available.length > 0 ? <p className="game-help">{errors.length ? "Last reported available" : "Available"}: {available.map(model => model.name).join(", ")}.</p> : !errors.length && <p className="game-help">No image generator is available in the latest ComfyUI inventory. Keep extra reference images off to use text and existing references.</p>}
      </>}
    </div>
    {onRefresh && <button type="button" className="quiet" disabled={loading} onClick={onRefresh}>Refresh image generators</button>}
    <p className="game-help">These options apply to new turns. A saved turn keeps its original settings.</p>
  </section>;
}
