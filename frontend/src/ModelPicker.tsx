import React, { useId } from "react";
import { Bot, RefreshCw, Settings2 } from "lucide-react";
import { assistantProfileReady, CONTEXT_LENGTHS, type AssistantProfile, type AssistantWorkspace } from "./assistantProfiles";
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
  settings: AssistantProfile;
  workspace?: AssistantWorkspace;
  models?: StudioModel[];
  online: boolean;
  busy: boolean | string;
  stage?: string;
  activeProfile?: Partial<AssistantProfile> | null;
  onChange: (patch: Partial<AssistantProfile>) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onConnections: () => void;
  onLoad?: () => void | Promise<void>;
};

export function residentModelOptions(models: StudioModel[] = []) {
  return models.filter(m => m.vision === true && typeof m.size_bytes === 'number' && m.size_bytes > 0 && m.size_bytes <= 8_000_000_000)
    .sort((a,b) => (a.size_bytes || 0) - (b.size_bytes || 0) || a.id.localeCompare(b.id));
}

export function modelPickerOptions(models: StudioModel[] = [], selected = "") {
  const seen = new Set<string>();
  const unique = models.filter(model => {
    if (!model || typeof model.id !== "string" || !model.id || seen.has(model.id)) return false;
    seen.add(model.id);
    return true;
  });
  const names = unique.map(model => {
    const name = model.name || model.display_name || model.id;
    const variant = model.id.match(/@([^/@]+)$/)?.[1];
    return variant && !name.toLowerCase().includes(variant.toLowerCase()) ? `${name} · ${variant.toUpperCase()}` : name;
  });
  const counts = new Map<string, number>();
  names.forEach(name => counts.set(name.toLowerCase(), (counts.get(name.toLowerCase()) || 0) + 1));
  const options = unique.map((model, index) => ({
    id: model.id,
    label: `${names[index]}${counts.get(names[index].toLowerCase())! > 1 ? ` · ${model.id}` : ''} · ${model.vision === true ? "Reads photos" : model.vision === false ? "Text only" : "Photo support unknown"}${model.loaded ? " · loaded" : ""}`,
    vision: model.vision,
    missing: false,
  }));
  if (selected && !seen.has(selected)) options.unshift({ id: selected, label: `${selected} · saved selection, not listed`, vision: null, missing: true });
  return options;
}

export default function ModelPicker({ settings, workspace = 'studio', models = [], online, busy, stage, activeProfile, onChange, onRefresh, onConnections, onLoad }: ModelPickerProps) {
  const id = useId();
  const selected = settings?.model || "";
  const options = modelPickerOptions(models, selected);
  const current = options.find(option => option.id === selected);
  const disabled = Boolean(busy);
  const resident = settings.ai_memory_mode !== 'exclusive';
  const cpuCompatible = residentModelOptions(models).some(model => model.id === selected);
  const loaded = online && models.find(m => m.id === selected)?.loaded === true;
  const ready = assistantProfileReady(settings,activeProfile,loaded);
  const settledStage = !stage || /^(?:idle|AI ready|H3 ready)(?:$|\s|[·:])/i.test(stage);
  const contexts = [...new Set([...CONTEXT_LENGTHS, settings.context_length])].sort((a,b) => a-b);
  return (
    <section className="simple-model-picker" aria-label={`${workspace === 'game' ? 'Game' : 'Studio'} assistant model`}>
      <div className="simple-model-heading">
        <Bot size={18} aria-hidden="true" />
        <label htmlFor={id}>{workspace === 'game' ? 'Game' : 'Studio'} assistant</label>
        <span className={`simple-model-status ${online ? "online" : ""}`} role="status">{!online ? 'LM Studio offline' : disabled ? (!settledStage ? stage : typeof busy === 'string' ? busy : 'Work in progress') : workspace === 'game' && current?.vision === false ? 'Game needs a vision model' : ready ? `Ready · ${resident ? 'CPU' : 'GPU'} · ${settings.context_length.toLocaleString('en-US')} tokens` : loaded ? 'Model loaded · prepare this profile' : 'LM Studio connected · model on demand'}</span>
      </div>
      <div className="simple-model-controls">
        <select id={id} value={selected} disabled={disabled || !options.length} onChange={event => onChange({model:event.target.value})} aria-describedby={`${id}-help`} title={selected || "Select an installed LM Studio model"}>
          {!selected && <option value="">Choose an installed model…</option>}
          {options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        <button type="button" className="secondary" disabled={disabled} onClick={() => onRefresh()} title="Refresh installed LM Studio models"><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
        <button type="button" className="secondary" onClick={onConnections}><Settings2 size={14} aria-hidden="true" /> Connection</button>
      </div>
      {selected && <small className="simple-model-key" title={selected}>Exact model: <code>{selected}</code></small>}
      <div className="simple-model-profile">
        <label htmlFor={`${id}-context`}>Context<select id={`${id}-context`} value={settings.context_length} disabled={disabled} onChange={event => onChange({context_length:Number(event.target.value)})}>{contexts.map(value => <option key={value} value={value}>{value.toLocaleString('en-US')} tokens</option>)}</select></label>
        <label htmlFor={`${id}-placement`}>Model memory<select id={`${id}-placement`} value={resident ? 'resident_cpu' : 'exclusive'} disabled={disabled} onChange={event => onChange({ai_memory_mode:event.target.value as AssistantProfile['ai_memory_mode']})}><option value="exclusive">GPU · automatic H3 handoff</option><option value="resident_cpu" disabled={!cpuCompatible && !resident}>CPU · keep ready with H3</option></select></label>
        {onLoad && <button type="button" className="secondary simple-model-load" disabled={disabled || !online || !selected || current?.missing} onClick={onLoad}>{disabled ? 'Working…' : 'Prepare assistant'}</button>}
      </div>
      <p id={`${id}-help`} className="simple-model-help">
        {!online ? 'Start the local server in LM Studio, then refresh. Your saved model and context are kept.' :
          current?.missing ? 'This saved model is not in the current list. Refresh or select an installed model.' :
          resident && !cpuCompatible ? 'This model is not verified for CPU residency. Choose a vision model up to 8 GB or switch to GPU mode.' :
          current?.vision === false ? (workspace === 'game' ? 'Full Game requires a vision model for scene inspection. Choose a model marked Reads photos.' : 'Text model: uses your words and saved image descriptions. Choose a photo-capable model for image inspection.') :
          current?.vision !== true ? 'Photo support is not reported for this model. A photo-capable model is needed for image inspection.' :
          resident ? 'Keeps this assistant in system memory while H3 renders. Review planned actions before rendering. Choose a larger model for stronger reasoning, or GPU mode for faster replies.' :
          'Uses the GPU when needed; H3 and this assistant share GPU time. Model and context stay saved for this workspace.'}
      </p>
      {!settledStage && <small className="simple-model-handoff" role="status">{stage}</small>}
    </section>
  );
}
