import { useEffect, useRef, useState } from 'react';
import { ImagePlus, RefreshCw } from 'lucide-react';
import { api, ApiError } from './api';
import type { Asset, Project } from './model';
import { imageGeneratorInventory, type ImageGeneratorModel } from './storyTypes';
import { addGeneratedKeyframe, defaultKeyframeDraft, keyframePending, keyframeSpec, MAGE_FLOW, type KeyframeDraft, type KeyframeRole } from './keyframeGeneration';
import './KeyframeGenerator.css';

type ImageRun = { id: string; status: string; stage?: string; error?: string; asset?: Asset; can_resume?: boolean; can_cancel?: boolean };
type Ticket = { request_id: string; spec: ReturnType<typeof keyframeSpec> };
type Saved = { draft: KeyframeDraft; ticket?: Ticket; run?: ImageRun };
const OPTIONS = { timeoutMs: 30_000 };
export function loadKeyframeDraft(projectId: string): Saved {
  try {
    const value = JSON.parse(localStorage.getItem(`h3-keyframe:${projectId}`) || 'null');
    if (value && typeof value.draft?.prompt === 'string' && Array.isArray(value.draft?.reference_asset_ids)) return value;
  } catch { /* A new draft needs no network request. */ }
  return { draft: defaultKeyframeDraft() };
}

