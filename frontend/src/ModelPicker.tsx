import React, { useId } from "react";
import { Bot, RefreshCw, Settings2, Zap } from "lucide-react";
import "./ModelPicker.css";

export type StudioModel = {
  id: string;
  name?: string;
  display_name?: string;
  vision?: boolean | null;
  loaded?: boolean | null;
  size_bytes?: number;
};

export type ModelPickerProps = {
  settings: { model?: string; ai_memory_mode?: string };
  models?: StudioModel[];
  online: boolean;
  busy: boolean | string;
  onSelect: (model: string) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onConnections: () => void;
  onResident?: (model: string) => void | Promise<void>;
  onLoad?: () => void | Promise<void>;
};

export function residentModelOptions(models: StudioModel[] = []) {
  return models.filter(m => /0[.]8b/i.test(m.id) && m.vision === true && typeof m.size_bytes === 'number' && m.size_bytes > 0 && m.size_bytes <= 1_500_000_000)
    .sort((a,b) => Number(b.id === 'qwen3.5-0.8b@q8_0') - Number(a.id === 'qwen3.5-0.8b@q8_0') || a.id.localeCompare(b.id));
}

export function modelPickerOptions(models: StudioModel[] = [], selected = "") {
  const seen = new Set<string>();
  const options = models.filter(model => {
    if (!model || typeof model.id !== "string" || !model.id || seen.has(model.id)) return false;
    seen.add(model.id);
    return true;
  }).map(model => ({
    id: model.id,
    label: `${model.name || model.display_name || model.id} · ${model.vision === true ? "Reads photos" : model.vision === false ? "Text only" : "Photo support unknown"}${model.loaded ? " · loaded" : ""}`,
    vision: model.vision,
    missing: false,
  }));
  if (selected && !seen.has(selected)) options.unshift({ id: selected, label: `${selected} · saved selection, not listed`, vision: null, missing: true });
  return options;
}

export default function ModelPicker({ settings, models = [], online, busy, onSelect, onRefresh, onConnections, onResident, onLoad }: ModelPickerProps) {
  const id = useId();
  const selected = settings?.model || "";
  const options = modelPickerOptions(models, selected);
  const current = options.find(option => option.id === selected);
  const disabled = Boolean(busy);
  const smallModels = residentModelOptions(models);
  const resident = settings.ai_memory_mode === 'resident_small';
  const loaded = models.find(m => m.id === selected)?.loaded === true;
  return (
    <section className="simple-model-picker" aria-label="Prompt assistant model">
      <div className="simple-model-heading">
        <Bot size={18} aria-hidden="true" />
        <label htmlFor={id}>Prompt assistant</label>
        <span className={`simple-model-status ${online ? "online" : ""}`}>{online ? "LM Studio connected" : "LM Studio offline"}</span>
      </div>
      <div className="simple-model-controls">
        <select id={id} value={selected} disabled={disabled || !options.length} onChange={event => onSelect(event.target.value)} aria-describedby={`${id}-help`} title={selected || "Select an installed LM Studio model"}>
          {!selected && <option value="">Choose an installed model…</option>}
          {options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        <button type="button" className="secondary" disabled={disabled} onClick={() => onRefresh()} title="Refresh installed LM Studio models"><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
        <button type="button" className="secondary" onClick={onConnections}><Settings2 size={14} aria-hidden="true" /> Connection</button>
      </div>
      <p id={`${id}-help`} className="simple-model-help">
        {!online ? "Start the local server in LM Studio, then refresh. You can keep editing your film." :
          current?.missing ? "This saved model is not in the current list. Refresh or choose an installed model before making a prompt." :
          current?.vision === false ? "This model uses your words and saved image descriptions. Choose “Reads photos” to inspect new photos." :
          current?.vision !== true ? "Photo support is not reported for this model. Use a model marked “Reads photos” when you want image analysis." :
          "This model can see your reference photos as it improves your idea."}
      </p>
      {onResident && <div className="simple-model-resident">
        <button type="button" className={resident ? 'secondary' : 'primary'} disabled={disabled || !online || !smallModels.length} onClick={() => onResident(smallModels.some(m=>m.id===selected)?selected:smallModels[0].id)}>
          <Zap size={15} aria-hidden="true" />{resident ? '0.8B stays ready with H3' : 'Use 0.8B with H3'}
        </button>
        {resident && onLoad && <button type="button" className="secondary" disabled={disabled || !online || loaded} onClick={onLoad}>{loaded ? 'Assistant loaded' : 'Load assistant'}</button>}
      </div>}
      <small className="simple-model-handoff">{resident
        ? 'Small assistant in system memory · H3 on the GPU. Both stay loaded. Best for simple prompt edits. Review story choices; choose a larger model for stronger continuity.'
        : 'Your selected model loads when needed. Larger assistants and H3 take turns using GPU memory.'}</small>
    </section>
  );
}