export default function KeyframeGenerator({ project, update, busy = false }: {
  project: Project; update: (change: (project: Project) => void) => void; busy?: boolean;
}) {
  const [saved, setSaved] = useState(() => loadKeyframeDraft(project.id)), savedRef = useRef(saved);
  const [open, setOpen] = useState(false), [models, setModels] = useState<ImageGeneratorModel[]>([]);
  const [checking, setChecking] = useState(false), [checked, setChecked] = useState(false), [pending, setPending] = useState(false);
  const [error, setError] = useState(''), [catalogError, setCatalogError] = useState(''), [role, setRole] = useState<KeyframeRole>('first_frame');
  const operation = useRef(false);
  const draft = saved.draft, run = saved.run, unresolved = !!saved.ticket && (!run || keyframePending(run.status));
  const selected = models.find(model => model.id === draft.model);
  const available = selected && selected.available !== false && selected.compatible !== false;
  const mage = draft.model === MAGE_FLOW;
  const save = (next: Saved, required = false) => {
    try { localStorage.setItem(`h3-keyframe:${project.id}`, JSON.stringify(next)); }
    catch { if (required) throw new Error('Allow local browser storage before generating so this request can be recovered after refresh.'); }
    savedRef.current = next; setSaved(next);
  };
  const change = (patch: Partial<KeyframeDraft>) => save({ ...savedRef.current, draft: { ...savedRef.current.draft, ...patch } });
  const catalog = async () => {
    setChecking(true); setCatalogError('');
    try { const value = await api('/assets/generators', undefined, undefined, undefined, { timeoutMs: 60_000 }); setModels(imageGeneratorInventory(value)); setChecked(true); setCatalogError((value.errors || []).join(' ')); }
    catch (e) { setCatalogError((e as Error).message); }
    finally { setChecking(false); }
  };
  useEffect(() => { if (open && !checked && !checking) void catalog(); }, [open]);
  const check = async () => {
    const ticket = savedRef.current.ticket; if (!ticket) return;
    const value = await api(`/asset-runs/${ticket.request_id}`, undefined, undefined, undefined, OPTIONS);
    if (savedRef.current.ticket?.request_id === ticket.request_id) { save({ ...savedRef.current, run: value }); setError(''); }
  };
  useEffect(() => {
    if (!saved.ticket || (run && !keyframePending(run.status))) return;
    let alive = true, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (document.visibilityState !== 'hidden') try { await check(); } catch (e) { if (alive) setError(`Could not confirm this saved request. ${(e as Error).message}`); }
      if (alive) timer = setTimeout(poll, 4000);
    };
    void poll(); return () => { alive = false; clearTimeout(timer); };
  }, [saved.ticket?.request_id, run?.status]);
  const perform = async (fn: () => Promise<void>) => {
    if (operation.current) return;
    operation.current = true; setPending(true); setError('');
    try { await fn(); } catch (e) { setError((e as Error).message); }
    finally { operation.current = false; setPending(false); }
  };
  const submit = async (ticket: Ticket) => {
    try {
      const value = await api('/asset-runs', ticket, undefined, undefined, OPTIONS);
      save({ ...savedRef.current, run: value }); setError('');
    } catch (e) {
      // A rejected validation request cannot launch a job. Other failures keep
      // the exact durable ticket until explicit recovery; no write is retried.
      if (e instanceof ApiError && [400, 422].includes(e.status)) save({ ...savedRef.current, run: { id: ticket.request_id, status: 'failed', error: e.message } });
      else save({ ...savedRef.current, run: { id: ticket.request_id, status: 'unknown', stage: 'Checking saved submission' } });
      throw e;
    }
  };
  const generate = () => perform(async () => {
    if (unresolved) throw new Error('Check the existing request before generating another image.');
    const spec = keyframeSpec(savedRef.current.draft, project, Math.floor(Math.random() * 2 ** 32));
    const ticket = { request_id: crypto.randomUUID(), spec };
    save({ ...savedRef.current, ticket, run: { id: ticket.request_id, status: 'unknown', stage: 'Submitting image' } }, true);
    await submit(ticket);
  });
  const existing = !!run?.asset && project.assets.some(asset => asset.id === run.asset!.id);
  return <details className="keyframe-generator" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><ImagePlus size={17}/>Create or edit a keyframe <span>Local image generator</span></summary>
    <div className="keyframe-body">
      <p>Describe one clear story beat. Use MageFlow with existing images to carry the same character and setting into another pose.</p>
      <label>Image name<input value={draft.name} maxLength={100} onChange={event => change({ name: event.target.value })}/></label>
      <label>Keyframe prompt<textarea aria-label="Keyframe prompt" rows={4} value={draft.prompt} maxLength={6000} placeholder="Keep the same character and room. Move the character's hand toward the red button…" onChange={event => change({ prompt: event.target.value })}/></label>
      <label>Keyframe model<select aria-label="Keyframe model" value={draft.model} onChange={event => change({ model: event.target.value })}>
        {!selected && <option value={draft.model}>{draft.model} · {checked ? 'unconfirmed' : 'saved choice'}</option>}
        {models.map(model => <option key={model.id} value={model.id} disabled={model.available === false || model.compatible === false}>{model.id === MAGE_FLOW ? 'MageFlow · edit from references' : model.name}{model.available === false ? ' · unavailable' : ''}</option>)}
      </select></label>
      <div className="keyframe-dimensions"><label>Width<input type="number" min={mage ? 512 : 128} max={mage ? 2048 : 1024} step={16} value={draft.width} onChange={event => change({ width: Number(event.target.value) })}/></label><label>Height<input type="number" min={mage ? 512 : 128} max={mage ? 2048 : 1024} step={16} value={draft.height} onChange={event => change({ height: Number(event.target.value) })}/></label></div>
      {mage && <fieldset><legend>Reference images · select 1–4</legend><div className="keyframe-references">{project.assets.filter(asset => asset.media_type === 'image').map(asset => <label key={asset.id}><input type="checkbox" checked={draft.reference_asset_ids.includes(asset.id)} disabled={!draft.reference_asset_ids.includes(asset.id) && draft.reference_asset_ids.length >= 4} onChange={event => change({ reference_asset_ids: event.target.checked ? [...draft.reference_asset_ids, asset.id] : draft.reference_asset_ids.filter(id => id !== asset.id) })}/><span>{asset.name || asset.id}</span></label>)}</div>{!project.assets.some(asset => asset.media_type === 'image') && <small>Add an existing photo first, or choose Z-Image to create the opening image.</small>}</fieldset>}
      <div className="keyframe-actions"><button type="button" disabled={pending || busy || unresolved || !available || !draft.prompt.trim()} onClick={() => void generate()}>Generate keyframe</button><button type="button" disabled={checking} onClick={() => void catalog()}><RefreshCw size={14}/>{checking ? 'Checking…' : 'Refresh models'}</button></div>
      {checked && !available && <p className="keyframe-help">Your chosen model is unavailable or unconfirmed. Refresh after starting ComfyUI, or choose a listed model. Your draft is kept.</p>}
      {selected?.reason && <p className="keyframe-help">{selected.reason}</p>}{catalogError && <p className="keyframe-error" role="alert">{catalogError}</p>}
      {pending && <p role="status">Saving image request…</p>}
      {run && <p role="status">{run.stage || run.status}{run.error ? ` · ${run.error}` : ''}</p>}
      {error && <p className="keyframe-error" role="alert">{error}</p>}
      {unresolved && <><p className="keyframe-help">This request is saved. Changes above apply only to your next image. Refreshing checks its status without submitting again.</p><div className="keyframe-actions">
        <button type="button" disabled={pending} onClick={() => void perform(check)}>Check saved request</button>
        {run?.status === 'unknown' && <button type="button" disabled={pending} onClick={() => void perform(() => submit(savedRef.current.ticket!))}>Resend same request</button>}
        {run?.can_resume && <button type="button" disabled={pending || busy} onClick={() => void perform(async () => { const value = await api(`/asset-runs/${saved.ticket!.request_id}/resume`, {}, undefined, undefined, OPTIONS); save({ ...savedRef.current, run: value }); })}>Resume image</button>}
        {run?.can_cancel && <button type="button" disabled={pending} onClick={() => void perform(async () => { const value = await api(`/asset-runs/${saved.ticket!.request_id}/cancel`, {}, undefined, undefined, OPTIONS); save({ ...savedRef.current, run: value }); })}>Cancel image</button>}
      </div></>}
      {run?.status === 'succeeded' && run.asset && <div className="keyframe-result"><a href={`/api/assets/${run.asset.id}/file`} target="_blank" rel="noopener noreferrer"><img src={`/api/assets/${run.asset.id}/thumbnail`} alt={`Generated keyframe: ${run.asset.name}`}/></a>
        <label>Add image as<select aria-label="Add image as" value={role} onChange={event => setRole(event.target.value as KeyframeRole)}><option value="first_frame">First frame</option><option value="last_frame">Last frame</option><option value="reference_image">Reference image</option></select></label>
        <p className="keyframe-help">Keyframes set the compatible video mode and keep the opposite endpoint when present. Reference image selects Reference photos mode.</p>
        <button type="button" disabled={busy} onClick={() => { try { update(current => addGeneratedKeyframe(current, run.asset!, role)); setError(''); } catch (e) { setError((e as Error).message); } }}>{existing ? 'Apply image role' : 'Add to project'}</button>
        {existing && <small role="status">This image is in your project.</small>}
      </div>}
    </div>
  </details>;
}
